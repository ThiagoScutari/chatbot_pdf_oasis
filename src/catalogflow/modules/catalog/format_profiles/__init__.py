"""Loader e validador de `BrandFormatProfile` (ADR-010, Sprint 08 Fase A).

Profiles ficam versionados em código (arquivos JSON em
`format_profiles/<id>.json`) e são validados contra `schema.json`
(JSONSchema Draft 2020-12). Cada profile é carregado on-demand e
cacheado por id durante o lifetime do processo — profiles são imutáveis
em runtime; mudanças exigem deploy.

A Fase A entrega apenas o loader, o schema e a dataclass. Os JSONs
`oasis_default.json` e `ferla_like.json` chegam na Fase B e na Fase D.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Final

from jsonschema import Draft202012Validator

from catalogflow.shared.errors import (
    BrandFormatProfileInvalidError,
    BrandFormatProfileNotFoundError,
)

PROFILES_DIR: Final[Path] = Path(__file__).parent
SCHEMA_PATH: Final[Path] = PROFILES_DIR / "schema.json"


@dataclass(frozen=True, slots=True)
class BrandFormatProfile:
    """Profile validado, pronto para consumo pelo orquestrador.

    `strategies` é um mapping `eixo → {id, params}` já checado contra o
    schema; consumidores podem confiar nas chaves obrigatórias (`sku`,
    `grade`, `price`, `swatches`, `name`) e na presença de `id` em
    cada strategy_ref.
    """

    id: str
    name: str
    version: str
    strategies: Mapping[str, Mapping[str, Any]]


@lru_cache(maxsize=1)
def _load_schema() -> dict[str, Any]:
    """Lê e cacheia o JSONSchema do diretório do pacote."""
    with SCHEMA_PATH.open(encoding="utf-8") as f:
        data: dict[str, Any] = json.load(f)
    return data


@lru_cache(maxsize=64)
def load_profile(profile_id: str) -> BrandFormatProfile:
    """Carrega e valida um profile do diretório `format_profiles/`.

    O resultado é cacheado por `profile_id` (LRU); use
    `load_profile.cache_clear()` em testes para evitar contaminação.

    Levanta:
        BrandFormatProfileNotFoundError — `<profile_id>.json` ausente
            do diretório.
        BrandFormatProfileInvalidError — JSON malformado ou não
            conforme ao schema. O `details` da exceção carrega `path`
            (lista do `absolute_path` do erro) e `message` do validator,
            para diagnóstico operacional.
    """
    path = PROFILES_DIR / f"{profile_id}.json"
    if not path.is_file():
        raise BrandFormatProfileNotFoundError(
            f"format profile not found: {profile_id!r}",
            code="BRAND_FORMAT_PROFILE_NOT_FOUND",
            details={"profile_id": profile_id, "path": str(path)},
        )

    try:
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as exc:
        raise BrandFormatProfileInvalidError(
            f"profile {profile_id!r} contains invalid JSON",
            code="BRAND_FORMAT_PROFILE_INVALID_JSON",
            details={"profile_id": profile_id, "error": str(exc)},
        ) from exc

    validator = Draft202012Validator(_load_schema())
    errors = sorted(validator.iter_errors(data), key=lambda e: list(e.absolute_path))
    if errors:
        first = errors[0]
        raise BrandFormatProfileInvalidError(
            f"profile {profile_id!r} does not conform to schema",
            code="BRAND_FORMAT_PROFILE_INVALID_SCHEMA",
            details={
                "profile_id": profile_id,
                "path": list(first.absolute_path),
                "message": first.message,
            },
        )

    return BrandFormatProfile(
        id=data["id"],
        name=data["name"],
        version=data["version"],
        strategies=data["strategies"],
    )
