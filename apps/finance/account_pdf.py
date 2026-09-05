import io
import os

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle)

SECRETARY_NAME = "Wiktor Włochacz"
SECRETARY_PHONE = "737 796 515"
ORG_NAME = "Teatr Scena Główna Handlowa"

_FONT = "Helvetica"
_FONT_B = "Helvetica-Bold"
_FONTS_DONE = False

NAVY = colors.HexColor("#1f2d4d")
GREY = colors.HexColor("#666666")
LIGHT = colors.HexColor("#eef0f4")


def _register_fonts():
    """Rejestruje czcionkę TTF z obsługą polskich znaków (z fallbackami)."""
    global _FONT, _FONT_B, _FONTS_DONE
    if _FONTS_DONE:
        return
    candidates = [
        ("/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf"),
        ("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
         "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"),
        ("/System/Library/Fonts/Supplemental/Arial.ttf",
         "/System/Library/Fonts/Supplemental/Arial Bold.ttf"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ]
    for reg, bold in candidates:
        if os.path.exists(reg):
            pdfmetrics.registerFont(TTFont("Body", reg))
            pdfmetrics.registerFont(
                TTFont("Body-Bold", bold if os.path.exists(bold) else reg))
            _FONT, _FONT_B = "Body", "Body-Bold"
            break
    _FONTS_DONE = True


def fmt_zl(value):
    """1234.5 -> '1 234,50 zł' ; ujemne z minusem."""
    neg = value < -0.005
    s = f"{abs(value):,.2f}".replace(",", " ").replace(".", ",")
    return f"{'−' if neg else ''}{s} zł"


def fmt_date(iso):
    if not iso:
        return "—"
    return f"{iso[8:10]}.{iso[5:7]}.{iso[0:4]}"


def _styles():
    return {
        "org": ParagraphStyle("org", fontName=_FONT_B, fontSize=10,
                              textColor=NAVY, spaceAfter=2),
        "sub": ParagraphStyle("sub", fontName=_FONT, fontSize=8.5,
                              textColor=GREY, spaceAfter=14),
        "h1": ParagraphStyle("h1", fontName=_FONT_B, fontSize=18,
                             textColor=NAVY, spaceAfter=3),
        "meta": ParagraphStyle("meta", fontName=_FONT, fontSize=8.5,
                               textColor=GREY, spaceAfter=12),
        "h2": ParagraphStyle("h2", fontName=_FONT_B, fontSize=11,
                             textColor=NAVY, spaceBefore=14, spaceAfter=6),
        "body": ParagraphStyle("body", fontName=_FONT, fontSize=10,
                               leading=15, spaceAfter=8),
        "note": ParagraphStyle("note", fontName=_FONT, fontSize=8.5,
                               leading=12.5, textColor=colors.HexColor("#333333")),
        "cell": ParagraphStyle("cell", fontName=_FONT, fontSize=8.5, leading=11),
        "cellb": ParagraphStyle("cellb", fontName=_FONT_B, fontSize=8.5,
                                leading=11),
        "cellw": ParagraphStyle("cellw", fontName=_FONT_B, fontSize=9,
                                leading=11, textColor=colors.white),
        "title": ParagraphStyle("title", fontName=_FONT_B, fontSize=20,
                                textColor=NAVY, alignment=TA_CENTER,
                                spaceBefore=6, spaceAfter=4),
        "right": ParagraphStyle("right", fontName=_FONT, fontSize=9,
                                textColor=GREY, alignment=TA_RIGHT,
                                spaceAfter=16),
        "foot": ParagraphStyle("foot", fontName=_FONT, fontSize=7.5,
                               textColor=GREY, alignment=TA_CENTER,
                               spaceBefore=20),
    }


def _records_table(rows, st):
    head = [Paragraph(h, st["cellb"]) for h in ("Data", "Opis", "Typ", "Kwota")]
    data = [head]
    for r in rows:
        amt = r["kwota"] if r["kierunek"] == "Osoba → Budżet" else -r["kwota"]
        data.append([
            Paragraph(fmt_date(r["date"]), st["cell"]),
            Paragraph(r["opis"] or "—", st["cell"]),
            Paragraph(r["typ"] or "—", st["cell"]),
            Paragraph(fmt_zl(amt), st["cell"]),
        ])
    t = Table(data, colWidths=[22 * mm, 98 * mm, 24 * mm, 26 * mm],
              repeatRows=1)
    style = [
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), _FONT_B),
        ("ALIGN", (3, 0), (3, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
    ]
    for i in range(1, len(data)):
        if i % 2 == 0:
            style.append(("BACKGROUND", (0, i), (-1, i), LIGHT))
    t.setStyle(TableStyle(style))
    return t


def _bal_word(value):
    if abs(value) < 0.005:
        return "uregulowane"
    if value > 0:
        return "zaległość wobec budżetu"
    return "należność od budżetu (nadpłata / zwrot)"


def _summary_table(account, st):
    bd, ba = account["balance_due"], account["balance_all"]
    t = Table([
        [Paragraph("Bilans zobowiązań zapadłych", st["cellb"]),
         Paragraph(f"{fmt_zl(bd)}  ({_bal_word(bd)})", st["cell"])],
        [Paragraph("Bilans z uwzgl. zobowiązań planowanych", st["cellb"]),
         Paragraph(f"{fmt_zl(ba)}  ({_bal_word(ba)})", st["cell"])],
    ], colWidths=[78 * mm, 92 * mm])
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (0, -1), LIGHT),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
    ]))
    return t


