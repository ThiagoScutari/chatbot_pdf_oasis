"""Middlewares HTTP.

`RequestIdMiddleware` é a fonte de verdade do `request_id` que aparece em
todo envelope JSON (`meta.request_id`) e em logs estruturados.

Implementação pure ASGI (não `BaseHTTPMiddleware`): há um problema conhecido
do `BaseHTTPMiddleware` da Starlette que faz exceptions internas escaparem
do `wrap_app_handling_exceptions` do FastAPI, deixando handlers globais
inertes. Pure ASGI evita o problema.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import uuid4

from starlette.datastructures import MutableHeaders
from starlette.requests import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

REQUEST_ID_HEADER = "X-Request-ID"
_HEADER_BYTES = REQUEST_ID_HEADER.encode("latin-1")
_HEADER_LOWER = REQUEST_ID_HEADER.lower().encode("latin-1")


class RequestIdMiddleware:
    """Lê `X-Request-ID` do cliente ou gera UUID4.

    Disponibiliza o valor em `request.state.request_id` para handlers e
    exception handlers, e ecoa no header da resposta para correlação ponta a
    ponta com cliente/proxy.
    """

    def __init__(self, app: ASGIApp, *, header_name: str = REQUEST_ID_HEADER) -> None:
        self.app = app
        self.header_name = header_name

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        # Lê header de entrada (se vier).
        incoming: str | None = None
        for name, value in scope.get("headers", []):
            if name.lower() == _HEADER_LOWER:
                incoming = value.decode("latin-1")
                break
        request_id = incoming if incoming else str(uuid4())

        # Anexa em scope["state"] — é o que `request.state.<x>` lê.
        state = scope.setdefault("state", {})
        state["request_id"] = request_id

        # Wrap do `send` para inserir/sobrescrever o header na resposta.
        async def send_with_header(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(raw=message.get("headers", []))
                headers[self.header_name] = request_id
                message["headers"] = headers.raw
            await send(message)

        await self.app(scope, receive, send_with_header)


def get_request_id(request: Request) -> str:
    """Retorna o request_id corrente.

    Se o middleware ainda não rodou (ex: erro muito cedo no pipeline), gera
    um valor temporário para o handler poder responder.
    """
    return getattr(request.state, "request_id", str(uuid4()))


# `Callable` import só para forward refs em type hints externos.
_ = Callable, Awaitable
