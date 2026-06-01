"""Infraestrutura de Strategy Pattern para o `PDFAnalyzer` (ADR-010).

Este pacote agrupa, por eixo de extração (SKU, grade, preço, swatches,
nome), as ABCs e os registries que permitem que cada marca tenha um
`BrandFormatProfile` apontando para estratégias plugáveis.

A Fase A (Sprint 08) entrega apenas a infraestrutura — nenhuma estratégia
concreta. Implementações chegam a partir da Fase B.
"""
