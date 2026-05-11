"""
oasis_form_v2.py
================
Versão 2 — adiciona grade de pedido ao catálogo Oasis com suporte a
MÚLTIPLAS LINHAS DE COR (1, 2 ou 3 linhas conforme o produto).

Cada linha corresponde a uma cor detectada pelos swatches do PDF.
O swatch colorido real é desenhado à esquerda de cada linha.
As cores serão nomeadas via API futuramente; por ora usamos "Cor 1", "Cor 2"...

Nomenclatura dos campos AcroForm:
    qty__<SKU>__cor<N>__<TAM>
    Exemplo: qty__0442500912-0__cor1__PP

Uso:
    python3 oasis_form_v2.py

Saída:
    /home/claude/OASIS_MOTION_v2.pdf
"""

import re, json
import pymupdf
import pdfplumber

# ──────────────────────────────────────────────
#  CONFIGURAÇÕES
# ──────────────────────────────────────────────

INPUT_PDF  = "/mnt/user-data/uploads/CATA_LOGO_OASIS_MOTION.pdf"
OUTPUT_PDF = "/home/claude/OASIS_MOTION_v2.pdf"
META_JSON  = "/home/claude/catalogo_meta_cores.json"   # gerado anteriormente

TAMANHO_MAP = {
    "PP-M":  ["PP", "P", "M"],
    "PP-G":  ["PP", "P", "M", "G"],
    "PP-GG": ["PP", "P", "M", "G", "GG"],
    "P-M":   ["P", "M"],
    "P-G":   ["P", "M", "G"],
    "P-GG":  ["P", "M", "G", "GG"],
}

# ── Paleta de cores (brand Oasis) ──────────────
COR_HEADER_BG    = (0.12, 0.10, 0.09)
COR_HEADER_TEXT  = (1.00, 1.00, 1.00)
COR_FUNDO_PAINEL = (0.97, 0.96, 0.94)
COR_BORDA_PAINEL = (0.75, 0.72, 0.68)
COR_LABEL_TAM    = (0.20, 0.18, 0.16)
COR_LABEL_COR    = (0.30, 0.28, 0.26)
COR_CAMPO_FUNDO  = (1.00, 1.00, 1.00)
COR_CAMPO_BORDA  = (0.60, 0.57, 0.53)
COR_CAMPO_TEXTO  = (0.08, 0.08, 0.08)
COR_LINHA_DIV    = (0.82, 0.79, 0.75)

# ── Dimensões (pontos PDF) ─────────────────────
HEADER_H     = 22    # altura faixa "PEDIDO"
LABEL_TAM_H  = 20    # altura labels de tamanho (PP P M G)
CAMPO_H      = 38    # altura campo input por cor
COR_COL_W    = 70    # largura coluna "Cor N" + swatch
CAMPO_W      = 82    # largura coluna por tamanho
PAD_V        = 8     # padding vertical painel
PAD_H        = 6     # padding horizontal painel
SWATCH_SZ    = 14    # tamanho do quadrado swatch desenhado
FONTE        = "helv"

# ──────────────────────────────────────────────
#  FUNÇÕES AUXILIARES
# ──────────────────────────────────────────────

RE_SKU   = re.compile(r'\b(\d{10,13}-\d)\b')
RE_GRADE = re.compile(r'\b(PP-GG|PP-G|PP-M|P-GG|P-G|P-M)\b')
RE_NOME  = re.compile(
    r'\b(JAQUETA|CALÇA|VESTIDO|CONJUNTO|BLUSA|BODY|SHORT|BLAZER)\b',
    re.IGNORECASE
)


def detectar_swatches(page_mupdf, threshold_frac=0.920):
    h = page_mupdf.rect.height
    thr = h * threshold_frac
    swatches = []
    for d in page_mupdf.get_drawings():
        r, fill = d["rect"], d.get("fill")
        if (r.y0 >= thr and r.width < 45 and r.height < 45
                and fill and fill != (1, 1, 1)):
            swatches.append({
                "x0": r.x0, "y0": r.y0,
                "fill": tuple(round(c, 4) for c in fill),
                "fill_hex": "#{:02x}{:02x}{:02x}".format(
                    int(fill[0]*255), int(fill[1]*255), int(fill[2]*255)),
            })
    swatches.sort(key=lambda s: s["x0"])
    return swatches


