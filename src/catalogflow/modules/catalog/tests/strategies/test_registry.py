"""Testes dos registries por eixo.

Cada eixo (SKU, grade, preço, swatches, nome) tem um registry independente
com mesmo padrão de API: `register_*_strategy(name, cls)` e
`get_*_strategy(name)`. Os testes são parametrizados sobre os 5 eixos.
"""

from __future__ import annotations

import importlib
from collections.abc import Iterator
from typing import Any

import pytest

from catalogflow.modules.catalog.strategies.base import (
    GradeStrategy,
    SkuStrategy,
)

REGISTRIES = [
    pytest.param(
        "catalogflow.modules.catalog.strategies.sku",
        "SkuStrategy",
        "SKU_STRATEGIES",
        "register_sku_strategy",
        "get_sku_strategy",
        id="sku",
    ),
    pytest.param(
        "catalogflow.modules.catalog.strategies.grade",
        "GradeStrategy",
        "GRADE_STRATEGIES",
        "register_grade_strategy",
        "get_grade_strategy",
        id="grade",
    ),
    pytest.param(
        "catalogflow.modules.catalog.strategies.price",
        "PriceStrategy",
        "PRICE_STRATEGIES",
        "register_price_strategy",
        "get_price_strategy",
        id="price",
    ),
    pytest.param(
        "catalogflow.modules.catalog.strategies.swatches",
        "SwatchesStrategy",
        "SWATCHES_STRATEGIES",
        "register_swatches_strategy",
        "get_swatches_strategy",
        id="swatches",
    ),
    pytest.param(
        "catalogflow.modules.catalog.strategies.name",
        "NameStrategy",
        "NAME_STRATEGIES",
        "register_name_strategy",
        "get_name_strategy",
        id="name",
    ),
]


# ──────────────────────────────────────────────
#  Limpeza obrigatória entre testes para não vazar estado
# ──────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_all_registries() -> Iterator[None]:
    """Isola cada teste do estado real dos registries.

    Salva snapshot do estado pré-teste (que pode estar populado pelas
    estratégias auto-registradas na Fase B), esvazia, executa o teste e
    restaura. Sem isso, um teste de registry deixaria as estratégias
    reais desregistradas e quebraria a regressão no resto da suite.
    """
    from catalogflow.modules.catalog.strategies.grade import GRADE_STRATEGIES
    from catalogflow.modules.catalog.strategies.name import NAME_STRATEGIES
    from catalogflow.modules.catalog.strategies.price import PRICE_STRATEGIES
    from catalogflow.modules.catalog.strategies.sku import SKU_STRATEGIES
    from catalogflow.modules.catalog.strategies.swatches import SWATCHES_STRATEGIES

    all_regs: tuple[dict[str, Any], ...] = (
        SKU_STRATEGIES,
        GRADE_STRATEGIES,
        PRICE_STRATEGIES,
        SWATCHES_STRATEGIES,
        NAME_STRATEGIES,
    )
    snapshots = [dict(reg) for reg in all_regs]
    for reg in all_regs:
        reg.clear()
    try:
        yield
    finally:
        for reg, snap in zip(all_regs, snapshots, strict=True):
            reg.clear()
            reg.update(snap)


def _make_fake_strategy(abc_module: Any, abc_name: str) -> type:
    """Cria uma subclasse concreta mínima da ABC do eixo."""
    abc_cls = getattr(abc_module, abc_name)

    def _extract(self: Any, *args: Any, **kwargs: Any) -> Any:
        return None

    return type(f"Fake{abc_name}", (abc_cls,), {"extract": _extract})


# ──────────────────────────────────────────────
#  Testes parametrizados em cada eixo
# ──────────────────────────────────────────────


@pytest.mark.parametrize(
    ("module_name", "abc_name", "registry_attr", "register_fn", "get_fn"),
    REGISTRIES,
)
def test_register_strategy_and_retrieve_it(
    module_name: str,
    abc_name: str,
    registry_attr: str,
    register_fn: str,
    get_fn: str,
) -> None:
    module = importlib.import_module(module_name)
    base_module = importlib.import_module("catalogflow.modules.catalog.strategies.base")
    fake_cls = _make_fake_strategy(base_module, abc_name)

    getattr(module, register_fn)("fake_strategy", fake_cls)
    retrieved = getattr(module, get_fn)("fake_strategy")

    assert retrieved is fake_cls


@pytest.mark.parametrize(
    ("module_name", "abc_name", "registry_attr", "register_fn", "get_fn"),
    REGISTRIES,
)
def test_register_duplicate_name_raises_value_error(
    module_name: str,
    abc_name: str,
    registry_attr: str,
    register_fn: str,
    get_fn: str,
) -> None:
    module = importlib.import_module(module_name)
    base_module = importlib.import_module("catalogflow.modules.catalog.strategies.base")
    fake_cls = _make_fake_strategy(base_module, abc_name)
    getattr(module, register_fn)("dup", fake_cls)

    with pytest.raises(ValueError, match="already registered"):
        getattr(module, register_fn)("dup", fake_cls)


@pytest.mark.parametrize(
    ("module_name", "abc_name", "registry_attr", "register_fn", "get_fn"),
    REGISTRIES,
)
def test_get_unknown_name_raises_key_error_with_available(
    module_name: str,
    abc_name: str,
    registry_attr: str,
    register_fn: str,
    get_fn: str,
) -> None:
    module = importlib.import_module(module_name)
    base_module = importlib.import_module("catalogflow.modules.catalog.strategies.base")
    fake_cls = _make_fake_strategy(base_module, abc_name)
    getattr(module, register_fn)("only_one", fake_cls)

    with pytest.raises(KeyError) as exc_info:
        getattr(module, get_fn)("missing")

    assert "only_one" in str(exc_info.value)


def test_registry_is_isolated_per_axis() -> None:
    from catalogflow.modules.catalog.strategies.grade import (
        GRADE_STRATEGIES,
        register_grade_strategy,
    )
    from catalogflow.modules.catalog.strategies.sku import (
        SKU_STRATEGIES,
        register_sku_strategy,
    )

    fake_sku = type(
        "FakeSku",
        (SkuStrategy,),
        {"extract": lambda self, ctx, params: []},
    )
    fake_grade = type(
        "FakeGrade",
        (GradeStrategy,),
        {"extract": lambda self, zctx, params: None},
    )

    register_sku_strategy("xyz", fake_sku)
    register_grade_strategy("xyz", fake_grade)

    assert SKU_STRATEGIES["xyz"] is fake_sku
    assert GRADE_STRATEGIES["xyz"] is fake_grade
