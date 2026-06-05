"""Testes do loader e validador de BrandFormatProfile (ADR-010, Sprint 08 Fase A).

A Fase A entrega:
- `BrandFormatProfile` dataclass frozen + slots.
- `load_profile(profile_id)` com `lru_cache` por id.
- Validação contra JSONSchema Draft 2020-12 (`schema.json`).

Estes testes isolam o diretório de profiles via `tmp_path` + monkeypatch,
de modo a não depender de arquivos versionados (que só chegam na Fase B
e na Fase D do PRD).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from catalogflow.modules.catalog import format_profiles as fp_module
from catalogflow.modules.catalog.format_profiles import (
    BrandFormatProfile,
    load_profile,
)
from catalogflow.shared.errors import (
    BrandFormatProfileInvalidError,
    BrandFormatProfileNotFoundError,
)

# ──────────────────────────────────────────────
#  Fixtures
# ──────────────────────────────────────────────


@pytest.fixture
def isolated_profiles_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Aponta `PROFILES_DIR` para `tmp_path` e limpa o cache do loader.

    O `SCHEMA_PATH` é módulo-level e segue apontando para o `schema.json`
    real do pacote — apenas a localização dos profiles JSON é redirecionada.
    """
    monkeypatch.setattr(fp_module, "PROFILES_DIR", tmp_path)
    load_profile.cache_clear()
    yield tmp_path
    load_profile.cache_clear()


def _valid_profile_payload(profile_id: str = "foo") -> dict[str, Any]:
    return {
        "id": profile_id,
        "name": "Profile de teste",
        "version": "1.0.0",
        "strategies": {
            "sku": {"id": "regex_hyphenated", "params": {}},
            "grade": {"id": "alpha_range", "params": {}},
            "price": {"id": "br_currency", "params": {}},
            "swatches": {"id": "geometric_bottom", "params": {}},
            "name": {"id": "positional_title", "params": {}},
        },
    }


def _write_profile(profiles_dir: Path, profile_id: str, payload: Any) -> Path:
    path = profiles_dir / f"{profile_id}.json"
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# ──────────────────────────────────────────────
#  Caminho feliz
# ──────────────────────────────────────────────


def test_load_profile_with_valid_minimal_json(isolated_profiles_dir: Path) -> None:
    _write_profile(isolated_profiles_dir, "foo", _valid_profile_payload("foo"))

    profile = load_profile("foo")

    assert isinstance(profile, BrandFormatProfile)
    assert profile.id == "foo"
    assert profile.name == "Profile de teste"
    assert profile.version == "1.0.0"
    assert profile.strategies["sku"]["id"] == "regex_hyphenated"


# ──────────────────────────────────────────────
#  Caminhos de erro
# ──────────────────────────────────────────────


def test_load_profile_with_missing_file_raises_not_found(
    isolated_profiles_dir: Path,
) -> None:
    with pytest.raises(BrandFormatProfileNotFoundError) as exc_info:
        load_profile("foo")

    err = exc_info.value
    assert err.code == "BRAND_FORMAT_PROFILE_NOT_FOUND"
    assert err.details["profile_id"] == "foo"


def test_load_profile_with_invalid_json_syntax_raises_invalid(
    isolated_profiles_dir: Path,
) -> None:
    _write_profile(isolated_profiles_dir, "foo", '{ "id":')

    with pytest.raises(BrandFormatProfileInvalidError) as exc_info:
        load_profile("foo")

    assert exc_info.value.code == "BRAND_FORMAT_PROFILE_INVALID_JSON"


def test_load_profile_with_missing_required_field_raises_invalid(
    isolated_profiles_dir: Path,
) -> None:
    payload = _valid_profile_payload("foo")
    del payload["version"]
    _write_profile(isolated_profiles_dir, "foo", payload)

    with pytest.raises(BrandFormatProfileInvalidError) as exc_info:
        load_profile("foo")

    assert exc_info.value.code == "BRAND_FORMAT_PROFILE_INVALID_SCHEMA"
    assert "version" in exc_info.value.details["message"].lower()


def test_load_profile_with_unknown_top_level_property_raises_invalid(
    isolated_profiles_dir: Path,
) -> None:
    payload = _valid_profile_payload("foo")
    payload["extra"] = "x"
    _write_profile(isolated_profiles_dir, "foo", payload)

    with pytest.raises(BrandFormatProfileInvalidError) as exc_info:
        load_profile("foo")

    assert exc_info.value.code == "BRAND_FORMAT_PROFILE_INVALID_SCHEMA"
    assert "extra" in exc_info.value.details["message"]


def test_load_profile_with_invalid_version_format_raises_invalid(
    isolated_profiles_dir: Path,
) -> None:
    payload = _valid_profile_payload("foo")
    payload["version"] = "1.0"
    _write_profile(isolated_profiles_dir, "foo", payload)

    with pytest.raises(BrandFormatProfileInvalidError) as exc_info:
        load_profile("foo")

    assert exc_info.value.code == "BRAND_FORMAT_PROFILE_INVALID_SCHEMA"
    assert exc_info.value.details["path"] == ["version"]