def extrair_blocos_legenda(page_plumb, page_w):
    """
    Retorna lista de blocos {sku, grade, tamanhos, x_fim, y_ini, y_fim, side}
    para cada produto presente na página.
    """
    text   = page_plumb.extract_text() or ""
    skus   = RE_SKU.findall(text)
    grades = RE_GRADE.findall(text)
    if not skus:
        return []

    words     = page_plumb.extract_words()
    h         = float(page_plumb.height)
    bot_words = [w for w in words if float(w['top']) > h * 0.92]
    if not bot_words:
        return []

    blocos = []
    n      = len(skus)
    x_mid  = page_w / 2

    for i, sku in enumerate(skus):
        grade = grades[i] if i < len(grades) else (grades[0] if grades else "PP-M")
        if n == 1:
            side   = "single"
            subset = bot_words
        elif i == 0:
            side   = "left"
            subset = [w for w in bot_words if float(w['x0']) < x_mid]
        else:
            side   = "right"
            subset = [w for w in bot_words if float(w['x0']) >= x_mid]

        if not subset:
            continue

        xs = [float(w['x0']) for w in subset]
        xe = [float(w['x1']) for w in subset]
        ys = [float(w['top']) for w in subset]
        ye = [float(w['bottom']) for w in subset]

        blocos.append({
            "sku":      sku,
            "grade":    grade,
            "tamanhos": TAMANHO_MAP.get(grade, ["PP", "P", "M"]),
            "x_ini":    min(xs),
            "x_fim":    max(xe),
            "y_ini":    min(ys),
            "y_fim":    max(ye),
            "side":     side,
            "n_prods":  n,
        })
    return blocos


