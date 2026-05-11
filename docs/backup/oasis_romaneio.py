"""
oasis_romaneio.py
=================
Lê um catálogo Oasis preenchido (PDF com campos AcroForm),
extrai as quantidades informadas e gera um Romaneio de Pedido em PDF.

Suporta os dois formatos de campo:
  - v1: qty__<SKU>__<TAM>               (uma cor por produto)
  - v2: qty__<SKU>__cor<N>__<TAM>       (múltiplas cores por produto)

Uso:
    python3 oasis_romaneio.py <catalogo_preenchido.pdf> [lojista] [saida.pdf]

Exemplo:
    python3 oasis_romaneio.py OASIS_MOTION_v2_preenchido.pdf "Loja Moda & Arte"

Saída:
    romaneio_<lojista>.pdf
"""

import re, sys, json
from datetime import datetime
from collections import defaultdict
import pymupdf

# ──────────────────────────────────────────────
#  METADADOS DO CATÁLOGO (nomes e preços)
#  Fonte: extraídos do PDF original
# ──────────────────────────────────────────────

CATALOGO_INFO = {
    "0322500004-0": {"nome": "Jaqueta Berenice",         "preco": 3488.00},
    "0142500001-0": {"nome": "Calça Capri Esther",       "preco":  588.00},
    "0442500941-0": {"nome": "Vestido Joana",             "preco": 1598.00},
    "0442500885-0": {"nome": "Vestido Gina",              "preco": 1348.00},
    "0442500902-0": {"nome": "Vestido Paolla",            "preco":  988.00},
    "2222500382-0": {"nome": "Conjunto Aline",            "preco": 1888.00},
    "2222500377-0": {"nome": "Conjunto Body Saia Marta",  "preco": 2198.00},
    "2222500384-0": {"nome": "Conjunto Rubia",            "preco": 3588.00},
    "0442500921-0": {"nome": "Vestido Yasmin",            "preco": 1188.00},
    "0442500912-0": {"nome": "Vestido Safira",            "preco": 1388.00},
    "0442500907-0": {"nome": "Vestido Magdalena",         "preco": 1488.00},
    "2222500376-0": {"nome": "Conjunto Pricila",          "preco": 1498.00},
    "0442500901-0": {"nome": "Vestido Hellen",            "preco": 2198.00},
    "2222500386-0": {"nome": "Conjunto Body E Saia Noemi","preco": 2348.00},
    "0442500906-0": {"nome": "Vestido Suri",              "preco": 1698.00},
    "0442500903-0": {"nome": "Vestido Janine",            "preco": 1648.00},
    "0442500905-0": {"nome": "Vestido Ruth",              "preco":  828.00},
    "0442500911-0": {"nome": "Vestido Merabe",            "preco": 1328.00},
    "2222500383-0": {"nome": "Conjunto Brunna",           "preco": 1548.00},
    "2222500380-0": {"nome": "Conjunto Body E Saia Miriam","preco":2288.00},
    "0422500012-0": {"nome": "Body Rebeca",               "preco": 1588.00},
    "0062500062-0": {"nome": "Short Alfaiataria Leia",    "preco": 1068.00},
    "0442500904-0": {"nome": "Vestido Lidia",             "preco": 1368.00},
    "0362500185-0": {"nome": "Blusa Abigail em Seda",     "preco":  828.00},
    "0022500151-0": {"nome": "Calça Ana em Seda",         "preco": 1798.00},
    "0362500186-0": {"nome": "Blusa Yoana",               "preco": 1688.00},
    "0022500149-0": {"nome": "Calça Raquel",              "preco":  928.00},
    "0442500915-0": {"nome": "Vestido Hanna",             "preco": 1988.00},
    "0582500025-0": {"nome": "Blazer Sarah",              "preco": 2398.00},
    "442500908-0":  {"nome": "Vestido Salomé",            "preco": 1288.00},
}

ORDEM_TAMANHOS = ["PP", "P", "M", "G", "GG"]

# Regex para parsear nomes de campo
RE_V2 = re.compile(r'^qty__(.+)__cor(\d+)__([A-Z]+)$')   # v2: com cor
RE_V1 = re.compile(r'^qty__(.+)__([A-Z]+)$')              # v1: sem cor


# ──────────────────────────────────────────────
#  EXTRAÇÃO DOS DADOS DO PDF
# ──────────────────────────────────────────────

