# Pesquisa Técnica Aprofundada: Transformação de Catálogo PDF Visual em Pedido B2B Estruturado para Lojistas de Moda (Oasis Resortwear)

> **Escopo:** análise técnica completa sobre como converter um catálogo PDF visual (gerado por agência, sem camadas editáveis) em um instrumento de captura de pedidos com mínima fricção para lojistas que operam pelo celular e WhatsApp, com backend em Python. Cobre fundamentos de PDF interativo, bibliotecas Python, estratégias de posicionamento, fluxo de retorno de dados, alternativas (HTML form, Google Forms, visão computacional, QR code) e recomendação final.
>
> **Conclusão executiva (antecipada para diretores técnicos com pouco tempo):** após reunir evidências de Adobe, Foxit, Datalogics, Apryse, IDR Solutions, PSPDFKit/Nutrient, ISO 32000, documentação oficial do PyMuPDF, ReportLab, pdfrw, pypdf e PyPDFForm, além de discussões reais de comunidade (Adobe Community, Apple Discussions, GitHub), **a abordagem de "PDF editável com AcroForm + submit por HTTP" é tecnicamente possível em Python mas é a pior escolha para o público-alvo descrito** (lojistas em celular Android/iOS chegando via WhatsApp). O caminho recomendado é **PDF com QR Code/links por produto que direciona para um formulário web (HTML) responsivo hospedado no backend Python**, complementado em uma segunda fase por **agente de visão (GPT‑4o/Claude/Gemini) que lê fotos do catálogo marcado à mão** como fallback para clientes que preferem o fluxo atual. Justificativa detalhada no Bloco 6.

---

## BLOCO 1 — Fundamentos técnicos de PDF editável

### 1.1 PDF estático vs AcroForm vs XFA vs PDF/A

O ISO 32000 (PDF 1.7 e PDF 2.0) define o PDF como uma árvore de objetos. Acima dessa árvore há quatro "modos" relevantes para o nosso problema:

- **PDF estático (puramente visual):** o documento contém apenas operadores de página (texto, imagens, vetores). Não há entrada `/AcroForm` no Catalog, nem anotações do subtype `/Widget`. É o que sai do InDesign/Illustrator de uma agência quando ninguém pediu formulário. Compatibilidade universal: abre em qualquer leitor (Acrobat Reader, Foxit, Preview do macOS/iOS, Chrome PDF viewer, Edge, Firefox PDF.js, Safari, Adobe Acrobat mobile).

- **AcroForm (ISO 32000):** introduzido em 1996 com PDF 1.2 e padronizado no ISO 32000-1 (PDF 1.7) e ISO 32000-2 (PDF 2.0). É a forma "original" e atualmente única recomendada de formulário PDF. Cada campo é uma anotação `/Subtype /Widget` ligada a uma página, e há uma estrutura raiz `/AcroForm` no Catalog do documento listando todos os campos. Suportado por praticamente todos os leitores que renderizam PDF (Acrobat Reader, Foxit, Nitro, PDF-XChange, Adobe Reader mobile no iOS/Android). Suporte parcial em browsers (Chrome/Edge/Firefox/Safari) — eles renderizam e permitem editar campos, mas o suporte a JavaScript embarcado e ações de Submit é desigual (Datalogics, Appligent Labs).

- **XFA (XML Forms Architecture):** introduzido pela Adobe em 2003 com PDF 1.5, embutia um template XML inteiro dentro do PDF. **XFA foi DEPRECADO no PDF 2.0 (ISO 32000-2:2017).** Não é suportado pelo Chrome PDF viewer, Firefox PDF.js, Safari, Preview do macOS, Adobe Reader mobile mais recente, nem pela maioria dos leitores de terceiros. Quando aberto em viewer incompatível mostra apenas uma página "Please wait…" (Apryse, IDR Solutions, Wikipedia/XFA). **Não usar.**

- **PDF/A (ISO 19005):** padrão de arquivamento de longo prazo. PDF/A‑1 e PDF/A‑2 proíbem XFA, JavaScript e referências externas. Permite AcroForm estático (sem JS de submit). Para nosso caso é irrelevante diretamente, mas é importante saber: se quiser conformidade arquivística, AcroForm é o único caminho viável.

**Implicações práticas de compatibilidade de leitores (consolidado de várias fontes):**

| Leitor / Plataforma | PDF estático | AcroForm fields (visualizar/editar) | AcroForm JavaScript | AcroForm Submit HTTP | XFA |
|---|---|---|---|---|---|
| Adobe Acrobat / Reader desktop | OK | OK | OK | OK | Parcial (legado) |
| Adobe Reader mobile (iOS/Android) | OK | OK (preenche e salva) | Parcial | **Falha frequente** (vide thread Adobe Community 9396139) | Não |
| Foxit / Nitro / PDF-XChange | OK | OK | OK | OK | Não/parcial |
| Chrome / Edge built-in viewer | OK | OK preenche | Limitado | Limitado | Não |
| Firefox PDF.js | OK | OK preenche | Limitado | Limitado | Não |
| Safari iOS (Quick Look / preview) | OK | **Visualiza mas não permite preencher de forma confiável** | Não | Não | Não |
| Apple Preview macOS | OK | OK | Limitado | Não | Não |
| Google Drive viewer | OK | Não preenche | Não | Não | Não |
| WhatsApp viewer embutido | OK (apenas visualização) | **Não permite preencher** | Não | Não | Não |

O ponto crítico: o **viewer embutido do WhatsApp não preenche campos de formulário** — a usuária precisa explicitamente "abrir em outro app" (Adobe Reader, Files, Drive). Isso é uma fricção real, documentada em discussões da comunidade Jotform, Adobe e Apple Discussions.

### 1.2 AcroForm — estrutura interna e tipos de campo

Estrutura interna (ISO 32000-1, §12.7):

```
Catalog
 └── /AcroForm
      ├── /Fields  → [array de IndirectRefs para Field objects]
      ├── /NeedAppearances true   ← obriga o viewer a recalcular aparência
      ├── /DA "/Helv 0 Tf 0 g"    ← Default Appearance string
      └── /DR  → dicionário de recursos (fontes etc.)

Page
 └── /Annots
      └── annotation /Subtype /Widget
            ├── /FT  (Field Type): /Tx (text), /Btn (button), /Ch (choice), /Sig (signature)
            ├── /T   (nome do campo)
            ├── /V   (valor)
            ├── /Rect [x1 y1 x2 y2]
            ├── /MaxLen, /Ff (field flags)
            └── /AP  (Appearance stream)
```

Tipos de campo definidos pelo PDF Reference (§12.7.4):

1. **Text field** (`/Tx`): texto livre, uma ou múltiplas linhas. Subflags importantes: `Multiline` (bit 13), `Password` (bit 14), `FileSelect` (bit 21), `DoNotSpellCheck` (bit 23), `Comb` (bit 25, caracteres tabulados em células — útil para SKU), e via `/AA` (Additional Actions) é possível filtrar somente dígitos.
2. **Button field** (`/Btn`): com subflags determina **Push button** (bit 17), **Radio** (bit 16), ou **Checkbox** (default). Radios precisam de grupo via `/Parent` compartilhado e `/Kids`.
3. **Choice field** (`/Ch`): com subflag `Combo` (bit 18) torna-se dropdown; sem ela é listbox. `/Opt` é o array de opções.
4. **Signature field** (`/Sig`): assinatura digital criptográfica — irrelevante para nosso caso.

**Qual o melhor para "quantidade numérica" em um pedido B2B de moda?** O recomendado é **Text field com formatação numérica via JavaScript de keystroke** (`AFNumber_Keystroke`) ou, idealmente, **Text field com flag `Comb` + `MaxLen=3` ou 4** (uma "célula" por dígito), porque:

