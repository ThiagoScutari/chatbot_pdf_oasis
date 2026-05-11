"""Middlewares HTTP.

`RequestIdMiddleware` é a fonte de verdade do `request_id` que aparece em
todo envelope JSON (`meta.request_id`) e em logs estruturados.
"""

from __future__ import annotations

from uuid import uuid4

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

REQUEST_ID_HEADER = "X-Request-ID"


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Lê `X-Request-ID` do cliente ou gera UUID4.

    Disponibiliza o valor em `request.state.request_id` para handlers e
    exception handlers, e ecoa no header da resposta para correlação ponta a
    ponta com cliente/proxy.
    """

    def __init__(self, app: ASGIApp, *, header_name: str = REQUEST_ID_HEADER) -> None:
        super().__init__(app)
        self.header_name = header_name

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        incoming = request.headers.get(self.header_name)
        request_id = incoming if incoming else str(uuid4())
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers[self.header_name] = request_id
        return response


def get_request_id(request: Request) -> str:
    """Retorna o request_id corrente.

    Se o middleware ainda não rodou (ex: erro muito cedo no pipeline), gera
    um valor temporário para o handler poder responder.
    """
    return getattr(request.state, "request_id", str(uuid4()))