def extrair_pedido(pdf_path: str) -> dict:
    """
    Lê todos os campos AcroForm preenchidos do PDF.
    Retorna um dict organizado:
      {
        sku: {
          cor_idx (int, 1-based): {
            tamanho: qtd (int)
          }
        }
      }
    """
    pedido = defaultdict(lambda: defaultdict(dict))

    doc = pymupdf.open(pdf_path)

    for page in doc:
        for widget in page.widgets() or []:
            if widget.field_type != pymupdf.PDF_WIDGET_TYPE_TEXT:
                continue

            name  = widget.field_name or ""
            value = (widget.field_value or "").strip()

            if not value:
                continue

            try:
                qtd = int(value)
            except ValueError:
                try:
                    qtd = int(float(value))
                except ValueError:
                    continue

            if qtd <= 0:
                continue

            # Tentar parsear v2 primeiro (com cor)
            m2 = RE_V2.match(name)
            if m2:
                sku, cor_idx, tam = m2.group(1), int(m2.group(2)), m2.group(3)
                pedido[sku][cor_idx][tam] = pedido[sku][cor_idx].get(tam, 0) + qtd
                continue

            # Fallback v1 (sem cor → atribui à cor 1)
            m1 = RE_V1.match(name)
            if m1:
                sku, tam = m1.group(1), m1.group(2)
                pedido[sku][1][tam] = pedido[sku][1].get(tam, 0) + qtd

    doc.close()

    # Converter defaultdict para dict normal
    return {sku: dict(cores) for sku, cores in pedido.items()}


# ──────────────────────────────────────────────
#  GERAÇÃO DO ROMANEIO EM PDF
# ──────────────────────────────────────────────

# ── Cores do romaneio ──────────────────────────
C_PRETO    = (0.05, 0.05, 0.05)
C_CINZA_E  = (0.92, 0.91, 0.89)   # fundo linha par
C_CINZA_C  = (0.75, 0.73, 0.70)   # separador
C_BRAND    = (0.12, 0.10, 0.09)   # cabeçalho brand
C_BRAND_T  = (1.00, 1.00, 1.00)   # texto no brand
C_ACENTO   = (0.65, 0.50, 0.25)   # dourado Oasis
C_VERDE    = (0.10, 0.55, 0.25)   # valor positivo
C_TEXTO    = (0.18, 0.16, 0.14)   # texto geral
C_MUTED    = (0.50, 0.48, 0.45)   # texto secundário
C_BRANCO   = (1.00, 1.00, 1.00)
C_BORDA    = (0.80, 0.78, 0.75)

FONTE      = "helv"
FONTE_B    = "hebo"   # helv bold

# ── Dimensões da página do romaneio ───────────
PAGE_W   = 595   # A4 largura
PAGE_H   = 842   # A4 altura
MARGIN_X = 36
MARGIN_Y = 36
CONTENT_W = PAGE_W - 2 * MARGIN_X

# ── Larguras das colunas da grade ─────────────
COL_COR  = 70
COL_TAM  = 60
COLS_TAMANHOS = ["PP", "P", "M", "G", "GG"]
N_COLUNAS = len(COLS_TAMANHOS)
COL_TOTAL = 55

ROW_H_DADOS  = 20
ROW_H_HEADER = 18
ROW_H_SKU    = 24


def formatar_moeda(v: float) -> str:
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