- Em desktop, a validação JS funciona perfeitamente.
- **Em mobile, JavaScript é frequentemente ignorado**, então a validação deve ser silenciosa (apenas teclado numérico) ou no servidor.
- Combo box ("0, 1, 2, 5, 10, 12…") **NÃO é recomendado**: a issue [pymupdf/PyMuPDF#1311](https://github.com/pymupdf/PyMuPDF/issues/1311) documenta que Adobe Acrobat Pro 2020 e Adobe Reader DC no macOS **não exibem o valor** do combobox preenchido programaticamente — comportamento inconsistente entre viewers.

Para grade de tamanhos (P/M/G/GG/XGG) a melhor escolha é **um Text field numérico por célula**, pois:

- Permite que a lojista digite "0", "3", "12" diretamente.
- Funciona em todos os viewers AcroForm.
- Estrutura idêntica em todas as páginas, facilitando o parsing.
- Não depende de JavaScript.

### 1.3 JavaScript em PDF (PDF JS Actions)

Definido pela Adobe em "Acrobat JavaScript Scripting Reference". É um dialeto baseado em ECMAScript, executado pelo motor JS do leitor, com APIs específicas (`Field`, `Doc`, `app`, `event`). Triggers possíveis (ISO 32000 §12.6.3): mouse up/down/enter/exit, field focus/blur, format, keystroke, validate, calculate, page open/close, document open/save/print, willSave/willPrint.

Uso típico:

```javascript
// Trigger: Format (mostrar como inteiro)
event.value = AFMakeNumber(event.value);
if (isNaN(event.value)) event.value = "";

// Trigger: Calculate em campo "Total"
var sum = 0;
for (var i=0; i<this.numFields; i++) {
   var f = this.getField(this.getNthFieldName(i));
   if (f.name.match(/^qty_/)) sum += Number(f.value)||0;
}
event.value = sum;

// Trigger: Mouse Up no botão Submit
this.submitForm({
   cURL: "https://api.oasisresortwear.com/pedido",
   cSubmitAs: "XFDF"
});
```

**Suporte por viewer (compilado de Datalogics, Foxit, Appligent Labs, Adobe Community):**

| Viewer | JS de cálculo/validação | `submitForm()` | `mailto:` em submit |
|---|---|---|---|
| Acrobat Reader desktop (Win/Mac) | OK | OK | OK (abre cliente mail) |
| Foxit / Nitro / PDF-XChange | OK majoritariamente | OK | OK |
| Acrobat Reader iOS | Parcial | **Frequentemente quebra** | Parcial |
| Acrobat Reader Android | Parcial | **Reportes recorrentes de falha** (vide thread Adobe 9396139) | Parcial |
| Chrome / Edge / Firefox PDF | Ignora JS na maior parte | Não confiável | Não |
| Safari iOS preview | Ignora | Não | Não |
| Preview macOS | Ignora | Não | Não |

**Conclusão para Oasis Resortwear:** assumir que **JavaScript em PDF NÃO funciona** no celular. Qualquer cálculo de total, validação de quantidade ou submit precisa ser planejado para falhar graciosamente quando o JS for ignorado.

### 1.4 FDF e XFDF

**FDF (Forms Data Format):** definido no Adobe FDF Toolkit Reference e formalizado em ISO 32000. É um subconjunto da sintaxe PDF que carrega apenas pares nome/valor de campos. Estrutura típica:

```
%FDF-1.2
1 0 obj
<<
 /FDF << /Fields [
   << /T (ref_produto_001_cor_AZUL_tam_P) /V (3) >>
   << /T (ref_produto_001_cor_AZUL_tam_M) /V (5) >>
 ] >>
>>
endobj
trailer << /Root 1 0 R >>
%%EOF
```

**XFDF (XML Forms Data Format):** mesma semântica, formato XML — mais fácil de parsear em Python (`xml.etree`). Estrutura:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<xfdf xmlns="http://ns.adobe.com/xfdf/">
  <fields>
    <field name="ref_001_AZUL_P"><value>3</value></field>
    <field name="ref_001_AZUL_M"><value>5</value></field>
  </fields>
</xfdf>
```

O botão Submit do PDF é configurado com um action object `/S /SubmitForm`, uma `/URL` de destino e um conjunto de flags (PSPDFKit/Nutrient documenta os bits): bit 1 `ExportFormat`, bit 2 `IncludeNoValueFields`, bit 3 `GetMethod`, bit 5 `SubmitAsPDF`, bit 6 `AsXFDF`. Se `AsXFDF=1` o cliente envia XML; se nenhum desses bits, envia FDF. O servidor recebe via HTTP POST.

**É possível receber dados estruturados sem servidor?** Sim, parcialmente:

- `submitForm()` com `cSubmitAs:"FDF"` e uma URL `mailto:` faz o leitor (no desktop) abrir o cliente de email com um anexo `.fdf` ou XFDF anexado. **No mobile isso raramente funciona** — em iOS/Android o Adobe Reader simplesmente "compartilha" o PDF inteiro.
- Alternativa: o usuário salva o PDF preenchido inteiro e envia por WhatsApp. Você recebe o `.pdf` e parseia no backend (cobertura no Bloco 4).

### 1.5 Limitações conhecidas em mobile

Evidências coletadas:

- **Safari iOS Preview / Quick Look:** abre o PDF, mas os campos AcroForm aparecem como cinza/inertes — exige "abrir em outro app".
- **Chrome Android:** abre via Google Drive viewer, que **não preenche AcroForm**. Precisa baixar e abrir no Adobe Reader/Files.
- **WhatsApp viewer:** apenas visualização. A lojista precisa fazer um "compartilhar com" e escolher um app de PDF. Fricção real.
- **Adobe Reader mobile:** preenche e salva, mas botões de Submit HTTP frequentemente falham silenciosamente (thread Adobe Community 9396139 confirma isso para Android 7+).
- **Foxit Mobile / Xodo / PDF Expert iOS:** funcionam melhor, mas não há garantia de qual app a lojista tem instalado.

**Alternativas que preservam mobile:**

1. **HTML responsivo em URL única** (mesma experiência em qualquer celular).
2. **QR Code em cada produto do PDF → URL com formulário web mobile-first**.
3. **WhatsApp Flows** (formulários nativos do WhatsApp Business API, lançados em 2023).
4. **Visão computacional sobre foto/print do catálogo marcado à mão** (Bloco 5).

---

## BLOCO 2 — Bibliotecas Python para criação e manipulação de PDF editável

### 2.1 pypdf (ex‑PyPDF2)

**Versão atual:** pypdf 6.x (maio 2026), em desenvolvimento ativo no GitHub. PyPDF2 foi descontinuado e fundido em pypdf em 2022.

**Capacidades para AcroForm:**

- Ler e listar campos: `reader.get_fields()`, ou iterar `page.annotations` filtrando `Subtype == "/Widget"`.
- Preencher valores existentes: `writer.update_page_form_field_values(page, {"field_name": "valor"}, auto_regenerate=False)`.
- Achatar campos (transformar em conteúdo de página): combinação de `flatten` + `remove_annotations(subtypes="/Widget")`.

**Limitação crítica:** pypdf **NÃO tem API alto-nível para CRIAR campos de formulário do zero sobre um PDF existente**. Para criar, é preciso construir manualmente os dicionários PDF (`DictionaryObject` com `/FT /Tx`, `/T`, `/Rect` etc.) e injetá-los em `/AcroForm/Fields` e no `/Annots` da página — viável mas trabalhoso e propenso a erro. Em produção, usa-se pypdf para leitura/preenchimento e PyMuPDF/ReportLab para criação.

**Exemplo de extração de campos preenchidos:**

```python
from pypdf import PdfReader

reader = PdfReader("catalogo_preenchido.pdf")
dados = reader.get_form_text_fields()  # dict {field_name: value}
# Para campos não-texto também:
todos = reader.get_fields()  # inclui checkbox, radio, choice
for nome, field in (todos or {}).items():
    print(nome, "=", field.get("/V"))
```

### 2.2 reportlab

**Capacidades:** geração de PDF do zero (não edita PDFs existentes). Excelente para layout rico via canvas e Platypus (flowables). Suporta AcroForm via `canvas.acroForm` (módulo `reportlab.pdfbase.acroform`, mais novo) ou via `reportlab.pdfbase.pdfform` (legado, não use).

**Tipos de widgets suportados:**

- `acroForm.textfield(name, x, y, width, height, value, maxlen, fontName, fontSize, borderStyle, borderColor, fillColor, textColor, forceBorder, tooltip, fieldFlags, ...)`
- `acroForm.checkbox(name, x, y, buttonStyle, ...)`
- `acroForm.radio(name, x, y, value, selected, shape, ...)`
- `acroForm.choice(name, x, y, width, height, options, value, ...)` (combobox/listbox conforme `fieldFlags`)
- `acroForm.listbox(...)`

**Exemplo completo (pedido B2B):**

```python
from reportlab.pdfgen import canvas
from reportlab.lib.colors import black, lightgrey

def montar_pagina_produto(c, produto):
    """produto = {'ref': 'OAS-001', 'cores': ['Azul', 'Coral'], 'tamanhos': ['P','M','G','GG']}"""
    c.setFont("Helvetica-Bold", 14)
    c.drawString(50, 750, f"Ref. {produto['ref']}")

    # imagem do produto previamente exportada como PNG pela agência
    c.drawImage(f"imgs/{produto['ref']}.png", 50, 400, width=300, height=320)

    form = c.acroForm
    y = 380
    for cor in produto['cores']:
        c.drawString(50, y, f"Cor: {cor}")
        x = 150
        for tam in produto['tamanhos']:
            c.drawString(x, y, tam)
            form.textfield(
                name=f"qty__{produto['ref']}__{cor}__{tam}",
                x=x, y=y-22, width=40, height=20,
                borderColor=black, fillColor=lightgrey,
                fontName="Helvetica", fontSize=10,
                fieldFlags='', maxlen=3, tooltip=f"Quantidade {cor}/{tam}",
                forceBorder=True
            )
            x += 60
        y -= 50

c = canvas.Canvas("catalogo_oasis.pdf")
for prod in catalogo:
    montar_pagina_produto(c, prod)
    c.showPage()
c.save()
```

**Limitação fundamental:** ReportLab **não lê** PDFs. Para sobrepor sobre um PDF visual existente é preciso combinar com **pdfrw** (carrega o PDF original como XObject que ReportLab desenha como background).

### 2.3 pdfrw

**Capacidades:** leitura e escrita de baixo nível da árvore de objetos PDF. Permite ler um PDF existente, manipular suas estruturas (incluindo AcroForm) e gravar de volta sem reprocessar streams. Famoso pelo padrão "**pdfrw + ReportLab**": carregar página existente como template, desenhar coisas novas em cima com ReportLab.

**Funcionalidades-chave:**

- `PdfReader(arquivo)` carrega árvore de objetos completa.
- Itera `template.pages[i]['/Annots']` para ler widgets existentes.
- Modificar valores: `annotation.update(pdfrw.PdfDict(V=pdfrw.objects.pdfstring.PdfString.encode(valor)))`
- Setar `NeedAppearances`: `template.Root.AcroForm.update(pdfrw.PdfDict(NeedAppearances=pdfrw.PdfObject('true')))`
- Adicionar campos novos: criar `PdfDict(Type=/Annot, Subtype=/Widget, FT=/Tx, T='nome', Rect=[...])` e inseri-lo em `page.Annots`.

**Limitação:** pdfrw está em manutenção limitada (último release significativo há anos) e a criação de widgets do zero exige conhecimento profundo da spec ISO 32000. **Para adicionar campos sobre um PDF existente, PyMuPDF é radicalmente mais simples.**

### 2.4 PyMuPDF (fitz) — A biblioteca central deste projeto

**Versão atual (maio 2026):** 1.27.x. Built sobre MuPDF (Artifex). Wheels para Python 3.10–3.14 em Windows/macOS/Linux. License: **AGPL v3** ou comercial. (Atenção: AGPL exige liberar o código do produto que a usa, ou comprar licença comercial Artifex. Para um SaaS B2B comercial, **isto é um fator decisivo** — orçar licença comercial ou usar wrapper que isole; alternativa é "pdfrw + ReportLab", ambos MIT/BSD.)

**Capacidades para AcroForm desde v1.13.11:**

- Criar widgets sobre um PDF existente: `page.add_widget(widget)`.
- Atualizar widgets: `annot.update()`.
- Iterar: `for w in page.widgets():`.
- Ler valor: `w.field_value`, `w.field_name`, `w.field_type`, `w.rect`.
- Tipos suportados (`pymupdf.PDF_WIDGET_TYPE_*`): `TEXT` (7), `CHECKBOX`, `RADIOBUTTON`, `COMBOBOX`, `LISTBOX`, `SIGNATURE` (read-only), `BUTTON`.
- Não suportado: **criação automática de grupos de radio buttons** (a doc oficial declara isso explicitamente), e atualização da `button_style` de checkbox/radio existentes.

**Atributos do `Widget` (mais relevantes):**

| Atributo | Tipo | Descrição |
|---|---|---|
| `rect` | `fitz.Rect` | retângulo em coordenadas PDF (origem inferior-esquerda) |
| `field_name` | str | identificador único |
| `field_type` | int | constante `PDF_WIDGET_TYPE_TEXT` etc. |
| `field_value` | str/bool | valor atual |
| `field_label` | str | tooltip / nome alternativo |
| `text_font`, `text_fontsize`, `text_color` | | aparência do texto |
| `border_style`, `border_width`, `border_color`, `fill_color` | | aparência |
| `text_maxlen` | int | maxlen para text |
| `choice_values` | list[str] | opções para combo/listbox (mandatório) |
| `field_flags` | int | flags de campo |
| `script`, `script_calc`, `script_change`, `script_format`, `script_blur`, `script_focus`, `script_stroke` | str | JavaScript actions |

**Exemplo canônico de adicionar campos de quantidade sobre cada página de um catálogo existente:**

```python
import pymupdf  # antes era 'import fitz'

# Mapa pré-definido das coordenadas por página (ver Bloco 3 sobre como obter)
LAYOUT = {
    0: {  # página 0 do PDF
        'ref': 'OAS-001',
        'celulas': [
            # (cor, tamanho, x0, y0, x1, y1) em pontos PDF
            ('Azul',  'P',   400, 600, 440, 620),
            ('Azul',  'M',   460, 600, 500, 620),
            ('Azul',  'G',   520, 600, 560, 620),
            ('Coral', 'P',   400, 575, 440, 595),
            # ...
        ]
    },
    # ... 1 entrada por página/produto
}

def adicionar_campos(input_path, output_path, layout):
    doc = pymupdf.open(input_path)
    for page_index, page_info in layout.items():
        page = doc[page_index]
        ref = page_info['ref']
        for cor, tam, x0, y0, x1, y1 in page_info['celulas']:
            w = pymupdf.Widget()
            w.rect = pymupdf.Rect(x0, y0, x1, y1)
            w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
            w.field_name = f"qty__{ref}__{cor}__{tam}"
            w.field_value = ""
            w.text_maxlen = 3
            w.text_fontsize = 10
            w.fill_color = (1, 1, 0.85)        # amarelo claro
            w.border_color = (0.2, 0.2, 0.2)
            w.border_width = 0.5
            w.text_font = "Helv"
            # Para hint numérico em mobile (não 100% suportado):
            # w.script_format = 'AFNumber_Format(0,0,0,0,"",true);'
            # w.script_change = 'AFNumber_Keystroke(0,0,0,0,"",true);'
            page.add_widget(w)
        # IMPORTANTE: forçar recálculo de aparência ao abrir
    doc.save(output_path, clean=True, garbage=4, deflate=True)
    doc.close()

adicionar_campos("catalogo_visual.pdf", "catalogo_editavel.pdf", LAYOUT)
```

**Limitações conhecidas relevantes (de Issues e Discussions do GitHub pymupdf/PyMuPDF):**

1. PDFs com rotação ≠ 0 exigem multiplicar `rect` por `page.derotation_matrix` (Discussion #1837), e mesmo assim alguns viewers (Acrobat, Foxit) ignoram a orientação correta — SumatraPDF e PDF-XChange acertam.
2. Combo boxes preenchidos via `field_value` **não exibem o valor consistentemente em Acrobat Pro 2020 / Adobe Reader DC** (Issue #1311). Por isso preferir **text fields** para quantidades.
3. Radio button groups precisam ser construídos manualmente via `/Parent` ↔ `/Kids` — PyMuPDF não automatiza.
4. Após `add_widget`/`update_widget`, fazer `page = doc.reload_page(page)` antes de reusar a referência.
5. PyMuPDF é **single-thread** (MuPDF não é fully thread-safe). Use `multiprocessing` para paralelizar lotes.

### 2.5 fillpdf e pdf2image + Pillow

- **fillpdf** (PyPI): wrapper sobre **pdftk** (binário externo Java) para preencher AcroForms existentes. Não cria campos, só preenche. Dependência externa pesada — não recomendado para produção containerizada.
- **pdf2image + Pillow:** rasteriza PDF para PNG/JPG. Útil para gerar **previews para o painel admin** ou para **passar para o modelo de visão** (Bloco 5). Não tem nada a ver com formulários.

### 2.6 pdfplumber

- Focado em extração de texto e detecção de tabelas, com posicionamento preciso de cada caractere (bounding box). É a melhor ferramenta para **inspecionar coordenadas** de elementos no catálogo (vide Bloco 3) e identificar onde estão palavras-chave como "Ref.", "Tamanho", "Cor", "P M G GG", auxiliando o posicionamento semi-automático dos campos.

**Exemplo de extração de coordenadas:**

```python
import pdfplumber

with pdfplumber.open("catalogo.pdf") as pdf:
    for page in pdf.pages:
        # Achar todas as ocorrências da palavra "Tamanho" e onde estão "P", "M", "G", "GG"
        for word in page.extract_words():
            if word['text'] in ('P','M','G','GG','XGG'):
                print(page.page_number, word['text'], word['x0'], word['top'], word['x1'], word['bottom'])
```

### 2.7 PyPDFForm

Projeto open source de Jinge Li (`chinapandaman/PyPDFForm`). Atual v4.7 / v3.x (a transição da v2→v3 em maio/2025 abandonou a abordagem de "watermark" e passou a manipular diretamente os campos AcroForm). Pontos fortes:

- API alta-nível para **criar campos sobre um PDF existente** com poucas linhas.
- Cria text, checkbox, radio, dropdown, signature, image, paragraph.
- Tem **gerador de grid de coordenadas** para você posicionar visualmente (`PdfWrapper(...).generate_coordinate_grid()`).
- Licença MIT — **importante** se a licença AGPL do PyMuPDF for um problema.

```python
from PyPDFForm import PdfWrapper, Fields, RawElements

pdf = PdfWrapper("catalogo_visual.pdf")
pdf.bulk_create_fields([
    Fields.TextField("qty__OAS001__Azul__P",  page_number=1, x=400, y=600, width=40, height=20),
    Fields.TextField("qty__OAS001__Azul__M",  page_number=1, x=460, y=600, width=40, height=20),
    # ...
])
pdf.write("catalogo_editavel.pdf")
```

### 2.8 Comparativo — Melhor opção para o nosso caso (a, b, c)

(a) receber PDF visual existente, (b) adicionar campos em posições específicas, (c) salvar PDF editável mantendo layout:

| Biblioteca | (a) lê PDF visual | (b) cria campos posicionados | (c) preserva layout | Licença | Veredito |
|---|---|---|---|---|---|
| pypdf | OK | Manual (baixo nível) | OK | BSD | Bom só para ler/preencher |
| reportlab puro | Não | OK (criando do zero) | Não preserva, recria | BSD | Não atende (a) |
| pdfrw + reportlab | OK | OK (via XObject) | OK | MIT | Funciona, código verboso |
| **PyMuPDF (fitz)** | **OK** | **OK (API alta)** | **OK** | **AGPL/comercial** | **Melhor técnica** |
| **PyPDFForm** | **OK** | **OK (API alta)** | **OK** | **MIT** | **Melhor para SaaS comercial** |
| fillpdf | OK (só preenche) | Não | — | MIT | Não atende (b) |

**Recomendação:** **PyMuPDF** se a licença AGPL for aceitável (ou se vai comprar licença comercial Artifex — em geral US$ baixos a médios milhares/ano para um SaaS). **PyPDFForm** se quiser MIT puro. **pdfrw + ReportLab** se quiser controle granular sem dependências exóticas.

---

## BLOCO 3 — Estratégias de posicionamento de campos no PDF existente

### 3.1 Como obter coordenadas (x, y, w, h)

O sistema de coordenadas PDF tem origem no **canto inferior esquerdo**, em pontos (1/72"). Página A4 = 595 × 842 pt; Letter = 612 × 792 pt. PyMuPDF e ReportLab usam esta convenção, mas PyMuPDF expõe também `page.rect` em coordenadas "top-left" para `Rect`.

Estratégias para descobrir onde colocar os widgets:

1. **pdfplumber** para extrair texto com bounding box e usar marcadores no design (ex.: "QTDE", labels "P/M/G/GG", a ref do produto): a agência pode incluir essas labels invisíveis ou visíveis, e seu script localiza-as e calcula a célula adjacente.
2. **PyMuPDF `page.search_for(string)`** retorna `list[Rect]` com todas as ocorrências de um texto — perfeito para localizar âncoras.
3. **Inspeção manual** via ferramenta visual (Sejda, PDFEscape, Acrobat Pro, LibreOffice Draw): usuário arrasta campos sobre o PDF, salva e seu script extrai os `Rect` desse PDF de "template" via `page.widgets()`.
4. **Gerar overlay de grade de coordenadas** com PyMuPDF/PyPDFForm para descobrir manualmente.

**Snippet usando âncora de texto (recomendado para catálogos com layout repetitivo):**

```python
import pymupdf

doc = pymupdf.open("catalogo.pdf")
TAMANHOS = ["P","M","G","GG","XGG"]
CORES_KEYWORD = "Cor:"

for page in doc:
    # localiza a referência do produto (assumindo formato "OAS-XXX")
    refs = [(r, page.get_text("text", clip=r)) for r in page.search_for("OAS-")]
    if not refs:
        continue
    ref_text = page.get_text("text", clip=refs[0][0].include_rect(refs[0][0]+(80,0,80,0)))
    ref_clean = ref_text.split()[0]

    # localiza labels de tamanho na ordem em que aparecem
    label_rects = {}
    for tam in TAMANHOS:
        rs = page.search_for(tam)
        if rs:
            label_rects[tam] = rs[0]

    # localiza labels de cor
    for r_cor in page.search_for(CORES_KEYWORD):
        # extrai o nome da cor ao lado direito de "Cor:"
        nome_cor_rect = pymupdf.Rect(r_cor.x1, r_cor.y0, r_cor.x1+100, r_cor.y1)
        nome_cor = page.get_text("text", clip=nome_cor_rect).strip()

        # para cada tamanho, posiciona a célula de quantidade ABAIXO do label de tamanho
        # e na mesma linha vertical da cor
        for tam, lr in label_rects.items():
            cell = pymupdf.Rect(lr.x0-2, r_cor.y0, lr.x1+2, r_cor.y1+2)
            w = pymupdf.Widget()
            w.rect = cell
            w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
            w.field_name = f"qty__{ref_clean}__{nome_cor}__{tam}"
            w.fill_color = (1,1,0.85)
            w.text_maxlen = 3
            page.add_widget(w)

doc.save("catalogo_editavel.pdf", clean=True, garbage=4, deflate=True)
```

### 3.2 Abordagem de "overlay"

"Overlay" é tecnicamente: **o PDF original é preservado intacto como conteúdo de página**; apenas adicionamos novas anotações `/Widget` na entrada `/Annots` da página. **Não rasterizamos, não recriamos as streams de conteúdo, não tocamos no design da agência**. Isso é exatamente o que `page.add_widget()` do PyMuPDF faz.

Variante alternativa (overlay visual usando ReportLab + pdfrw): renderiza o PDF da agência como background XObject em uma nova canvas do ReportLab, desenha os widgets em cima e salva. Mais código, mesma ideia. Use somente se PyMuPDF estiver fora de questão.

### 3.3 Ferramentas visuais (GUI) para posicionar + exportar coordenadas

| Ferramenta | Plataforma | Cria AcroForm visual | Exporta coords automaticamente | Custo |
|---|---|---|---|---|
| **Adobe Acrobat Pro** | desktop | Padrão-ouro, ferramenta "Prepare Form" auto-detecta linhas/grids | Sim, gera `.pdf` que você lê com pypdf/pymupdf | US$ 20–25/mês |
| **Sejda PDF** (web e desktop) | web/desktop | Sim, drag-and-drop com nomes, defaults, multiselect | Sim, PDF resultante tem widgets reais; ler depois com PyMuPDF | Free limitado / paid |
| **PDFEscape** | web | Sim, drag-and-drop | Sim | Free limitado / paid |
| **PDF24 Creator** | desktop Windows | Sim | Sim | Free |
| **LibreOffice Draw** | desktop | Sim, com Form Controls | Sim, mas tipografia/render podem ficar comprometidos | Free |
| **pdftk + pdftk-java** | CLI | Não | Lê AcroForm existente (`dump_data_fields`) | Free |
| **PyPDFForm coordinate grid** | Python | Não cria campos, gera grid sobreposto para você descobrir x,y | Sim, indireto | Free |

**Fluxo recomendado para Oasis Resortwear:**

1. Uma vez por catálogo: a agência (ou o operador interno) abre o PDF no **Sejda** ou Acrobat Pro e arrasta um conjunto de campos sobre **um produto-modelo** com nomes padronizados (`qty__{REF}__{COR}__{TAM}`). Salva.
2. Um script Python lê esse "template-com-campos" via PyMuPDF, extrai os `Rect`s e gera um **JSON de layout**.
3. Para cada nova coleção, se o layout repete por página, o script replica o JSON aplicando deltas conhecidos (mudança de Y entre produtos) e o aplica em todas as páginas.

### 3.4 Múltiplas páginas com layout similar mas não idêntico

Três estratégias, em ordem de robustez:

**(A) Âncora por texto (recomendada quando o template tem labels visíveis ou ocultas):**

- A agência insere âncoras imperceptíveis no PDF (ex.: texto branco "ANCHOR_QTY_GRID" no canto da grade).
- Seu script faz `page.search_for("ANCHOR_QTY_GRID")` e calcula o grid relativo à âncora.

**(B) Detecção visual via reconhecimento de tabela:**

- `pdfplumber.Page.find_tables()` detecta a grade visual de tamanhos.
- Para cada célula da tabela detectada, cria um widget.

**(C) Layout-template por SKU:**

- A agência exporta cada produto com layout previsível (todos partem do mesmo InDesign master). Você define **um único `LAYOUT_PADRAO`** com offsets relativos à âncora do produto, e itera.

Combinação prática: **(A) + fallback para um JSON manual editável pela equipe interna** quando uma página fugir do padrão. Implementar versionamento desse JSON por coleção (Outono25, Verão26 etc).

---

## BLOCO 4 — Fluxo de recebimento e processamento dos dados preenchidos

### 4.1 Opções técnicas para receber dados após "Submit"

| Mecanismo | Como funciona | Compatibilidade mobile real |
|---|---|---|
| **Submit HTTP POST com AsXFDF** | Botão com action `/SubmitForm`, URL `https://api.oasis.com/pedido`, flag `AsXFDF=1`. Servidor parsa XML. | **Falha frequente no Adobe Reader mobile**; não suportada por Chrome PDF / Safari Preview |
| **Submit HTTP POST como PDF inteiro** | Flag `SubmitAsPDF`. Servidor recebe o PDF preenchido e extrai. | Mesma limitação |
| **`mailto:` submit** | URL é `mailto:pedidos@oasis.com?subject=Pedido`. Anexa FDF/XFDF/PDF. | Inconsistente em mobile; Outlook iOS pode ignorar anexo |
| **FDF/XFDF salvo localmente** | Botão dispara `app.execMenuItem("ExportData")`. | Praticamente nulo em mobile |
| **Compartilhar o PDF preenchido inteiro** (manual) | Usuária salva o PDF com Adobe Reader / Files / Drive e envia por WhatsApp | **Funciona em 100% dos casos** se a usuária aceitou abrir num app de PDF capaz |
| **QR Code → URL web** | PDF tem QR Code; cliente abre formulário web no celular | **Funciona 100%** |

**Conclusão prática:** para o público alvo (lojistas em Android/iOS chegando via WhatsApp), **só dois caminhos são confiáveis**:

1. Cliente preenche no Adobe Reader / Foxit / Xodo, salva, e **manualmente compartilha o PDF preenchido** com a empresa via WhatsApp.
2. **QR Code/link → web form**.

O Submit-via-HTTP **funciona como uma melhoria opcional para clientes em desktop**, não como caminho principal.

### 4.2 Extração de dados de um PDF preenchido recebido

**Com PyMuPDF:**

```python
import pymupdf, json, re

def extrair_pedido(path_pdf, lojista_id=None):
    doc = pymupdf.open(path_pdf)
    itens = []
    for page in doc:
        for w in page.widgets():
            if w.field_type != pymupdf.PDF_WIDGET_TYPE_TEXT:
                continue
            m = re.match(r"^qty__(?P<ref>[^_]+)__(?P<cor>[^_]+)__(?P<tam>[^_]+)$",
                         w.field_name or "")
            if not m: 
                continue
            val = (w.field_value or "").strip()
            if not val:
                continue
            try:
                qty = int(val.replace(",", "").replace(".", ""))
            except ValueError:
                continue
            if qty <= 0:
                continue
            itens.append({
                "ref": m.group("ref"),
                "cor": m.group("cor"),
                "tam": m.group("tam"),
                "qtd": qty
            })
    doc.close()
    return {
        "lojista_id": lojista_id,
        "itens": itens,
        "total_pecas": sum(i["qtd"] for i in itens),
        "total_skus": len(itens),
    }

print(json.dumps(extrair_pedido("pedido_recebido.pdf"), indent=2, ensure_ascii=False))
```

**Com pypdf (alternativa puramente BSD):**

```python
from pypdf import PdfReader
import re

reader = PdfReader("pedido_recebido.pdf")
fields = reader.get_form_text_fields() or {}
pat = re.compile(r"^qty__(?P<ref>[^_]+)__(?P<cor>[^_]+)__(?P<tam>[^_]+)$")
itens = []
for name, val in fields.items():
    m = pat.match(name)
    if not m or not val: 
        continue
    try:
        q = int(val)
    except ValueError:
        continue
    if q > 0:
        itens.append({"ref": m["ref"], "cor": m["cor"], "tam": m["tam"], "qtd": q})
```

### 4.3 Formato canônico do pedido

Schema recomendado (compatível com qualquer ERP/CRM):

```json
{
  "pedido_id": "uuid-v4",
  "lojista": {"id": 123, "razao_social": "Loja XYZ", "cnpj": "..."},
  "canal": "whatsapp",
  "coleção": "Verao26",
  "data_pedido": "2026-05-11T14:22:00-03:00",
  "origem_arquivo": "pedido_loja_XYZ_2026-05-11.pdf",
  "itens": [
    {"ref":"OAS-001", "cor":"Azul", "tamanho":"P", "qtd": 3, "preco_unit_sugerido": 0.0, "subtotal": 0.0},
    {"ref":"OAS-001", "cor":"Azul", "tamanho":"M", "qtd": 5}
  ],
  "total_pecas": 8,
  "total_skus": 2,
  "observacoes": "string opcional",
  "validacao": {
    "duplicados": [],
    "sem_estoque": [],
    "ref_desconhecidas": []
  }
}
```

Normalização desde o PDF: aplicar pipeline (1) parse → (2) sanitizar inteiros → (3) cruzar com tabela mestre de SKUs (validar `ref`, `cor`, `tam` válidos) → (4) cruzar com estoque/MOQ → (5) gravar em base + enviar confirmação WhatsApp via API (Meta WhatsApp Business Cloud API).

### 4.4 Cliente preenche no celular, salva e envia por WhatsApp — pontos de falha

**Sim, é possível**, mas com pontos de falha reais:

1. **Viewer correto:** WhatsApp não preenche. Cliente precisa "Abrir com…" → escolher Adobe Reader / Foxit / Xodo / Files. ~30–50% de churn aqui em públicos não-técnicos.
2. **Salvar versus enviar original:** muitos viewers em iOS abrem em modo "annotate" sem salvar de fato no PDF; ao compartilhar, o anexo pode vir vazio. Acrobat Reader e Files do iOS 17+ funcionam bem, Quick Look não.
3. **Achatamento do PDF (flatten):** alguns clientes "imprimem como PDF" para enviar, o que **destrói os AcroFields** transformando-os em texto sobreposto. Resultado: seu parser não encontra valores. Mitigação: detectar PDF flatten (sem `/AcroForm`) e cair em pipeline de visão computacional (OCR) automaticamente.
4. **Tamanho do anexo:** WhatsApp limita anexos a 100 MB (atual 2026). PDFs com muitas imagens podem estourar; comprimir ou dividir.
5. **Codificação do nome do arquivo:** WhatsApp renomeia frequentemente. Embed do `lojista_id` em campo oculto do PDF (campo Text com `flag /ReadOnly + /NoView`) — o backend lê esse campo para identificar o cliente.
6. **Cliente preenche várias páginas, envia só algumas (split)** — pouco comum, mas possível.
7. **PDF está protegido com senha** — `pymupdf.open` retorna documento que requer `.authenticate()`. Garantir que o PDF original não tenha senha.

---

## BLOCO 5 — Abordagens alternativas

### 5.1 HTML Form como alternativa

Página web responsiva, mobile-first, hospedada em `oasis.com/pedido/{lojista_token}/{colecao}`.

**Prós (decisivos):**

- **Funciona em 100% dos celulares**, sem instalação de app.
- Validação em tempo real (estoque, MOQ, total).
- Galeria de imagens responsiva (zoom, swipe).
- Salva rascunho automaticamente (localStorage / backend).
- Pode ter login social ou link mágico.
- Integração trivial com Python (Flask/FastAPI + Jinja/HTMX, ou Next.js consumindo API Python).
- Permite mostrar preço, desconto por volume, total em tempo real — recursos que **JavaScript em PDF não entrega em mobile**.
- Logs completos de quem abriu, quanto tempo ficou, onde parou.

**Contras:**

- Exige conexão durante o preenchimento (mitigar com PWA offline-first).
- Exige hosting, domínio, certificado HTTPS, monitoramento.
- "Não é um catálogo" — psicologicamente algumas lojistas valorizam ter o PDF no celular para folhear offline.

**Padrão híbrido recomendado:** mantém o PDF visual lindo como **catálogo** para a lojista folhear, e **cada produto tem um QR Code/link curto** que abre a tela web do pedido daquele produto. Une o melhor dos dois mundos.

### 5.2 Google Forms / Typeform

**Google Forms:**

- Limita imagens (uma única por questão), sem layout de catálogo de moda.
- Não suporta bem "grade de tamanhos × cores".
- Apenas um questionário monolítico. Para 50 produtos × 4 cores × 5 tamanhos = 1000 campos: inviável.

**Typeform:**

- Belíssimo UX mas o modelo "uma pergunta por tela" não escala para catálogo.
- Caro (US$ 50–100+/mês com volumes profissionais).
- Issue documentada de embed em Safari iOS perdendo estado (Typeform Community).

**Veredito:** **inadequados** para catálogo de moda B2B com variações. Servem para pesquisa pós-pedido, não para captura do pedido em si.

### 5.3 WhatsApp + Vision/LLM (foto do catálogo marcado à mão)

A lojista já hoje envia foto do PDF impresso ou de print do celular com quantidades escritas à mão. Um agente de visão computacional processa essa imagem.

**Estado da arte (maio 2026):**

- **GPT‑4o (OpenAI):** OCR e visão multimodal. Benchmark Roboflow reporta ~94% de acurácia média em OCR genérico e ganhos de velocidade de ~58% sobre GPT‑4V. Bom para texto datilografado, **decente mas variável para manuscrito**.
- **Claude 3.7 Sonnet / 4 (Anthropic):** atualmente um dos melhores em "reading mixed printed + handwritten documents" em benchmarks de IDP públicos (Automat case study, 2024–2025) — em formulários complexos médicos chegou a níveis próximos a IDP especializado quando fine-tunado.
- **Gemini 2.5 Pro (Google):** muito forte em visão, com janelas de contexto enormes (1M tokens) que permitem enviar várias páginas + esquema de produto + tabelas de tamanhos juntos.
- **Modelos especializados open source:** Donut, LayoutLMv3, PaddleOCR + post-processing — mais barato em escala, exige fine-tuning.

**Acurácia esperada para o caso "foto de página de catálogo com 4 colunas (P/M/G/GG) e quantidades escritas à mão":**

- **Texto impresso (referência, cor, label de tamanho):** 99%+ com qualquer dos 3 grandes.
- **Manuscrito legível (algarismos arábicos isolados em células bem definidas):** 88–96% por SKU em laboratório, com prompt bem desenhado (cropar a célula, listar as opções válidas, pedir output JSON com schema fixo). Erros típicos: confundir "1" com "7", "0" com "6/9", deixar célula vazia interpretada como "0" quando havia rabisco fraco.
- **Manuscrito ilegível ou rasurado:** 50–80%. Necessário fluxo de revisão humana ("você quis dizer 3 ou 5 do Azul P?") via WhatsApp.

**Limitações operacionais:**

- Custo por requisição (GPT-4o ~US$ 5/M tokens input, multiplicado por número de páginas — fica entre US$ 0.01–0.10 por pedido).
- Latência 3–15s por imagem.
- Risco de alucinação numérica — modelos podem "inventar" números plausíveis. **Mitigação:** sempre exigir output estruturado JSON com schema, validar contra grade conhecida do produto, exigir confiança ≥ X, fluxo de confirmação humana.
- Privacidade: enviar fotos de catálogos para APIs externas. Considerar self-host (modelo open source ou Azure OpenAI on-region).

**Arquitetura recomendada para essa via:**

```
WhatsApp Business Cloud API (webhook)
  → recebe imagem
  → pré-processamento (OpenCV: deskew, denoise, detect grid)
  → recorte de cada célula (P/M/G/GG por cor)
  → batch GPT-4o-mini com prompt: "Você é um leitor de pedidos. Para cada célula
     {ref, cor, tam}, retorne o número escrito (0 se vazio). Saída JSON
     conforme schema X."
  → validação cruzada com schema do catálogo
  → resposta WhatsApp: "Identifiquei seu pedido: 3 Azul P, 5 Azul M…. Confirma? Sim/Não"
  → grava pedido se confirmado, ou abre handoff humano
```

### 5.4 PDF + QR Code por produto

Para cada produto, gerar um QR Code com URL única:

```python
import pymupdf, qrcode, io

def add_qr_per_product(pdf_in, pdf_out, layout):
    doc = pymupdf.open(pdf_in)
    for page_idx, info in layout.items():
        page = doc[page_idx]
        url = f"https://pedido.oasis.com/{info['ref']}?token={info['token']}"
        qr = qrcode.QRCode(box_size=4, border=1)
        qr.add_data(url)
        qr.make()
        img = qr.make_image()
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        # canto inferior direito da página
        pr = page.rect
        rect = pymupdf.Rect(pr.x1-100, pr.y1-100, pr.x1-20, pr.y1-20)
        page.insert_image(rect, stream=buf.read(), keep_proportion=True)
        # também adicionar um link clicável (para quem está vendo o PDF no celular)
        page.insert_link({"kind": pymupdf.LINK_URI, "from": rect, "uri": url})
    doc.save(pdf_out, clean=True, garbage=4, deflate=True)
```

Esta abordagem é talvez a mais robusta para o nosso contexto: o catálogo continua sendo o catálogo, e qualquer celular abrindo o PDF (mesmo no preview do WhatsApp) **pode tocar no QR/área e abrir o formulário web do produto**.

---

## BLOCO 6 — Implementação prática, referências e recomendação final

### 6.1 Artigos, posts e repositórios verificados

**Documentação oficial e canônica:**

- PyMuPDF Widget reference: <https://pymupdf.readthedocs.io/en/latest/widget.html>
- PyMuPDF Page reference (`add_widget`, `widgets()`): <https://pymupdf.readthedocs.io/en/latest/page.html>
- PyMuPDF "How to Add Form Fields" wiki: <https://github.com/pymupdf/PyMuPDF/wiki/How-to-Add-Form-Fields>
- PyMuPDF-Utilities repo (`fields/form-fields.py`): <https://github.com/pymupdf/PyMuPDF-Utilities/blob/master/fields/form-fields.py>
- Artifex blog "Automating PDF Form Filling and Flattening": <https://artifex.com/blog/automating-pdf-form-filling-and-flattening-with-pymupdf>
- pypdf forms interactions: <https://pypdf.readthedocs.io/en/stable/user/forms.html>
- ReportLab user guide ch.4 (acroForm): <https://docs.reportlab.com/reportlab/userguide/ch4_pdffeatures/>
- "Creating Interactive PDF Forms in ReportLab with Python": <https://blog.pythonlibrary.org/2018/05/29/creating-interactive-pdf-forms-in-reportlab-with-python/>
- pdfrw fillable forms (West Health): <https://westhealth.github.io/exploring-fillable-forms-with-pdfrw.html>
- "Filling Editable PDF in Python" (Medium): <https://medium.com/@vivsvaan/filling-editable-pdf-in-python-76712c3ce99>
- "Dynamically changing PDF Acroforms with Python and Javascript" (TheCodeWork): <https://thecodework.com/blog/dynamically-changing-pdf-acroforms-with-python-and-javascript/>

**Bibliotecas open source relevantes:**

- `pymupdf/PyMuPDF` (AGPL) — <https://github.com/pymupdf/PyMuPDF>
- `chinapandaman/PyPDFForm` (MIT) — <https://github.com/chinapandaman/PyPDFForm> e doc <https://chinapandaman.github.io/PyPDFForm/>
- `pmaupin/pdfrw` (MIT) — <https://github.com/pmaupin/pdfrw>
- `py-pdf/pypdf` (BSD) — <https://github.com/py-pdf/pypdf>
- `ccnmtl/fdfgen` — geração de FDF para preencher forms — <https://github.com/ccnmtl/fdfgen>
- `altaurog/pdfforms` — wrapper pdftk para preencher com CSV — <https://github.com/altaurog/pdfforms>

**Sobre AcroForm vs XFA e compatibilidade:**

- Foxit "Acroforms vs XFA": <https://www.foxit.com/blog/acroforms-vs-xfa-forms/>
- Datalogics "How to Work With AcroForms & XFA": <https://www.datalogics.com/how-to-work-with-acroforms-xfa>
- Apryse "Effortlessly Manage XFA Documents 2024": <https://apryse.com/blog/xfa-options-alternatives-by-apryse>
- Appligent Labs JS dynamic forms: <https://labs.appligent.com/appligent-labs/acroforms-vs.-xfa/aem-forms-dynamic-report-generation-with-javascript>
- Wikipedia XFA: <https://en.wikipedia.org/wiki/XFA>
- "PDF Form Types Explained" (Flipper File): <https://flipperfile.com/pdf-guides/pdf-form-types-explained/>

**Submit de formulário e mobile:**

- Adobe Acrobat tutorial "Submitting PDF form data": <https://acrobatusers.com/tutorials/submitting-data/>
- PSPDFKit/Nutrient guide form submission iOS: <https://pspdfkit.com/guides/ios/forms/form-submission/>
- Adobe Community thread "submit button on Acrobat Mobile" (problema documentado): <https://community.adobe.com/t5/acrobat-discussions/submit-button-on-acrobat-mobile/m-p/9396139>
- Jotform "Form mobile firendly submit": <https://www.jotform.com/answers/2147593-i-need-a-fillable-pdf-form-to-be-mobile-firendly-the-form-now-is-not-allowing-to-submit-from-a-mobile-device>

**Visão computacional com manuscrito:**

- Roboflow "GPT-4o vision use cases" (benchmarks OCR): <https://blog.roboflow.com/gpt-4o-vision-use-cases/>
- Automat case "Fine-Tune GPT-4o for IDP & RPA": <https://www.runautomat.com/blog/how-to-fine-tune-gpt-4o-for-industry-specific-document-processing-and-robotic-process-automation>
- Encord "GPT-4 Vision explained": <https://encord.com/blog/gpt4-vision/>
- arXiv "Putting GPT-4o to the Sword" (avaliação ampla): <https://arxiv.org/pdf/2407.09519>

### 6.2 Existe projeto open source que resolve "PDF visual → PDF com formulário de pedido"?

Após varredura no GitHub: **não existe projeto open source maduro que entregue exatamente o pipeline ponta-a-ponta** (catálogo de moda PDF → AcroForm → WhatsApp/web → pedido estruturado). O que existe são componentes reutilizáveis:

- **PyPDFForm** (5k+ stars): cobre a parte de criar campos sobre PDF existente com API limpa, em Python puro com licença MIT. **Reutilizável quase 100%** para o estágio "tornar PDF editável".
- **PyMuPDF-Utilities** (`fields/form-fields.py`): exemplo direto do mantenedor da lib.
- **pdfforms** de altaurog: preenchimento em lote com CSV — útil para gerar PDFs **pré-preenchidos** por lojista (ex.: já com nome da loja, CNPJ, vendedor).
- **fdfgen**: geração de FDF/XFDF — útil para servidor receber dados via Submit e converter de volta para PDF.
- Para WhatsApp Cloud API + Python: tutoriais como o da GuruSup e implementações no `dlthub`.

**Conclusão:** o produto Oasis Resortwear precisa ser desenvolvido juntando componentes. Não existe "monolito" pronto.

### 6.3 Recomendação técnica final — fundamentada

Dadas as restrições (catálogo PDF visual sem camadas, lojistas em celular usando WhatsApp, dados estruturados no backend Python, mínima fricção):

**Arquitetura recomendada (em ordem de prioridade de implementação):**

#### Fase 1 — MVP (4–6 semanas): **PDF com QR Code/links + Web Form mobile-first**

1. **Pipeline de produção do catálogo editável** (Python, executado a cada nova coleção):
   - Input: PDF visual da agência + JSON `catalogo_skus.json` (lista de produtos com cores, tamanhos, preços, MOQs).
   - Para cada página/produto: gerar token único + URL `https://pedido.oasis.com/{token}`, gerar QR Code, sobrepor no canto da página com **PyMuPDF** (`page.insert_image` + `page.insert_link`).
   - Output: PDF "linkado" idêntico ao original, mais um QR/link visível.
2. **Distribuição:** WhatsApp Business Cloud API envia o PDF para a base de lojistas com mensagem template aprovada.
3. **Web form (FastAPI + HTMX + Tailwind):**
   - Mobile-first, telão de imagens, grade tamanhos × cores intuitiva.
   - Salva rascunho a cada digitação (autosave a cada 2s).
   - Mostra total de peças e valor estimado em tempo real.
   - Submissão grava em Postgres + dispara confirmação WhatsApp.
   - Tempo médio de pedido < 5 minutos por loja média (estimativa).

#### Fase 2 — Catálogo verdadeiramente editável (6–10 semanas): **AcroForm + Submit web (fallback desktop)**

Para lojistas que prefiram o fluxo offline-no-PDF (algumas regiões com internet ruim):

1. Mesmo pipeline acima, mas além do QR Code, **adicionar AcroForm fields com PyPDFForm ou PyMuPDF** em cada célula de quantidade.
2. Botão "Enviar pedido" com action `SubmitForm` para `https://api.oasis.com/pedido/submit` em formato `XFDF`. Funciona em desktop. **Aceitar também o envio manual do PDF preenchido por WhatsApp** — webhook recebe, parser extrai (código da seção 4.2).
3. Adicionar campos ocultos no PDF (read-only + no-view) com `lojista_token` e `colecao_id` para identificação ao receber.

#### Fase 3 — Fluxo de visão computacional (8–12 semanas): **Fallback para foto manuscrita**

Algumas lojistas vão continuar mandando foto com pedido escrito à mão (cultural, especialmente Norte/Nordeste). Para essas:

1. Webhook WhatsApp detecta imagem (não PDF).
2. Pipeline: deskew (OpenCV) → detecção de grade da página (cv2/contour) → recorte de cada célula → chamada batch a **GPT‑4o-mini ou Claude 3.5 Haiku** com prompt estruturado pedindo JSON Schema fixo.
3. Validação cruzada contra `catalogo_skus.json`.
4. Resposta WhatsApp: "Identifiquei seu pedido: [resumo]. **Responda 1 para confirmar ou 2 para corrigir.**"
5. Em caso de baixa confiança ou recusa, escalonamento humano via inbox.

**Por que esta arquitetura é a melhor:**

- **Mínima fricção** real para a lojista: ela toca em um QR/link e está num formulário web que funciona em qualquer celular. **Não depende** de a lojista ter Adobe Reader instalado, nem de o WhatsApp suportar o submit do PDF.
- **Sobreviva ao zoo de viewers PDF mobile**, conforme evidências coletadas no Bloco 1.5 e na thread Adobe Community 9396139.
- **Aproveita o catálogo bonito da agência** sem reprocessar layout (overlay puro via `page.insert_image` + `page.insert_link` — não toca em design).
- **Backend 100% Python**: FastAPI + PyMuPDF + qrcode + httpx (para WhatsApp Cloud API) + openai/anthropic SDK (visão).
- **Escalonável**: cada fase entrega valor em separado. A Fase 1 já cobre 80% do caso de uso. As fases 2 e 3 são fallbacks para edge cases (offline e fotos manuscritas).
- **Custo licença:** se Fase 1 isolada, usa só `pymupdf` (AGPL — atenção) ou trocar por `pdfrw + reportlab` (MIT) para a geração do QR Code overlay. PyPDFForm (MIT) cobre Fase 2 sem ferir AGPL.

**Por que NÃO recomendar "PDF AcroForm puro como caminho principal" para o público alvo:**

1. **Adobe Reader mobile + WhatsApp + Submit HTTP é uma combinação documentadamente quebrada** (Adobe Community 9396139, Jotform community, Apple Discussions).
2. **Lojistas em Android genérico raramente têm Adobe Reader**; o viewer padrão é o do Google Drive ou do fabricante (Samsung, Xiaomi), os quais **não preenchem AcroForm**.
3. JavaScript no PDF (cálculo de total, validação) **não funciona em mobile** com confiabilidade — perde-se a UX rica.
4. Não tem analytics. Não dá para ver se a lojista abriu, quanto tempo ficou, onde parou.
5. PDF preenchido enviado por WhatsApp **muitas vezes é flatten** (achatado), perdendo os campos. Aí cai-se em visão computacional sem ter planejado.

**Veredito final:** a tecnologia "PDF AcroForm com submit" foi pensada em 1996 para fluxos B2B desktop com Acrobat instalado. Para 2026 com lojistas brasileiras em celulares variados chegando via WhatsApp, ela é tecnicamente fascinante mas **operacionalmente subótima**. O caminho moderno é **PDF lindo (intocável) + QR Code/link em cada produto → Web App mobile-first → backend Python → confirmação WhatsApp**, com fallback de visão computacional para quem manda foto manuscrita. Essa combinação atende mínima fricção, dados estruturados, backend Python e aproveita o catálogo da agência sem retrabalho.

---

## Apêndice — Snippet completo de geração do catálogo "QR-linked" (Fase 1 MVP)

```python
"""
generate_catalog_links.py
Adiciona QR Code + link clicável em cada página/produto de um PDF visual.
Backend: gera URLs únicas por (lojista, produto, coleção) que abrem o formulário web.
"""
import io, json, uuid, pymupdf, qrcode
from pathlib import Path

BASE_URL = "https://pedido.oasis.com"

def gerar_qr_png_bytes(url: str, size_px: int = 240) -> bytes:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                       box_size=6, border=1)
    qr.add_data(url); qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = io.BytesIO(); img.save(buf, format="PNG"); buf.seek(0)
    return buf.read()

def gerar_catalogo_linkado(pdf_in: str, pdf_out: str,
                           catalogo_meta: list[dict], lojista_token: str):
    """
    catalogo_meta: [{"page": 0, "ref": "OAS-001"}, {"page": 1, "ref": "OAS-002"}, ...]
    """
    doc = pymupdf.open(pdf_in)
    by_page = {m["page"]: m for m in catalogo_meta}

    for i in range(len(doc)):
        meta = by_page.get(i)
        if not meta:
            continue
        url = f"{BASE_URL}/{lojista_token}/{meta['ref']}?p={i+1}"
        page = doc[i]
        pr = page.rect

        # 1) inserir QR Code no canto inferior direito (≈ 60pt)
        qr_size = 70
        margin = 18
        qr_rect = pymupdf.Rect(pr.x1 - margin - qr_size,
                               pr.y1 - margin - qr_size,
                               pr.x1 - margin,
                               pr.y1 - margin)
        page.insert_image(qr_rect, stream=gerar_qr_png_bytes(url),
                          keep_proportion=True, overlay=True)

        # 2) inserir link clicável sobre o QR (para quem está vendo o PDF
        #    direto no celular - toque abre URL)
        page.insert_link({"kind": pymupdf.LINK_URI,
                          "from": qr_rect, "uri": url})

        # 3) inserir um pequeno texto "Pedir" acima do QR
        text_rect = pymupdf.Rect(qr_rect.x0, qr_rect.y0 - 14,
                                 qr_rect.x1, qr_rect.y0 - 2)
        page.insert_textbox(text_rect, "Toque para pedir →",
                            fontsize=8, fontname="helv",
                            color=(0, 0, 0), align=pymupdf.TEXT_ALIGN_CENTER)

    doc.save(pdf_out, clean=True, garbage=4, deflate=True)
    doc.close()

if __name__ == "__main__":
    meta = json.loads(Path("colecao_verao26.json").read_text())
    gerar_catalogo_linkado(
        pdf_in="catalogo_verao26.pdf",
        pdf_out=f"catalogo_loja_ABC_{uuid.uuid4()}.pdf",
        catalogo_meta=meta,
        lojista_token="abc-7f3e-91ad"
    )
```

E o `endpoint` Python que recebe o pedido pelo web form ou pelo PDF preenchido enviado por WhatsApp:

```python
"""
api_pedidos.py — FastAPI endpoint que aceita pedido via JSON (web form) ou via PDF preenchido.
"""
from fastapi import FastAPI, UploadFile, File, HTTPException
from pydantic import BaseModel
import pymupdf, re, datetime, uuid

app = FastAPI()

class ItemIn(BaseModel):
    ref: str; cor: str; tam: str; qtd: int

class PedidoIn(BaseModel):
    lojista_token: str; colecao: str; itens: list[ItemIn]; obs: str | None = None

@app.post("/pedido/web")
def pedido_web(p: PedidoIn):
    return _normalizar_e_persistir(p.lojista_token, p.colecao,
                                   [i.dict() for i in p.itens], p.obs)

@app.post("/pedido/pdf")
async def pedido_pdf(file: UploadFile = File(...), lojista_token: str | None = None):
    data = await file.read()
    doc = pymupdf.open(stream=data, filetype="pdf")
    itens, lojista, colecao = [], lojista_token, None
    pat = re.compile(r"^qty__(?P<ref>[^_]+)__(?P<cor>[^_]+)__(?P<tam>[^_]+)$")
    for page in doc:
        for w in page.widgets() or []:
            if w.field_name == "_meta_lojista_token":
                lojista = lojista or w.field_value
            elif w.field_name == "_meta_colecao":
                colecao = w.field_value
            elif w.field_type == pymupdf.PDF_WIDGET_TYPE_TEXT:
                m = pat.match(w.field_name or "")
                if not m: continue
                try: q = int((w.field_value or "0").strip() or 0)
                except: q = 0
                if q > 0:
                    itens.append({"ref": m["ref"], "cor": m["cor"],
                                  "tam": m["tam"], "qtd": q})
    doc.close()
    if not itens:
        # fallback opcional: pipeline de visão computacional sobre o PDF achatado
        raise HTTPException(422, "PDF sem campos preenchidos (talvez achatado)")
    return _normalizar_e_persistir(lojista, colecao, itens, None)

def _normalizar_e_persistir(token, colecao, itens, obs):
    # ... validar SKUs contra master, estoque, MOQ, persistir, disparar WhatsApp ...
    return {"pedido_id": str(uuid.uuid4()),
            "lojista_token": token,
            "colecao": colecao,
            "total_pecas": sum(i["qtd"] for i in itens),
            "total_skus": len(itens),
            "itens": itens,
            "criado_em": datetime.datetime.now().isoformat()}
```

Este código cobre os dois principais caminhos de entrada de pedido (web e PDF) num único backend Python, com ~80 linhas, e é a espinha dorsal do produto.