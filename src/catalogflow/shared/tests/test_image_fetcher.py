"""Testes do `shared/image_fetcher.py` — scraping + cache de fotos de produto.

Estratégia:
- HTTP é mockado via `respx` (já é dep de dev) — não tocamos no AMC real.
- Redis é substituído por um cliente em memória via monkeypatch de
  `get_redis_client` e `get_redis_binary_client` no módulo `infra.cache`.
- `_normalize_sku` é puro e testado direto.

Todas as funções públicas são best-effort: erros viram `None`, nunca
levantam. A maioria dos testes valida exatamente essa propriedade.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from catalogflow.shared import image_fetcher as ifx

# ──────────────────────────────────────────────
#  Fake Redis (texto e binário) + monkeypatch
# ──────────────────────────────────────────────


class _FakeRedis:
    """Implementação minimalista compatível com o que o módulo usa: get/set."""

    def __init__(self) -> None:
        self.store: dict[str, Any] = {}

    async def get(self, key: str) -> Any:
        return self.store.get(key)

    async def set(self, key: str, value: Any, ex: int | None = None) -> None:
        # TTL ignorado para fins de teste — basta persistir em memória.
        del ex
        self.store[key] = value


class _BrokenRedis:
    """Simula Redis fora — todas as operações levantam."""

    async def get(self, key: str) -> Any:
        raise RuntimeError("redis down")

    async def set(self, key: str, value: Any, ex: int | None = None) -> None:
        raise RuntimeError("redis down")


@pytest.fixture(autouse=True)
def _patch_redis(monkeypatch: pytest.MonkeyPatch) -> tuple[_FakeRedis, _FakeRedis]:
    """Substitui os singletons de Redis por fakes em memória."""
    text_client = _FakeRedis()
    bytes_client = _FakeRedis()
    monkeypatch.setattr("catalogflow.infra.cache.get_redis_client", lambda: text_client)
    monkeypatch.setattr("catalogflow.infra.cache.get_redis_binary_client", lambda: bytes_client)
    return text_client, bytes_client


# ──────────────────────────────────────────────
#  _normalize_sku — função pura
# ──────────────────────────────────────────────


class TestNormalizeSku:
    def test_strips_suffix_and_leading_zeros(self) -> None:
        """`0142500001-0` → `142500001` (drop suffix + drop zeros à esquerda)."""
        assert ifx._normalize_sku("0142500001-0") == "142500001"

    def test_accepts_sku_without_dash(self) -> None:
        """SKU sem `-` mantém a parte numérica intacta."""
        assert ifx._normalize_sku("0142500001") == "142500001"

    def test_empty_string_returns_none(self) -> None:
        """SKU vazio → None."""
        assert ifx._normalize_sku("") is None

    def test_non_numeric_part_returns_none(self) -> None:
        """SKU com letras antes do `-` é rejeitado."""
        assert ifx._normalize_sku("ABC-0") is None

    def test_dash_only_returns_none(self) -> None:
        """Só `-` (parte numérica vazia) → None."""
        assert ifx._normalize_sku("-0") is None


# ──────────────────────────────────────────────
#  fetch_product_image_url
# ──────────────────────────────────────────────


_HTML_TWO_IMAGES = """
<html><body>
<img class="img-fluid" src="https://cdn/x/foto-detalhe.jpg" />
<img class="img-fluid" src="https://cdn/x/foto-principal.jpg" />
<img class="img-fluid" src="https://cdn/x/textura.jpg" />
</body></html>
"""

_HTML_NO_IMAGES = "<html><body><p>sem imagens</p></body></html>"

_HTML_IMG_NO_SRC = """<html><body>
<img class="img-fluid" />
<img class="img-fluid" />
</body></html>"""


@pytest.mark.asyncio
class TestFetchProductImageUrl:
    async def test_returns_penultimate_img_src(self) -> None:
        """Convenção AMC: a foto-padrão é a penúltima `<img class=img-fluid>`."""
        sku = "0142500001-0"
        with respx.mock(assert_all_called=True) as router:
            router.get("https://qrcode.amctextil.com.br/142500001").mock(
                return_value=httpx.Response(200, text=_HTML_TWO_IMAGES)
            )
            url = await ifx.fetch_product_image_url(sku)
        assert url == "https://cdn/x/foto-principal.jpg"

    async def test_falls_back_to_last_when_only_one_image(self) -> None:
        """Se só houver 1 imagem, devolvemos ela mesma — não erra."""
        html = '<img class="img-fluid" src="https://cdn/x/única.jpg" />'
        with respx.mock() as router:
            router.get("https://qrcode.amctextil.com.br/100").mock(
                return_value=httpx.Response(200, text=html)
            )
            url = await ifx.fetch_product_image_url("100-0")
        assert url == "https://cdn/x/única.jpg"

    async def test_invalid_sku_returns_none_without_http_call(self) -> None:
        """SKU não-numérico nunca chama o AMC."""
        with respx.mock(assert_all_called=False) as router:
            stub = router.get("https://qrcode.amctextil.com.br/")
            url = await ifx.fetch_product_image_url("LIXO")
        assert url is None
        assert not stub.called

    async def test_cached_url_is_returned_without_http(
        self, _patch_redis: tuple[_FakeRedis, _FakeRedis]
    ) -> None:
        """URL no cache do Redis vence — sem chamar AMC."""
        text_client, _ = _patch_redis
        text_client.store["img_url:0001-0"] = "https://cdn/x/cached.jpg"
        with respx.mock(assert_all_called=False) as router:
            stub = router.get("https://qrcode.amctextil.com.br/1")
            url = await ifx.fetch_product_image_url("0001-0")
        assert url == "https://cdn/x/cached.jpg"
        assert not stub.called

    async def test_status_not_200_returns_none(self) -> None:
        """Status != 200 → None (sem tentar parse)."""
        with respx.mock() as router:
            router.get("https://qrcode.amctextil.com.br/999").mock(return_value=httpx.Response(404))
            url = await ifx.fetch_product_image_url("0999-0")
        assert url is None

    async def test_http_error_returns_none(self) -> None:
        """Timeout / connect error → None (fail-soft)."""
        with respx.mock() as router:
            router.get("https://qrcode.amctextil.com.br/1").mock(
                side_effect=httpx.ConnectError("offline")
            )
            url = await ifx.fetch_product_image_url("0001-0")
        assert url is None

    async def test_no_images_in_html_returns_none(self) -> None:
        """Página sem `<img class=img-fluid>` → None."""
        with respx.mock() as router:
            router.get("https://qrcode.amctextil.com.br/2").mock(
                return_value=httpx.Response(200, text=_HTML_NO_IMAGES)
            )
            url = await ifx.fetch_product_image_url("0002-0")
        assert url is None

    async def test_img_without_src_returns_none(self) -> None:
        """Img tag sem atributo `src` → None."""
        with respx.mock() as router:
            router.get("https://qrcode.amctextil.com.br/3").mock(
                return_value=httpx.Response(200, text=_HTML_IMG_NO_SRC)
            )
            url = await ifx.fetch_product_image_url("0003-0")
        assert url is None

    async def test_successful_fetch_caches_url(
        self, _patch_redis: tuple[_FakeRedis, _FakeRedis]
    ) -> None:
        """Após sucesso, a URL fica em `img_url:{sku}` no Redis."""
        text_client, _ = _patch_redis
        with respx.mock() as router:
            router.get("https://qrcode.amctextil.com.br/142500001").mock(
                return_value=httpx.Response(200, text=_HTML_TWO_IMAGES)
            )
            await ifx.fetch_product_image_url("0142500001-0")
        assert text_client.store["img_url:0142500001-0"] == "https://cdn/x/foto-principal.jpg"


# ──────────────────────────────────────────────
#  fetch_product_image_bytes
# ──────────────────────────────────────────────


@pytest.mark.asyncio
class TestFetchProductImageBytes:
    async def test_cached_bytes_returned_without_http(
        self, _patch_redis: tuple[_FakeRedis, _FakeRedis]
    ) -> None:
        """Cache de bytes vence — não chama AMC nem CDN."""
        _, bin_client = _patch_redis
        bin_client.store["img_bytes:0001-0"] = b"cached-bytes"
        with respx.mock(assert_all_called=False) as router:
            stub = router.get("https://qrcode.amctextil.com.br/1")
            data = await ifx.fetch_product_image_bytes("0001-0")
        assert data == b"cached-bytes"
        assert not stub.called

    async def test_url_resolution_failure_returns_none(self) -> None:
        """URL não resolvida (AMC offline) → bytes None."""
        with respx.mock() as router:
            router.get("https://qrcode.amctextil.com.br/1").mock(return_value=httpx.Response(404))
            data = await ifx.fetch_product_image_bytes("0001-0")
        assert data is None

    async def test_successful_download_returns_and_caches_bytes(
        self, _patch_redis: tuple[_FakeRedis, _FakeRedis]
    ) -> None:
        """Sucesso: bytes da CDN + cache em img_bytes:{sku}."""
        _, bin_client = _patch_redis
        with respx.mock() as router:
            router.get("https://qrcode.amctextil.com.br/142500001").mock(
                return_value=httpx.Response(200, text=_HTML_TWO_IMAGES)
            )
            router.get("https://cdn/x/foto-principal.jpg").mock(
                return_value=httpx.Response(200, content=b"\xff\xd8\xff JPEG")
            )
            data = await ifx.fetch_product_image_bytes("0142500001-0")
        assert data == b"\xff\xd8\xff JPEG"
        assert bin_client.store["img_bytes:0142500001-0"] == b"\xff\xd8\xff JPEG"

    async def test_cdn_status_not_200_returns_none(self) -> None:
        """CDN devolve 500 no download → None (fail-soft)."""
        with respx.mock() as router:
            router.get("https://qrcode.amctextil.com.br/142500001").mock(
                return_value=httpx.Response(200, text=_HTML_TWO_IMAGES)
            )
            router.get("https://cdn/x/foto-principal.jpg").mock(return_value=httpx.Response(500))
            data = await ifx.fetch_product_image_bytes("0142500001-0")
        assert data is None

    async def test_cdn_http_error_returns_none(self) -> None:
        """ConnectError no download → None."""
        with respx.mock() as router:
            router.get("https://qrcode.amctextil.com.br/142500001").mock(
                return_value=httpx.Response(200, text=_HTML_TWO_IMAGES)
            )
            router.get("https://cdn/x/foto-principal.jpg").mock(
                side_effect=httpx.ConnectError("offline")
            )
            data = await ifx.fetch_product_image_bytes("0142500001-0")
        assert data is None


# ──────────────────────────────────────────────
#  fetch_product_images — paralelismo + dedup
# ──────────────────────────────────────────────


@pytest.mark.asyncio
class TestFetchProductImagesBatch:
    async def test_empty_list_returns_empty_dict(self) -> None:
        """Lista vazia → dict vazio (sem HTTP)."""
        assert await ifx.fetch_product_images([]) == {}

    async def test_deduplicates_skus_before_fetching(self) -> None:
        """SKUs duplicados viram 1 request — confirmado por respx call_count."""
        with respx.mock() as router:
            page = router.get("https://qrcode.amctextil.com.br/142500001").mock(
                return_value=httpx.Response(200, text=_HTML_TWO_IMAGES)
            )
            cdn = router.get("https://cdn/x/foto-principal.jpg").mock(
                return_value=httpx.Response(200, content=b"JPG")
            )
            result = await ifx.fetch_product_images(
                ["0142500001-0", "0142500001-0", "0142500001-0"]
            )
        assert result == {"0142500001-0": b"JPG"}
        assert page.call_count == 1
        assert cdn.call_count == 1

    async def test_missing_skus_are_omitted_from_result(self) -> None:
        """SKU sem foto (404) é omitido — não devolve `None` explícito."""
        with respx.mock() as router:
            router.get("https://qrcode.amctextil.com.br/100").mock(
                return_value=httpx.Response(200, text=_HTML_TWO_IMAGES)
            )
            router.get("https://cdn/x/foto-principal.jpg").mock(
                return_value=httpx.Response(200, content=b"OK")
            )
            router.get("https://qrcode.amctextil.com.br/200").mock(return_value=httpx.Response(404))
            result = await ifx.fetch_product_images(["100-0", "200-0"])
        assert result == {"100-0": b"OK"}
        assert "200-0" not in result


# ──────────────────────────────────────────────
#  Cache helpers — fail-soft em Redis fora
# ──────────────────────────────────────────────


@pytest.mark.asyncio
class TestCacheFailSoft:
    async def test_cache_get_url_returns_none_when_redis_broken(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`_cache_get_url`: Redis down → retorna None sem levantar."""
        monkeypatch.setattr("catalogflow.infra.cache.get_redis_client", lambda: _BrokenRedis())
        assert await ifx._cache_get_url("qualquer") is None

    async def test_cache_set_url_swallows_redis_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`_cache_set_url`: erro de SET é engolido (fail-soft)."""
        monkeypatch.setattr("catalogflow.infra.cache.get_redis_client", lambda: _BrokenRedis())
        # Sem assertion — basta não levantar.
        await ifx._cache_set_url("qualquer", "https://x")

    async def test_cache_get_image_bytes_returns_none_on_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`cache_get_image_bytes` é fail-soft."""
        monkeypatch.setattr(
            "catalogflow.infra.cache.get_redis_binary_client", lambda: _BrokenRedis()
        )
        assert await ifx.cache_get_image_bytes("sku") is None

    async def test_cache_set_image_bytes_swallows_redis_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`cache_set_image_bytes` engole erro de Redis."""
        monkeypatch.setattr(
            "catalogflow.infra.cache.get_redis_binary_client", lambda: _BrokenRedis()
        )
        await ifx.cache_set_image_bytes("sku", b"\x00")

    async def test_cache_set_image_bytes_persists_on_happy_path(
        self, _patch_redis: tuple[_FakeRedis, _FakeRedis]
    ) -> None:
        """Happy-path: bytes ficam armazenados em img_bytes:{sku}."""
        _, bin_client = _patch_redis
        await ifx.cache_set_image_bytes("xyz", b"jpeg-bytes")
        assert bin_client.store["img_bytes:xyz"] == b"jpeg-bytes"
