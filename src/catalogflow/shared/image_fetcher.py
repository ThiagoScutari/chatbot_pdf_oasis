"""Resolução e download de fotos de produtos via AMC QRCode.

A Oasis Resortwear hospeda fotos dos produtos em
`qrcode.amctextil.com.br/{codigo}` — páginas HTML com várias fotos.
Convenção descoberta empiricamente: a foto-padrão do catálogo é a
**penúltima** `<img class="img-fluid">` da página (a última costuma
ser foto de detalhe / textura).

Este módulo vive em `shared/` porque é consumido tanto pelo web layer
(thumbnails na UI) quanto pelo backend (fotos embedadas nos PDFs de
romaneio e relatório de pendências — Sprint 04).

Regras (todas as funções):
- **Best-effort**: qualquer falha (timeout, parse, formato de SKU
  inválido, status code != 200) retorna `None` em vez de levantar.
  Foto é melhoria visual, não pode bloquear nem derrubar.
- Timeouts curtos (~3s) — o usuário não pode esperar pelo AMC.
- O SKU do catálogo vem no formato `0142500001-0`; o AMC só conhece
  o código numérico canônico (sem zeros à esquerda, sem sufixo).
"""

from __future__ import annotations

import asyncio
import logging

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

_BASE_URL = "https://qrcode.amctextil.com.br"
_DEFAULT_TIMEOUT_SECONDS = 3.0


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


async def fetch_product_image_url(
    sku: str,
    *,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> str | None:
    """Retorna a URL da foto de catálogo do produto, ou `None`.

    Nunca levanta. Qualquer falha de rede, parse ou ausência da imagem
    cai silenciosamente no `None`.
    """
    codigo = _normalize_sku(sku)
    if codigo is None:
        return None

    page_url = f"{_BASE_URL}/{codigo}"

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(page_url)
    except httpx.HTTPError as exc:
        logger.debug("product-image: HTTPError em %s — %s", page_url, exc)
        return None
    except Exception:  # pragma: no cover - defesa final
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

    if not images:
        return None
    target = images[-2] if len(images) >= 2 else images[-1]
    src = target.get("src")
    if not isinstance(src, str) or not src:
        return None
    return src


async def fetch_product_image_bytes(
    sku: str,
    *,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> bytes | None:
    """Resolve a URL via `fetch_product_image_url` e baixa os bytes.

    Combina os dois passos (HTML parse + GET da imagem) num só helper —
    usado para embedar a foto em PDFs (romaneio, pendências). Nunca
    levanta — falhas viram `None`.
    """
    url = await fetch_product_image_url(sku, timeout=timeout)
    if url is None:
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url)
    except httpx.HTTPError as exc:
        logger.debug("product-image-bytes: HTTPError em %s — %s", url, exc)
        return None
    except Exception:  # pragma: no cover
        logger.exception("product-image-bytes: exceção inesperada em %s", url)
        return None
    if resp.status_code != 200:
        return None
    return resp.content


async def fetch_product_images(
    skus: list[str],
    *,
    max_concurrent: int = 5,
    timeout: float = _DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, bytes]:
    """Busca fotos de múltiplos produtos em paralelo.

    Retorna `dict[sku, image_bytes]`. SKUs sem foto (ou que falharam)
    são **omitidos** do dict — sem entrada com None para não confundir
    o caller (`sku in product_images` indica disponibilidade).

    Concorrência limitada por semáforo (`max_concurrent=5` default) para
    não bombardear o AMC com requests paralelos quando o pedido tem
    muitos SKUs distintos.

    SKUs duplicados na entrada são deduplicados — uma única request
    por SKU mesmo que o pedido tenha o mesmo produto várias vezes.
    """
    unique_skus = list(dict.fromkeys(skus))  # dedup preservando ordem
    if not unique_skus:
        return {}

    semaphore = asyncio.Semaphore(max_concurrent)

    async def bounded(sku: str) -> tuple[str, bytes | None]:
        async with semaphore:
            return sku, await fetch_product_image_bytes(sku, timeout=timeout)

    results = await asyncio.gather(*(bounded(s) for s in unique_skus))
    return {sku: img for sku, img in results if img is not None}
