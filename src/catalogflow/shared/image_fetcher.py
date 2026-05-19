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

# Cache no Redis — chaves com prefixo (mesmo DB do resto da app, sem colisão).
# TTLs por nível:
#   - URL (scraping da página HTML do AMC): 7d. O CDN do AMC mantém URLs
#     estáveis; vale só re-validar quando o cache "amadurece".
#   - Bytes da imagem em si: 1d. O conteúdo pode ser regerado pelo CDN, mas
#     2 níveis evitam o segundo round-trip (download) na maioria dos hits.
_CACHE_KEY_URL_PREFIX = "img_url:"
_CACHE_KEY_BYTES_PREFIX = "img_bytes:"
_URL_CACHE_TTL_SECONDS = 7 * 24 * 3600
_BYTES_CACHE_TTL_SECONDS = 24 * 3600


async def _cache_get_url(sku: str) -> str | None:
    """Lê `img_url:{sku}` do Redis. Fail-soft — exceção vira `None`."""
    try:
        from catalogflow.infra.cache import get_redis_client

        client = get_redis_client()
        value = await client.get(f"{_CACHE_KEY_URL_PREFIX}{sku}")
        return value if value else None
    except Exception:
        logger.debug("img-cache: GET url falhou para sku=%s", sku, exc_info=False)
        return None


async def _cache_set_url(sku: str, url: str) -> None:
    """Salva `img_url:{sku}` com TTL de 7 dias. Fail-soft."""
    try:
        from catalogflow.infra.cache import get_redis_client

        client = get_redis_client()
        await client.set(
            f"{_CACHE_KEY_URL_PREFIX}{sku}",
            url,
            ex=_URL_CACHE_TTL_SECONDS,
        )
    except Exception:
        logger.debug("img-cache: SET url falhou para sku=%s", sku, exc_info=False)


async def cache_get_image_bytes(sku: str) -> bytes | None:
    """Lê `img_bytes:{sku}` do Redis (binário). Fail-soft. Público — o web
    handler usa diretamente para devolver Response sem passar pelo helper
    do bytes (que descarta content-type)."""
    try:
        from catalogflow.infra.cache import get_redis_binary_client

        client = get_redis_binary_client()
        value = await client.get(f"{_CACHE_KEY_BYTES_PREFIX}{sku}")
        return value if value else None
    except Exception:
        logger.debug("img-cache: GET bytes falhou para sku=%s", sku, exc_info=False)
        return None


async def cache_set_image_bytes(sku: str, data: bytes) -> None:
    """Salva `img_bytes:{sku}` com TTL de 24h. Fail-soft."""
    try:
        from catalogflow.infra.cache import get_redis_binary_client

        client = get_redis_binary_client()
        await client.set(
            f"{_CACHE_KEY_BYTES_PREFIX}{sku}",
            data,
            ex=_BYTES_CACHE_TTL_SECONDS,
        )
    except Exception:
        logger.debug("img-cache: SET bytes falhou para sku=%s", sku, exc_info=False)


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
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> str | None:
    """Retorna a URL da foto de catálogo do produto, ou `None`.

    Cache: `img_url:{sku}` no Redis (TTL 7d). Lookup é fail-soft — se o
    Redis estiver fora, o caminho segue para o scraping. Nunca levanta.
    """
    codigo = _normalize_sku(sku)
    if codigo is None:
        return None

    cached_url = await _cache_get_url(sku)
    if cached_url is not None:
        return cached_url

    page_url = f"{_BASE_URL}/{codigo}"

    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
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

    await _cache_set_url(sku, src)
    return src


async def fetch_product_image_bytes(
    sku: str,
    *,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> bytes | None:
    """Resolve a URL via `fetch_product_image_url` e baixa os bytes.

    Cache: `img_bytes:{sku}` no Redis (TTL 24h). Combina HTML parse +
    GET da imagem num helper — usado para embedar foto em PDFs (romaneio,
    pendências). Nunca levanta — falhas viram `None`.
    """
    cached_bytes = await cache_get_image_bytes(sku)
    if cached_bytes is not None:
        return cached_bytes

    url = await fetch_product_image_url(sku, timeout_seconds=timeout_seconds)
    if url is None:
        return None
    try:
        async with httpx.AsyncClient(timeout=timeout_seconds) as client:
            resp = await client.get(url)
    except httpx.HTTPError as exc:
        logger.debug("product-image-bytes: HTTPError em %s — %s", url, exc)
        return None
    except Exception:  # pragma: no cover
        logger.exception("product-image-bytes: exceção inesperada em %s", url)
        return None
    if resp.status_code != 200:
        return None

    await cache_set_image_bytes(sku, resp.content)
    return resp.content


async def fetch_product_images(
    skus: list[str],
    *,
    max_concurrent: int = 5,
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
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
            return sku, await fetch_product_image_bytes(sku, timeout_seconds=timeout_seconds)

    results = await asyncio.gather(*(bounded(s) for s in unique_skus))
    return {sku: img for sku, img in results if img is not None}