def calcular_painel_rect(bloco, swatches, page_w, page_h, todos_blocos):
    """
    Calcula (x0, y0, x1, y1) do painel de pedido.
    Para produto direito (right) ou único (single), o painel fica
    à direita do bloco de texto. Para produto esquerdo, também.
    """
    tamanhos = bloco["tamanhos"]
    n_cores  = max(1, len(swatches_para(bloco, swatches, page_w)))
    n_tam    = len(tamanhos)

    painel_w = COR_COL_W + n_tam * CAMPO_W + 2 * PAD_H
    painel_h = (PAD_V + HEADER_H + LABEL_TAM_H +
                n_cores * CAMPO_H + PAD_V)

    # Ancora vertical: top = y_ini do bloco de texto (minus padding)
    y0 = bloco["y_ini"] - PAD_V
    y1 = y0 + painel_h
    if y1 > page_h - 4:
        y1 = page_h - 4
        y0 = y1 - painel_h

    # Posição horizontal: sempre à direita do bloco de texto
    x0_raw = bloco["x_fim"] + 18
    x1_raw = x0_raw + painel_w

    # Garantir que não ultrapasse a borda da página
    if x1_raw > page_w - 16:
        x0_raw = page_w - painel_w - 16
        x1_raw = page_w - 16

    # Para produto esquerdo (em página de 2 produtos),
    # não invadir a zona do produto direito
    if bloco["side"] == "left":
        z_proib = min(
            (b["x_ini"] for b in todos_blocos if b["side"] == "right"),
            default=page_w
        )
        if x1_raw > z_proib - 16:
            # Comprimir: reduz largura dos campos
            x1_raw = z_proib - 16
            painel_w = x1_raw - x0_raw
            # Ajusta CAMPO_W proporcionalmente
            campo_w_novo = max(50, (painel_w - COR_COL_W - 2*PAD_H) // n_tam)
            return (float(x0_raw), float(y0), float(x1_raw), float(y1),
                    campo_w_novo)

    return (float(x0_raw), float(y0), float(x1_raw), float(y1), CAMPO_W)


def swatches_para(bloco, all_sw, page_w):
    """Filtra swatches que pertencem ao produto (por lado na página)."""
    n = bloco["n_prods"]
    x_mid = page_w / 2
    if n == 1 or bloco["side"] == "single":
        return all_sw
    if bloco["side"] == "left":
        return [s for s in all_sw if s["x0"] < x_mid]
    else:
        return [s for s in all_sw if s["x0"] >= x_mid]


# ──────────────────────────────────────────────
#  DESENHO DO PAINEL (visual + widgets)
# ──────────────────────────────────────────────

def desenhar_painel(page, bloco, sws_produto, x0, y0, x1, y1, campo_w):
    sku      = bloco["sku"]
    grade    = bloco["grade"]
    tamanhos = bloco["tamanhos"]
    n_cores  = max(1, len(sws_produto))
    n_tam    = len(tamanhos)

    # ── Fundo principal ──────────────────────────
    page.draw_rect(pymupdf.Rect(x0, y0, x1, y1),
                   color=COR_BORDA_PAINEL, fill=COR_FUNDO_PAINEL, width=0.7)

    # ── Header "PEDIDO ▸ grade" ──────────────────
    y_hdr = y0
    page.draw_rect(pymupdf.Rect(x0, y_hdr, x1, y_hdr + HEADER_H),
                   color=None, fill=COR_HEADER_BG, width=0)
    page.insert_textbox(
        pymupdf.Rect(x0 + 5, y_hdr + 3, x1 - 3, y_hdr + HEADER_H),
        f"PEDIDO  ▸  {grade}",
        fontname=FONTE, fontsize=9,
        color=COR_HEADER_TEXT, align=pymupdf.TEXT_ALIGN_LEFT
    )

    # ── Labels de tamanho ────────────────────────
    y_tam = y_hdr + HEADER_H
    for i, tam in enumerate(tamanhos):
        xc = x0 + COR_COL_W + i * campo_w
        page.insert_textbox(
            pymupdf.Rect(xc, y_tam, xc + campo_w, y_tam + LABEL_TAM_H),
            tam, fontname=FONTE, fontsize=10,
            color=COR_LABEL_TAM, align=pymupdf.TEXT_ALIGN_CENTER
        )

    # Linha divisória horizontal abaixo dos labels
    y_div = y_tam + LABEL_TAM_H
    page.draw_line(
        pymupdf.Point(x0 + 2, y_div),
        pymupdf.Point(x1 - 2, y_div),
        color=COR_LINHA_DIV, width=0.5
    )

    # ── Linhas de cor + campos ───────────────────
    for ci in range(n_cores):
        y_row = y_div + ci * CAMPO_H
        sw    = sws_produto[ci] if ci < len(sws_produto) else None

        # Swatch colorido (quadrado) + label "Cor N"
        if sw:
            sq_x = x0 + PAD_H
            sq_y = y_row + (CAMPO_H - SWATCH_SZ) / 2
            page.draw_rect(
                pymupdf.Rect(sq_x, sq_y, sq_x + SWATCH_SZ, sq_y + SWATCH_SZ),
                color=(0.4, 0.4, 0.4), fill=sw["fill"], width=0.5
            )
            txt_x = sq_x + SWATCH_SZ + 4
        else:
            txt_x = x0 + PAD_H

        label_cor = f"Cor {ci+1}"
        page.insert_textbox(
            pymupdf.Rect(txt_x, y_row + 2, x0 + COR_COL_W - 2,
                         y_row + CAMPO_H - 2),
            label_cor, fontname=FONTE, fontsize=8,
            color=COR_LABEL_COR, align=pymupdf.TEXT_ALIGN_LEFT
        )

        # Linha divisória vertical após coluna de cor
        page.draw_line(
            pymupdf.Point(x0 + COR_COL_W, y_row),
            pymupdf.Point(x0 + COR_COL_W, y_row + CAMPO_H),
            color=COR_LINHA_DIV, width=0.5
        )

        # Campos de input por tamanho
        for ti, tam in enumerate(tamanhos):
            xc    = x0 + COR_COL_W + ti * campo_w
            pad   = 4
            rect_campo = pymupdf.Rect(
                xc + pad,
                y_row + pad,
                xc + campo_w - pad,
                y_row + CAMPO_H - pad
            )
            field_name = f"qty__{sku}__cor{ci+1}__{tam}"

            widget = pymupdf.Widget()
            widget.rect          = rect_campo
            widget.field_type    = pymupdf.PDF_WIDGET_TYPE_TEXT
            widget.field_name    = field_name
            widget.field_value   = ""
            widget.text_maxlen   = 4
            widget.text_fontsize = 13
            widget.text_font     = FONTE
            widget.text_color    = COR_CAMPO_TEXTO
            widget.fill_color    = COR_CAMPO_FUNDO
            widget.border_color  = COR_CAMPO_BORDA
            widget.border_width  = 0.8
            widget.field_label   = f"Qtd {tam} / Cor {ci+1} — {sku}"
            page.add_widget(widget)

        # Linha divisória horizontal entre linhas de cor
        if ci < n_cores - 1:
            y_sep = y_row + CAMPO_H
            page.draw_line(
                pymupdf.Point(x0 + 2, y_sep),
                pymupdf.Point(x1 - 2, y_sep),
                color=COR_LINHA_DIV, width=0.3
            )


# ──────────────────────────────────────────────
#  PIPELINE PRINCIPAL
# ──────────────────────────────────────────────

def processar():
    print("Abrindo catálogo...")
    doc   = pymupdf.open(INPUT_PDF)
    plumb = pdfplumber.open(INPUT_PDF)
    total = len(doc)

    n_pages = 0
    n_skus  = 0

    for idx in range(total):
        page_m = doc[idx]
        page_p = plumb.pages[idx]
        pw = float(page_m.rect.width)
        ph = float(page_m.rect.height)

        blocos = extrair_blocos_legenda(page_p, pw)
        if not blocos:
            continue

        all_sw = detectar_swatches(page_m)

        # Calcular e desenhar painéis
        for bloco in blocos:
            sws = swatches_para(bloco, all_sw, pw)

            coords = calcular_painel_rect(bloco, sws, pw, ph, blocos)
            x0, y0, x1, y1, cw = coords

            desenhar_painel(page_m, bloco, sws, x0, y0, x1, y1, cw)

            n_cores = max(1, len(sws))
            print(f"  ✓ Pág {idx+1:02d} | {bloco['sku']} | {bloco['grade']} "
                  f"| {n_cores} cor(es) × {len(bloco['tamanhos'])} tam "
                  f"= {n_cores * len(bloco['tamanhos'])} campos "
                  f"| x={x0:.0f}–{x1:.0f}")
            n_skus += 1

        n_pages += 1

    plumb.close()

    print(f"\nSalvando... ({n_pages} págs, {n_skus} SKUs)")
    doc.save(OUTPUT_PDF, clean=True, garbage=4, deflate=True)
    doc.close()
    print(f"✅  {OUTPUT_PDF}")


if __name__ == "__main__":
    processar()
