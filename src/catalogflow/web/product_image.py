"""Resolução de thumbnails de produto via AMC QRCode.

A Oasis Resortwear hospeda fotos dos produtos em
`qrcode.amctextil.com.br/{codigo}` — páginas HTML com várias
fotos do produto. Convenção descoberta empiricamente: a foto que
melhor representa o catálogo é a **penúltima** `<img class="img-fluid">`
da página (a última costuma ser uma foto de detalhe / textura).

Regras:
- A função é **best-effort**: qualquer falha (timeout, parse, formato
  de SKU, status code != 200) retorna `None` em vez de levantar.
- Timeout HTTP de 3s — é uma melhoria visual, não pode bloquear a UI.
- O SKU do catálogo vem no formato `0142500001-0`; o AMC só conhece
  o código numérico canônico (sem zeros à esquerda e sem o sufixo).
"""

from __future__ import annotations

import logging

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_BASE_URL = "https://qrcode.amctextil.com.br"
_TIMEOUT_SECONDS = 3.0


def _normalize_sku(sku: str) -> str | None:
    """Converte `0142500001-0` no código canônico do AMC (`142500001`).

    Aceita também SKUs sem `-`. Retorna `None` se a parte principal não
    for inteiramente numérica — não queremos chamar o AMC com lixo.
    """
    if not sku:
        return None
    base = sku.split("-", 1)[0].strip()
    if not base or not base.isdigit():
        return None
    return str(int(base))


async def fetch_product_image_url(sku: str) -> str | None:
    """Retorna a URL da foto de catálogo do produto, ou `None`.

    Nunca levanta. Qualquer falha de rede, parse ou ausência da imagem
    cai silenciosamente no `None`, deixando a rota servir o placeholder.
    """
    codigo = _normalize_sku(sku)
    if codigo is None:
        return None

    page_url = f"{_BASE_URL}/{codigo}"

    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
            resp = await client.get(page_url)
    except httpx.HTTPError as exc:
        logger.debug("product-image: HTTPError em %s — %s", page_url, exc)
        return None
    except Exception:  # pragma: no cover - defesa final, não pode quebrar a UI
        logger.exception("product-image: exceção inesperada em %s", page_url)
        return None

    if resp.status_code != 200:
        return None

    try:
        soup = BeautifulSoup(resp.text, "html.parser")
        images = soup.find_all("img", class_="img-fluid")
    except Exception:  # pragma: no cover
        logger.exception("product-image: parse falhou em %s", page_url)
        return None

    # A foto-padrão do catálogo é a penúltima da página (descoberta empírica).
    # Se houver só 1, ela mesma serve; se nenhuma, devolvemos None.
    if not images:
        return None
    target = images[-2] if len(images) >= 2 else images[-1]
    src = target.get("src")
    if not isinstance(src, str) or not src:
        return None
    return src
