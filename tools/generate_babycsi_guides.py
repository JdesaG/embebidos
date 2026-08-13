#!/usr/bin/env python3
"""Generate the BabyCSI end-user poster and server operator manual."""

import re
from pathlib import Path

from reportlab.graphics import renderPDF
from reportlab.graphics.barcode import qr
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    KeepTogether,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


ROOT = Path(__file__).resolve().parents[1]
PDF_DIR = ROOT / "output" / "pdf"
POSTER_PDF = PDF_DIR / "AFICHE_USUARIO_BABYCSI.pdf"
SERVER_PDF = PDF_DIR / "MANUAL_SERVIDOR_BABYCSI.pdf"
SERVER_MD = ROOT / "MANUAL_SERVIDOR.md"
PROJECT_PDF = PDF_DIR / "ESTADO_Y_PLAN_CIERRE_BABYCSI.pdf"
PROJECT_MD = ROOT / "PLAN_CIERRE_PROYECTO.md"

NAVY = colors.HexColor("#102A43")
BLUE = colors.HexColor("#2368A2")
TEAL = colors.HexColor("#12A59A")
MINT = colors.HexColor("#DFF7F3")
ORANGE = colors.HexColor("#FFB45C")
CREAM = colors.HexColor("#F7F4EC")
INK = colors.HexColor("#17324D")
MUTED = colors.HexColor("#58738C")
WHITE = colors.white


def wrap_lines(text, font_name, font_size, max_width):
    words = text.split()
    lines = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if not current or stringWidth(candidate, font_name, font_size) <= max_width:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def draw_wrapped(c, text, x, y, max_width, font="Helvetica", size=10,
                 leading=13, color=INK):
    c.setFont(font, size)
    c.setFillColor(color)
    for line in wrap_lines(text, font, size, max_width):
        c.drawString(x, y, line)
        y -= leading
    return y


def draw_step(c, number, title, body, y, page_width):
    margin = 18 * mm
    height = 25 * mm
    c.setFillColor(WHITE)
    c.roundRect(margin, y - height, page_width - 2 * margin, height,
                5 * mm, fill=1, stroke=0)
    c.setFillColor(TEAL)
    c.circle(margin + 12 * mm, y - height / 2, 7 * mm, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 15)
    c.drawCentredString(margin + 12 * mm, y - height / 2 - 5, str(number))
    text_x = margin + 24 * mm
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 11.5)
    c.drawString(text_x, y - 8 * mm, title)
    draw_wrapped(c, body, text_x, y - 14 * mm,
                 page_width - margin - text_x - 7 * mm,
                 size=8.7, leading=10.5, color=MUTED)
    return y - height - 3.2 * mm