def test_load_profile_with_invalid_id_chars_raises_invalid(
    isolated_profiles_dir: Path,
) -> None:
    payload = _valid_profile_payload("foo")
    payload["id"] = "Has-Hyphen"
    _write_profile(isolated_profiles_dir, "foo", payload)

    with pytest.raises(BrandFormatProfileInvalidError) as exc_info:
        load_profile("foo")

    assert exc_info.value.code == "BRAND_FORMAT_PROFILE_INVALID_SCHEMA"
    assert exc_info.value.details["path"] == ["id"]


def test_load_profile_missing_strategies_axis_raises_invalid(
    isolated_profiles_dir: Path,
) -> None:
    payload = _valid_profile_payload("foo")
    del payload["strategies"]["swatches"]
    _write_profile(isolated_profiles_dir, "foo", payload)

    with pytest.raises(BrandFormatProfileInvalidError) as exc_info:
        load_profile("foo")

    assert exc_info.value.code == "BRAND_FORMAT_PROFILE_INVALID_SCHEMA"
    assert "swatches" in exc_info.value.details["message"].lower()


def test_load_profile_with_strategy_ref_missing_id_raises_invalid(
    isolated_profiles_dir: Path,
) -> None:
    payload = _valid_profile_payload("foo")
    payload["strategies"]["sku"] = {"params": {}}
    _write_profile(isolated_profiles_dir, "foo", payload)

    with pytest.raises(BrandFormatProfileInvalidError) as exc_info:
        load_profile("foo")

    assert exc_info.value.code == "BRAND_FORMAT_PROFILE_INVALID_SCHEMA"


# ──────────────────────────────────────────────
#  Cache
# ──────────────────────────────────────────────


def test_load_profile_caches_result(isolated_profiles_dir: Path) -> None:
    _write_profile(isolated_profiles_dir, "foo", _valid_profile_payload("foo"))
    first = load_profile("foo")
    modified = _valid_profile_payload("foo")
    modified["name"] = "Outro nome"
    _write_profile(isolated_profiles_dir, "foo", modified)

    second = load_profile("foo")

    assert first is second
    assert second.name == "Profile de teste"


# ──────────────────────────────────────────────
#  Meta-validação do schema
# ──────────────────────────────────────────────


def test_schema_itself_is_valid_against_draft_2020_12() -> None:
    schema_path = Path(fp_module.__file__).parent / "schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))

    Draft202012Validator.check_schema(schema)


# ──────────────────────────────────────────────
#  Profiles versionados em código (Fase B/D)
# ──────────────────────────────────────────────


def test_prefixed_dual_price_profile_loads() -> None:
    """O `prefixed_dual_price.json` real carrega e valida contra o schema."""
    load_profile.cache_clear()
    profile = load_profile("prefixed_dual_price")

    assert isinstance(profile, BrandFormatProfile)
    assert profile.id == "prefixed_dual_price"
    assert profile.version == "1.0.0"


def test_prefixed_dual_price_selects_expected_strategies() -> None:
    """O profile aponta para as estratégias do formato prefixado esperadas."""
    load_profile.cache_clear()
    profile = load_profile("prefixed_dual_price")

    assert profile.strategies["sku"]["id"] == "regex_prefixed"
    assert profile.strategies["grade"]["id"] == "alpha_range"
    assert profile.strategies["grade"]["params"] == {"tolerate_spaces": True}
    assert profile.strategies["price"]["id"] == "labeled_dual"
    assert profile.strategies["swatches"]["id"] == "geometric_bottom"
    assert profile.strategies["name"]["id"] == "positional_title"


def test_oasis_default_profile_loads() -> None:
    """Regressão: o `oasis_default.json` real continua válido."""
    load_profile.cache_clear()
    profile = load_profile("oasis_default")

    assert profile.id == "oasis_default"
    assert profile.strategies["name"]["id"] == "category_vocabulary"


# ──────────────────────────────────────────────
#  Hardening anti-path-traversal (Fase E)
# ──────────────────────────────────────────────


def test_load_profile_rejects_path_traversal() -> None:
    load_profile.cache_clear()
    with pytest.raises(BrandFormatProfileInvalidError) as exc_info:
        load_profile("../../etc/passwd")

    assert exc_info.value.code == "BRAND_FORMAT_PROFILE_INVALID"


def test_load_profile_rejects_absolute_path() -> None:
    load_profile.cache_clear()
    with pytest.raises(BrandFormatProfileInvalidError):
        load_profile("/etc/passwd")


def test_load_profile_rejects_uppercase_and_special_chars() -> None:
    load_profile.cache_clear()
    for bad in ("Oasis", "has-hyphen", "tem espaco", "dot.name", "back\\slash"):
        with pytest.raises(BrandFormatProfileInvalidError):
            load_profile(bad)


def test_load_profile_rejects_empty_string() -> None:
    load_profile.cache_clear()
    with pytest.raises(BrandFormatProfileInvalidError):
        load_profile("")


def test_load_profile_accepts_valid_ids() -> None:
    """Os profiles reais (ids válidos) carregam sem erro de validação."""
    load_profile.cache_clear()
    for valid_id in ("oasis_default", "prefixed_dual_price"):
        profile = load_profile(valid_id)
        assert profile.id == valid_id