def _history_flowables(account, st):
    out = [Paragraph(f"Zobowiązania ({len(account['zobowiazania'])})", st["h2"])]
    if account["zobowiazania"]:
        out.append(_records_table(account["zobowiazania"], st))
    else:
        out.append(Paragraph("Brak.", st["body"]))
    out.append(Paragraph(
        f"Transakcje — wpłaty i zwroty ({len(account['transakcje'])})", st["h2"]))
    if account["transakcje"]:
        out.append(_records_table(account["transakcje"], st))
    else:
        out.append(Paragraph("Brak.", st["body"]))
    return out


def _build(story):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=20 * mm,
                            bottomMargin=18 * mm, leftMargin=20 * mm,
                            rightMargin=20 * mm)
    doc.build(story)
    return buf.getvalue()


def render_history_pdf(account, generated_str):
    _register_fonts()
    st = _styles()
    story = [
        Paragraph(ORG_NAME, st["org"]),
        Paragraph("Budżet nieoficjalny — historia konta", st["sub"]),
        Paragraph(account["name"], st["h1"]),
        Paragraph(f"Wygenerowano: {generated_str}", st["meta"]),
        _summary_table(account, st),
    ]
    story += _history_flowables(account, st)
    story.append(Paragraph(
        "Kwota dodatnia = należność wobec budżetu (Osoba → Budżet); "
        "ujemna = należność od budżetu (Budżet → Osoba). "
        "Dokument wygenerowany automatycznie.", st["foot"]))
    return _build(story)


def render_wezwanie_pdf(account, generated_str):
    _register_fonts()
    st = _styles()
    owed = [u for u in account["unpaid_due"] if u["kierunek"] == "Osoba → Budżet"]
    credit = [u for u in account["unpaid_due"]
              if u["kierunek"] == "Budżet → Osoba"]
    total = account["balance_due"]

    story = [
        Paragraph(ORG_NAME, st["org"]),
        Paragraph("Budżet nieoficjalny — sekretariat", st["sub"]),
        Paragraph("WEZWANIE DO UREGULOWANIA SKŁADEK", st["title"]),
        Paragraph(f"Stan na: {generated_str}", st["right"]),
        Paragraph(f"<b>Dotyczy:</b> {account['name']}", st["body"]),
        Paragraph(
            "Zgodnie ze stanem rozliczeń Budżetu nieoficjalnego Teatru "
            "Scena Główna Handlowa, na koncie składkowym widnieje "
            "nieuregulowana zaległość. Prosimy o jej pilne uregulowanie. "
            "Poniżej zestawienie zaległych pozycji, objaśnienie wyliczenia "
            "oraz pełna historia konta.", st["body"]),
    ]

    head = [Paragraph(h, st["cellb"]) for h in
            ("Termin", "Tytuł", "Typ", "Kwota")]
    data = [head]
    for u in owed:
        data.append([
            Paragraph(fmt_date(u["date"]), st["cell"]),
            Paragraph(u["opis"] or "—", st["cell"]),
            Paragraph(u["typ"] or "—", st["cell"]),
            Paragraph(fmt_zl(u["kwota"]), st["cell"]),
        ])
    for u in credit:
        data.append([
            Paragraph(fmt_date(u["date"]), st["cell"]),
            Paragraph(f"(zwrot należny od budżetu) {u['opis']}", st["cell"]),
            Paragraph(u["typ"] or "—", st["cell"]),
            Paragraph("−" + fmt_zl(u["kwota"]), st["cell"]),
        ])
    data.append([Paragraph("", st["cell"]),
                 Paragraph("KWOTA DO ZAPŁATY", st["cellw"]),
                 Paragraph("", st["cell"]),
                 Paragraph(fmt_zl(total), st["cellw"])])
    t = Table(data, colWidths=[24 * mm, 96 * mm, 24 * mm, 26 * mm], repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BACKGROUND", (0, -1), (-1, -1), NAVY),
        ("ALIGN", (3, 0), (3, -1), "RIGHT"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("LINEBELOW", (0, 0), (-1, -2), 0.4, colors.HexColor("#cccccc")),
    ]))
    story.append(t)
    story.append(Spacer(1, 12))

    note = Table([[Paragraph(
        "<b>Jak czytamy to wezwanie.</b> Wpłaty zaliczane są na poczet "
        "zobowiązań w kolejności terminów. Jeżeli któreś składki zostały "
        "opłacone z wyprzedzeniem, nadwyżka pomniejsza najbliższe nieopłacone "
        "zobowiązanie — dlatego kwota przy danej pozycji bywa niższa niż jej "
        "pełny nominał. Pełne kwoty wszystkich zobowiązań oraz wszystkie "
        "wpłaty znajdują się w historii konta poniżej. W razie jakichkolwiek "
        "wątpliwości co do rozliczenia prosimy o wyjaśnienie sprawy "
        "z sekretarzem.", st["note"])]], colWidths=[170 * mm])
    note.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
        ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
    ]))
    story.append(note)
    story.append(Spacer(1, 12))

    pay = Table([[Paragraph(
        f"<b>Dane do wpłaty</b><br/>"
        f"Odbiorca: {SECRETARY_NAME} (sekretarz)<br/>"
        f"Telefon (przelew na telefon / BLIK): <b>{SECRETARY_PHONE}</b><br/>"
        f"Tytuł przelewu: składki — {account['name']}",
        st["body"])]], colWidths=[170 * mm])
    pay.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), LIGHT),
        ("BOX", (0, 0), (-1, -1), 0.5, NAVY),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
    ]))
    story.append(pay)

    story.append(Paragraph("Pełna historia konta", st["h2"]))
    story.append(_summary_table(account, st))
    story += _history_flowables(account, st)

    story.append(Paragraph(
        "Pseudodokument wygenerowany automatycznie na podstawie stanu "
        "rozliczeń z chwili uruchomienia.", st["foot"]))
    return _build(story)