def generate_poster():
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    width, height = A4
    c = canvas.Canvas(str(POSTER_PDF), pagesize=A4)
    c.setTitle("Afiche de usuario BabyCSI")
    c.setAuthor("Proyecto BabyCSI")

    c.setFillColor(CREAM)
    c.rect(0, 0, width, height, fill=1, stroke=0)
    c.setFillColor(NAVY)
    c.rect(0, height - 42 * mm, width, 42 * mm, fill=1, stroke=0)
    c.setFillColor(TEAL)
    c.circle(width - 26 * mm, height - 21 * mm, 13 * mm, fill=1, stroke=0)
    c.setFillColor(WHITE)
    c.circle(width - 30 * mm, height - 21 * mm, 2.5 * mm, fill=1, stroke=0)
    c.circle(width - 22 * mm, height - 21 * mm, 2.5 * mm, fill=1, stroke=0)
    c.setLineWidth(2)
    c.arc(width - 32 * mm, height - 29 * mm, width - 20 * mm,
          height - 21 * mm, 200, 140)

    c.setFillColor(ORANGE)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(18 * mm, height - 13 * mm, "GUÍA RÁPIDA PARA EL USUARIO")
    c.setFillColor(WHITE)
    c.setFont("Helvetica-Bold", 27)
    c.drawString(18 * mm, height - 25 * mm, "BABYCSI")
    c.setFont("Helvetica", 12)
    c.drawString(18 * mm, height - 34 * mm,
                 "Conecta, configura y controla el sistema desde tu teléfono")

    c.setFillColor(ORANGE)
    c.roundRect(18 * mm, height - 54 * mm, width - 36 * mm, 8 * mm,
                4 * mm, fill=1, stroke=0)
    c.setFillColor(NAVY)
    c.setFont("Helvetica-Bold", 9.5)
    c.drawCentredString(width / 2, height - 51.2 * mm,
                       "ANTES: el responsable debe dejar la computadora y el servidor encendidos")

    y = height - 59 * mm
    steps = [
        ("Enciende BabyCSI",
         "Conecta la power bank y espera aproximadamente un minuto."),
        ("Solo la primera vez o al cambiar de red",
         "En el teléfono, conéctate a BabyCSI-XXXX. Clave: BabyCSI26."),
        ("Guarda el Wi-Fi",
         "Abre 192.168.4.1. Escribe la red principal de 2.4 GHz y, si deseas, una red de respaldo. Pulsa Guardar y conectar."),
        ("Vuelve a la red habitual",
         "Espera el reinicio y conecta el teléfono a la misma red Wi-Fi que usa la computadora."),
        ("Abre el panel",
         "Visita http://Jandonys-MacBook-Air.local:8080/usuario o escanea el código QR de abajo."),
        ("Controla y confirma",
         "Ajusta luz, color, brillo o modo energético. Espera el mensaje Confirmado antes de dar el cambio por aplicado."),
    ]
    for index, (title, body) in enumerate(steps, 1):
        y = draw_step(c, index, title, body, y, width)

    bottom_y = 13 * mm
    box_height = 34 * mm
    c.setFillColor(NAVY)
    c.roundRect(18 * mm, bottom_y, width - 36 * mm, box_height,
                5 * mm, fill=1, stroke=0)
    c.setFillColor(ORANGE)
    c.setFont("Helvetica-Bold", 11)
    c.drawString(25 * mm, bottom_y + 24 * mm, "SI ALGO NO FUNCIONA")
    c.setFillColor(WHITE)
    c.setFont("Helvetica", 8.5)
    help_lines = [
        "1. Revisa que teléfono y computadora estén en la misma red.",
        "2. Reinicia primero el gateway y después la emisora.",
        "3. Si no abre el panel, solicita al responsable la IP actual.",
    ]
    for offset, line in enumerate(help_lines):
        c.drawString(25 * mm, bottom_y + (17 - offset * 5) * mm, line)

    c.setFillColor(WHITE)
    c.roundRect(width - 52 * mm, bottom_y + 2.5 * mm, 29 * mm, 29 * mm,
                2 * mm, fill=1, stroke=0)
    widget = qr.QrCodeWidget(
        "http://Jandonys-MacBook-Air.local:8080/usuario"
    )
    bounds = widget.getBounds()
    qr_size = 25 * mm
    drawing = Drawing(qr_size, qr_size,
                      transform=[qr_size / (bounds[2] - bounds[0]), 0, 0,
                                 qr_size / (bounds[3] - bounds[1]), 0, 0])
    drawing.add(widget)
    renderPDF.draw(drawing, c, width - 50 * mm, bottom_y + 4.5 * mm)

    c.setFillColor(MUTED)
    c.setFont("Helvetica", 6.8)
    c.drawCentredString(width / 2, 6 * mm,
                       "Prototipo académico - no sustituye un dispositivo médico")
    c.showPage()
    c.save()


