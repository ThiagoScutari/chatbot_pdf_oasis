"""Engine puro de extração de pedidos a partir de PDFs preenchidos.

Esqueleto reservado para Sprint 02. Quando implementado:

    class OrderExtractor:
        def extract(self, pdf_bytes: bytes) -> RawOrderData: ...

Lógica a migrar de `oasis_romaneio.py`:
    - Iterar todos os widgets de todas as páginas
    - Filtrar field_name não-vazio e valor positivo
    - Parsear `qty__SKU__corN__TAM` (v2) e `qty__SKU__TAM` (v1 legado)
    - Detectar PDF achatado (sem /AcroForm) → PDFFlattenedError
"""

from __future__ import annotations


class OrderExtractor:
    """Engine puro de extração — bytes → RawOrderData. Implementação na Sprint 02."""

    def extract(self, pdf_bytes: bytes) -> object:
        _ = pdf_bytes
        raise NotImplementedError("OrderExtractor.extract entra na Sprint 02")