class RomaneioBuilder:
    def __init__(self, lojista: str):
        self.doc     = pymupdf.open()
        self.lojista = lojista
        self._new_page()

    def _new_page(self):
        self.page = self.doc.new_page(width=PAGE_W, height=PAGE_H)
        self.y    = MARGIN_Y

    def _check_space(self, needed: float):
        if self.y + needed > PAGE_H - MARGIN_Y - 40:
            self._finalizar_pagina_rodape()
            self._new_page()
            self.y += 10  # pequena margem após virada

    def _finalizar_pagina_rodape(self):
        n = self.doc.page_count
        self.page.insert_textbox(
            pymupdf.Rect(MARGIN_X, PAGE_H - 25, PAGE_W - MARGIN_X, PAGE_H - 10),
            f"Pág. {n}  —  Oasis Resortwear  ©  Winter 26 / MOTION",
            fontname=FONTE, fontsize=8, color=C_MUTED,
            align=pymupdf.TEXT_ALIGN_CENTER
        )

    # ── Cabeçalho principal ──────────────────────
    def cabecalho(self):
        # Faixa brand (altura maior para garantir espaço)
        self.page.draw_rect(
            pymupdf.Rect(0, self.y, PAGE_W, self.y + 56),
            color=None, fill=C_BRAND, width=0
        )
        # insert_text: baseline = y + ascender (~75% da fontsize)
        self.page.insert_text(
            (MARGIN_X, self.y + 24),
            "OASIS RESORTWEAR",
            fontname=FONTE_B, fontsize=16, color=C_BRAND_T
        )
        self.page.insert_text(
            (MARGIN_X, self.y + 44),
            "ROMANEIO DE PEDIDO  -  Winter 26 / MOTION",
            fontname=FONTE, fontsize=9, color=(0.80, 0.75, 0.65)
        )
        self.y += 62

        # Info do pedido
        agora = datetime.now().strftime("%d/%m/%Y  %H:%M")
        self.page.insert_text(
            (MARGIN_X, self.y + 12),
            f"Lojista:  {self.lojista}",
            fontname=FONTE_B, fontsize=9, color=C_TEXTO
        )
        # Texto alinhado à direita: calcular posição manualmente
        txt_data = f"Emitido em:  {agora}"
        w_txt = pymupdf.get_text_length(txt_data, fontname=FONTE, fontsize=9)
        self.page.insert_text(
            (PAGE_W - MARGIN_X - w_txt, self.y + 12),
            txt_data, fontname=FONTE, fontsize=9, color=C_MUTED
        )
        self.y += 22

        # Linha separadora
        self.page.draw_line(
            pymupdf.Point(MARGIN_X, self.y),
            pymupdf.Point(PAGE_W - MARGIN_X, self.y),
            color=C_ACENTO, width=1.5
        )
        self.y += 10

    # ── Cabeçalho da grade de tamanhos ──────────
    def _header_grade(self, x_cor, x_tams, x_total):
        self.page.draw_rect(
            pymupdf.Rect(MARGIN_X, self.y, PAGE_W - MARGIN_X, self.y + ROW_H_HEADER),
            color=None, fill=(0.88, 0.86, 0.83), width=0
        )
        self.page.insert_textbox(
            pymupdf.Rect(x_cor, self.y + 2, x_cor + COL_COR, self.y + ROW_H_HEADER),
            "Cor", fontname=FONTE_B, fontsize=8, color=C_TEXTO,
            align=pymupdf.TEXT_ALIGN_LEFT
        )
        for i, tam in enumerate(COLS_TAMANHOS):
            self.page.insert_textbox(
                pymupdf.Rect(x_tams + i * COL_TAM, self.y + 2,
                             x_tams + i * COL_TAM + COL_TAM, self.y + ROW_H_HEADER),
                tam, fontname=FONTE_B, fontsize=8, color=C_TEXTO,
                align=pymupdf.TEXT_ALIGN_CENTER
            )
        self.page.insert_textbox(
            pymupdf.Rect(x_total, self.y + 2, x_total + COL_TOTAL, self.y + ROW_H_HEADER),
            "TOTAL", fontname=FONTE_B, fontsize=8, color=C_TEXTO,
            align=pymupdf.TEXT_ALIGN_CENTER
        )
        self.y += ROW_H_HEADER

    # ── Bloco de produto ─────────────────────────
    def bloco_produto(self, sku: str, cores: dict, n_produto: int):
        info   = CATALOGO_INFO.get(sku, {"nome": sku, "preco": 0.0})
        nome   = info["nome"]
        preco  = info["preco"]
        n_cors = len(cores)

        # Calcular total do produto
        total_pecas = sum(
            qtd
            for tams in cores.values()
            for qtd in tams.values()
        )
        valor_total = total_pecas * preco

        # Espaço necessário
        needed = ROW_H_SKU + ROW_H_HEADER + n_cors * ROW_H_DADOS + 16
        self._check_space(needed)

        # Fundo alternado do bloco de produto
        cor_fundo_bloco = C_CINZA_E if n_produto % 2 == 0 else C_BRANCO
        bloco_y0 = self.y
        bloco_h  = needed - 8
        self.page.draw_rect(
            pymupdf.Rect(MARGIN_X, bloco_y0,
                         PAGE_W - MARGIN_X, bloco_y0 + bloco_h),
            color=C_BORDA, fill=cor_fundo_bloco, width=0.4
        )

        # ── Linha de SKU / nome / preço ───────────
        self.page.insert_text(
            (MARGIN_X + 4, self.y + 14),
            nome.upper(),
            fontname=FONTE_B, fontsize=9, color=C_TEXTO
        )
        self.page.insert_text(
            (MARGIN_X + 210, self.y + 14),
            f"Ref: {sku}",
            fontname=FONTE, fontsize=7, color=C_MUTED
        )
        resumo = f"{formatar_moeda(preco)} / un  |  {total_pecas} pc  ->  {formatar_moeda(valor_total)}"
        w_res = pymupdf.get_text_length(resumo, fontname=FONTE_B, fontsize=8)
        self.page.insert_text(
            (PAGE_W - MARGIN_X - 4 - w_res, self.y + 14),
            resumo, fontname=FONTE_B, fontsize=8, color=C_ACENTO
        )
        self.y += ROW_H_SKU

        # Calcular x-offsets da grade (centralizado no conteúdo disponível)
        grade_w = COL_COR + N_COLUNAS * COL_TAM + COL_TOTAL
        x_start = MARGIN_X + 4
        x_cor   = x_start
        x_tams  = x_cor + COL_COR
        x_total = x_tams + N_COLUNAS * COL_TAM

        # ── Header da grade ───────────────────────
        self._header_grade(x_cor, x_tams, x_total)

        # ── Linhas de cor ─────────────────────────
        for ci, (cor_idx, tams) in enumerate(sorted(cores.items())):
            row_y = self.y

            # Fundo alternado por linha
            if ci % 2 == 1:
                self.page.draw_rect(
                    pymupdf.Rect(MARGIN_X + 2, row_y,
                                 PAGE_W - MARGIN_X - 2, row_y + ROW_H_DADOS),
                    color=None, fill=(0.95, 0.94, 0.92), width=0
                )

            # Label da cor
            self.page.insert_textbox(
                pymupdf.Rect(x_cor + 2, row_y + 2,
                             x_cor + COL_COR - 2, row_y + ROW_H_DADOS),
                f"Cor {cor_idx}",
                fontname=FONTE, fontsize=8, color=C_TEXTO,
                align=pymupdf.TEXT_ALIGN_LEFT
            )

            # Quantidades por tamanho
            total_cor = 0
            for i, tam in enumerate(COLS_TAMANHOS):
                qtd = tams.get(tam, 0)
                total_cor += qtd
                x_cel = x_tams + i * COL_TAM
                cor_qtd = C_TEXTO if qtd == 0 else C_PRETO
                peso    = FONTE if qtd == 0 else FONTE_B
                self.page.insert_textbox(
                    pymupdf.Rect(x_cel, row_y + 2,
                                 x_cel + COL_TAM, row_y + ROW_H_DADOS),
                    str(qtd) if qtd > 0 else "-",
                    fontname=peso, fontsize=9,
                    color=cor_qtd, align=pymupdf.TEXT_ALIGN_CENTER
                )

            # Total da cor
            self.page.insert_textbox(
                pymupdf.Rect(x_total, row_y + 2,
                             x_total + COL_TOTAL, row_y + ROW_H_DADOS),
                str(total_cor),
                fontname=FONTE_B, fontsize=9,
                color=C_VERDE if total_cor > 0 else C_MUTED,
                align=pymupdf.TEXT_ALIGN_CENTER
            )
            self.y += ROW_H_DADOS

        # Linha divisória inferior do bloco
        self.page.draw_line(
            pymupdf.Point(MARGIN_X, self.y + 4),
            pymupdf.Point(PAGE_W - MARGIN_X, self.y + 4),
            color=C_CINZA_C, width=0.5
        )
        self.y += 12

    # ── Totalizador final ────────────────────────
    def totalizador(self, total_pecas: int, valor_total: float, n_skus: int):
        self._check_space(70)
        self.y += 8

        # Faixa brand para o total
        self.page.draw_rect(
            pymupdf.Rect(MARGIN_X, self.y, PAGE_W - MARGIN_X, self.y + 56),
            color=None, fill=C_BRAND, width=0
        )

        self.page.insert_text(
            (MARGIN_X + 8, self.y + 20),
            f"{n_skus} referencias  |  {total_pecas} pecas",
            fontname=FONTE_B, fontsize=11, color=C_BRAND_T
        )

        lbl = "VALOR TOTAL DO PEDIDO"
        w_lbl = pymupdf.get_text_length(lbl, fontname=FONTE, fontsize=8)
        self.page.insert_text(
            (PAGE_W - MARGIN_X - 8 - w_lbl, self.y + 18),
            lbl, fontname=FONTE, fontsize=8, color=(0.75, 0.70, 0.60)
        )

        valor_str = formatar_moeda(valor_total)
        w_val = pymupdf.get_text_length(valor_str, fontname=FONTE_B, fontsize=16)
        self.page.insert_text(
            (PAGE_W - MARGIN_X - 8 - w_val, self.y + 44),
            valor_str, fontname=FONTE_B, fontsize=16, color=C_ACENTO
        )
        self.y += 62

    def salvar(self, path: str):
        self._finalizar_pagina_rodape()
        self.doc.save(path, clean=True, garbage=4, deflate=True)
        self.doc.close()
        print(f"✅  Romaneio salvo: {path}")