def manual_styles():
    sample = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "ManualTitle", parent=sample["Title"], fontName="Helvetica-Bold",
            fontSize=25, leading=29, textColor=WHITE, alignment=TA_LEFT,
            spaceAfter=8,
        ),
        "subtitle": ParagraphStyle(
            "ManualSubtitle", parent=sample["BodyText"], fontName="Helvetica",
            fontSize=10.5, leading=15, textColor=colors.HexColor("#DCEBFA"),
        ),
        "h1": ParagraphStyle(
            "H1", parent=sample["Heading1"], fontName="Helvetica-Bold",
            fontSize=19, leading=23, textColor=NAVY, spaceBefore=6,
            spaceAfter=10,
        ),
        "h2": ParagraphStyle(
            "H2", parent=sample["Heading2"], fontName="Helvetica-Bold",
            fontSize=14, leading=18, textColor=BLUE, spaceBefore=13,
            spaceAfter=7,
        ),
        "h3": ParagraphStyle(
            "H3", parent=sample["Heading3"], fontName="Helvetica-Bold",
            fontSize=11.5, leading=15, textColor=TEAL, spaceBefore=10,
            spaceAfter=5,
        ),
        "body": ParagraphStyle(
            "Body", parent=sample["BodyText"], fontName="Helvetica",
            fontSize=9.5, leading=14, textColor=INK, spaceAfter=7,
        ),
        "list": ParagraphStyle(
            "List", parent=sample["BodyText"], fontName="Helvetica",
            fontSize=9.2, leading=13, textColor=INK,
        ),
        "list_item": ParagraphStyle(
            "ListItem", parent=sample["BodyText"], fontName="Helvetica",
            fontSize=9.2, leading=13, textColor=INK, leftIndent=18,
            bulletIndent=0, spaceAfter=5,
        ),
        "code": ParagraphStyle(
            "Code", parent=sample["Code"], fontName="Courier", fontSize=6.8,
            leading=9.5, textColor=WHITE, leftIndent=0, rightIndent=0,
        ),
    }


def inline_markup(text):
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = text.replace("&lt;br/&gt;", "<br/>")
    text = re.sub(r"`([^`]+)`", r"<font name='Courier'>\1</font>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    return text


def markdown_flowables(path, styles):
    lines = path.read_text(encoding="utf-8").splitlines()
    story = []
    paragraph = []
    list_items = []
    list_kind = None
    code_lines = []
    in_code = False

    def flush_paragraph():
        if paragraph:
            story.append(Paragraph(inline_markup(" ".join(paragraph)), styles["body"]))
            paragraph.clear()

    def flush_list():
        nonlocal list_kind
        if list_items:
            for index, item in enumerate(list_items, 1):
                marker = str(index) if list_kind == "number" else "•"
                story.append(Paragraph(
                    inline_markup(item), styles["list_item"], bulletText=marker
                ))
            story.append(Spacer(1, 2))
            list_items.clear()
            list_kind = None

    for raw in lines:
        line = raw.rstrip()
        if line.startswith("```"):
            flush_paragraph()
            flush_list()
            if in_code:
                code = Preformatted(
                    "\n".join(code_lines), styles["code"],
                    maxLineLength=88, splitChars="/ ",
                )
                code_table = Table([[code]], colWidths=[159 * mm])
                code_table.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), NAVY),
                    ("BOX", (0, 0), (-1, -1), 0, NAVY),
                    ("LEFTPADDING", (0, 0), (-1, -1), 8),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ]))
                story.append(code_table)
                story.append(Spacer(1, 7))
                code_lines.clear()
            in_code = not in_code
            continue
        if in_code:
            code_lines.append(line)
            continue
        if not line:
            flush_paragraph()
            flush_list()
            continue
        if line.startswith("# "):
            flush_paragraph()
            flush_list()
            story.append(Paragraph(inline_markup(line[2:]), styles["h1"]))
            continue
        if line.startswith("## "):
            flush_paragraph()
            flush_list()
            story.append(Paragraph(inline_markup(line[3:]), styles["h2"]))
            continue
        if line.startswith("### "):
            flush_paragraph()
            flush_list()
            story.append(Paragraph(inline_markup(line[4:]), styles["h3"]))
            continue
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        number_match = re.match(r"^\d+\.\s+(.*)$", stripped)
        bullet_match = re.match(r"^-\s+(.*)$", stripped)
        if number_match or bullet_match:
            flush_paragraph()
            kind = "number" if number_match else "bullet"
            item_text = (number_match or bullet_match).group(1)
            if indent and list_items:
                list_items[-1] += f"<br/>- {item_text}"
                continue
            if list_kind and list_kind != kind:
                flush_list()
            list_kind = kind
            list_items.append(item_text)
            continue
        if indent and list_items:
            list_items[-1] += " " + stripped
            continue
        paragraph.append(line.strip())

    flush_paragraph()
    flush_list()
    return story


