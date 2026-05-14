"""Resolução de thumbnails de produto via AMC QRCode (compat shim).

A implementação real vive em `catalogflow.shared.image_fetcher` desde
a Sprint 04 — também é usada pelo backend para embedar fotos nos
PDFs de romaneio e relatório de pendências.

Este módulo re-exporta o helper de URL para manter retrocompat com
imports existentes (`from catalogflow.web.product_image import
fetch_product_image_url`) e com os testes que fazem `monkeypatch` no
namespace do web router.
"""

from __future__ import annotations

from catalogflow.shared.image_fetcher import fetch_product_image_url

__all__ = ["fetch_product_image_url"]