# ──────────────────────────────────────────────
#  MAIN
# ──────────────────────────────────────────────

def gerar_romaneio(pdf_path: str, lojista: str = "—", output_path: str = None):
    print(f"Lendo campos de: {pdf_path}")
    pedido = extrair_pedido(pdf_path)

    if not pedido:
        print("⚠️  Nenhum campo preenchido encontrado no PDF.")
        return

    # Estatísticas
    total_pecas = 0
    valor_total = 0.0
    for sku, cores in pedido.items():
        info  = CATALOGO_INFO.get(sku, {"preco": 0})
        pecas = sum(q for t in cores.values() for q in t.values())
        total_pecas += pecas
        valor_total += pecas * info["preco"]

    print(f"  → {len(pedido)} SKUs  |  {total_pecas} peças  |  {formatar_moeda(valor_total)}")

    # Nome do arquivo de saída
    if not output_path:
        slug = lojista.lower().replace(" ", "_").replace("&", "e")
        output_path = f"/home/claude/romaneio_{slug}.pdf"

    # Construir o PDF do romaneio
    builder = RomaneioBuilder(lojista)
    builder.cabecalho()

    for i, (sku, cores) in enumerate(sorted(pedido.items()), start=1):
        builder.bloco_produto(sku, cores, i)

    builder.totalizador(total_pecas, valor_total, len(pedido))
    builder.salvar(output_path)

    return output_path