def manual_page(canvas_obj, doc):
    canvas_obj.saveState()
    width, height = A4
    canvas_obj.setFillColor(NAVY)
    canvas_obj.rect(0, height - 22 * mm, width, 22 * mm, fill=1, stroke=0)
    canvas_obj.setFillColor(TEAL)
    canvas_obj.rect(0, height - 24 * mm, width, 2 * mm, fill=1, stroke=0)
    canvas_obj.setFillColor(WHITE)
    canvas_obj.setFont("Helvetica-Bold", 15)
    canvas_obj.drawString(18 * mm, height - 10 * mm, "SERVIDOR BABYCSI")
    canvas_obj.setFillColor(colors.HexColor("#DCEBFA"))
    canvas_obj.setFont("Helvetica", 7.8)
    canvas_obj.drawString(
        18 * mm, height - 16 * mm,
        "Preparación, arranque, comprobación y cierre antes de entregar el sistema.",
    )
    canvas_obj.setFillColor(MUTED)
    canvas_obj.setFont("Helvetica", 7.5)
    canvas_obj.drawString(18 * mm, 11 * mm, "BabyCSI - Manual del servidor")
    canvas_obj.drawRightString(width - 18 * mm, 11 * mm, f"Pagina {doc.page}")
    canvas_obj.restoreState()


def project_page(canvas_obj, doc):
    canvas_obj.saveState()
    width, height = A4
    canvas_obj.setFillColor(NAVY)
    canvas_obj.rect(0, height - 22 * mm, width, 22 * mm, fill=1, stroke=0)
    canvas_obj.setFillColor(ORANGE)
    canvas_obj.rect(0, height - 24 * mm, width, 2 * mm, fill=1, stroke=0)
    canvas_obj.setFillColor(WHITE)
    canvas_obj.setFont("Helvetica-Bold", 14)
    canvas_obj.drawString(18 * mm, height - 10 * mm,
                          "ESTADO Y PLAN DE CIERRE - BABYCSI")
    canvas_obj.setFillColor(colors.HexColor("#DCEBFA"))
    canvas_obj.setFont("Helvetica", 7.8)
    canvas_obj.drawString(
        18 * mm, height - 16 * mm,
        "Bloqueos, responsables, integración, contingencias y criterios de aceptación.",
    )
    canvas_obj.setFillColor(MUTED)
    canvas_obj.setFont("Helvetica", 7.5)
    canvas_obj.drawString(18 * mm, 11 * mm, "Proyecto BabyCSI")
    canvas_obj.drawRightString(width - 18 * mm, 11 * mm,
                               f"Página {doc.page}")
    canvas_obj.restoreState()


def generate_server_manual():
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    styles = manual_styles()
    doc = SimpleDocTemplate(
        str(SERVER_PDF), pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm,
        topMargin=29 * mm, bottomMargin=19 * mm,
        title="Manual del responsable del servidor BabyCSI",
        author="Proyecto BabyCSI",
    )
    story = []
    markdown_story = markdown_flowables(SERVER_MD, styles)
    if markdown_story and isinstance(markdown_story[0], Paragraph):
        markdown_story = markdown_story[1:]
    story.extend(markdown_story)
    doc.build(story, onFirstPage=manual_page, onLaterPages=manual_page)


def generate_project_plan():
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    styles = manual_styles()
    doc = SimpleDocTemplate(
        str(PROJECT_PDF), pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm,
        topMargin=29 * mm, bottomMargin=19 * mm,
        title="Estado y plan de cierre del proyecto BabyCSI",
        author="Proyecto BabyCSI",
    )
    story = markdown_flowables(PROJECT_MD, styles)
    if story and isinstance(story[0], Paragraph):
        story = story[1:]
    doc.build(story, onFirstPage=project_page, onLaterPages=project_page)


def main():
    generate_poster()
    generate_server_manual()
    generate_project_plan()
    print(POSTER_PDF)
    print(SERVER_PDF)
    print(PROJECT_PDF)


if __name__ == "__main__":
    main()