# ──────────────────────────────────────────────
#  MODO DEMO: gera com dados simulados
# ──────────────────────────────────────────────

def demo():
    """
    Demonstra o romaneio com um pedido fictício.
    Use isto antes de ter um PDF preenchido real.
    """
    import tempfile, os

    # Criar um PDF de teste preenchendo campos manualmente
    pdf_path = "/home/claude/OASIS_MOTION_v2.pdf"
    doc = pymupdf.open(pdf_path)

    # Simular preenchimento
    campos_demo = {
        "qty__0442500941-0__cor1__PP": "2",
        "qty__0442500941-0__cor1__P":  "3",
        "qty__0442500941-0__cor1__M":  "2",
        "qty__0442500941-0__cor1__G":  "1",
        "qty__0442500941-0__cor2__PP": "0",
        "qty__0442500941-0__cor2__P":  "2",
        "qty__0442500941-0__cor2__M":  "4",
        "qty__0442500941-0__cor2__G":  "2",
        "qty__0442500912-0__cor1__PP": "3",
        "qty__0442500912-0__cor1__P":  "5",
        "qty__0442500912-0__cor1__M":  "2",
        "qty__0442500912-0__cor2__PP": "1",
        "qty__0442500912-0__cor2__P":  "2",
        "qty__0442500912-0__cor2__M":  "2",
        "qty__2222500376-0__cor1__PP": "2",
        "qty__2222500376-0__cor1__P":  "3",
        "qty__2222500376-0__cor1__M":  "3",
        "qty__2222500376-0__cor2__PP": "0",
        "qty__2222500376-0__cor2__P":  "1",
        "qty__2222500376-0__cor2__M":  "2",
        "qty__0442500906-0__cor1__PP": "0",
        "qty__0442500906-0__cor1__P":  "2",
        "qty__0442500906-0__cor1__M":  "3",
        "qty__0582500025-0__cor1__PP": "1",
        "qty__0582500025-0__cor1__P":  "2",
        "qty__0582500025-0__cor1__M":  "2",
    }

    # Preencher os campos no PDF
    for page in doc:
        for widget in page.widgets() or []:
            if widget.field_name in campos_demo:
                widget.field_value = campos_demo[widget.field_name]
                widget.update()

    demo_path = "/home/claude/DEMO_preenchido.pdf"
    doc.save(demo_path)
    doc.close()
    print(f"PDF demo preenchido salvo: {demo_path}")

    gerar_romaneio(demo_path, "Loja Moda & Arte — Belo Horizonte",
                   "/home/claude/romaneio_demo.pdf")


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] != "demo":
        pdf_in   = sys.argv[1]
        lojista  = sys.argv[2] if len(sys.argv) >= 3 else "Lojista"
        out      = sys.argv[3] if len(sys.argv) >= 4 else None
        gerar_romaneio(pdf_in, lojista, out)
    else:
        # Modo demo
        demo()
