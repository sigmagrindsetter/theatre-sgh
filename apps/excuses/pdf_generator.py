import io
import os
from datetime import date
from pathlib import Path

from reportlab.lib.pagesizes import A4
from reportlab.lib.enums import TA_CENTER, TA_RIGHT, TA_LEFT, TA_JUSTIFY
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from pdfrw import PdfReader as PdfrwReader
from pdfrw.buildxobj import pagexobj
from pdfrw.toreportlab import makerl

from declension import (
    detect_gender,
    genitive_full_name,
    decline_lecturer_accusative,
    format_reason_instrumental,
)
from config import PRESIDENT_NAME, PROREKTOR_LINE1, PROREKTOR_LINE2, DEFAULT_REASON

TEMPLATE_PDF = Path(__file__).parent / "template.pdf"

_MONTHS_GEN = {
    1: "stycznia", 2: "lutego", 3: "marca", 4: "kwietnia",
    5: "maja", 6: "czerwca", 7: "lipca", 8: "sierpnia",
    9: "września", 10: "października", 11: "listopada", 12: "grudnia",
}

_FONT_REGULAR = None
_FONT_BOLD = None


def _register_fonts():
    global _FONT_REGULAR, _FONT_BOLD
    if _FONT_REGULAR:
        return
    candidates = [
        ("/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf"),
        ("/usr/share/fonts/truetype/liberation2/LiberationSerif-Regular.ttf",
         "/usr/share/fonts/truetype/liberation2/LiberationSerif-Bold.ttf"),
        ("/System/Library/Fonts/Supplemental/Times New Roman.ttf",
         "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf"),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSerif.ttf",
         "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Bold.ttf"),
    ]
    for regular, bold in candidates:
        if os.path.exists(regular):
            pdfmetrics.registerFont(TTFont("Serif", regular))
            bold_path = bold if os.path.exists(bold) else regular
            pdfmetrics.registerFont(TTFont("SerifBold", bold_path))
            _FONT_REGULAR = "Serif"
            _FONT_BOLD = "SerifBold"
            return
    raise RuntimeError("No serif TTF font found.")


def format_polish_date(dt) -> str:
    return f"{dt.day} {_MONTHS_GEN[dt.month]} {dt.year}"


def _esc(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _draw(c, text, style, x, y, width, max_h=200):
    p = Paragraph(_esc(text), style)
    _, h = p.wrap(width, max_h)
    p.drawOn(c, x, y - h)
    return h


def generate_pdf(data: dict) -> bytes:
    _register_fonts()

    first, *rest = data["name"].split(None, 1)
    last = rest[0] if rest else ""
    gender = detect_gender(first)

    pan_pani = "Pana" if gender == "m" else "Pani"
    name_gen = genitive_full_name(first, last, gender)
    lecturer_acc = decline_lecturer_accusative(data["lecturer"])

    class_date_str = format_polish_date(data["date_start"].date())
    today_str = format_polish_date(date.today())
    time_start = data["date_start"].strftime("%-H:%M")
    time_end = data["date_end"].strftime("%-H:%M")

    reason_raw = (data.get("reason") or "").strip()
    reason_instr = format_reason_instrumental(reason_raw) if reason_raw else DEFAULT_REASON

    date_line = f"Warszawa, {today_str} r."
    title = "Usprawiedliwienie"
    body = (
        f"Zwracam się z uprzejmą prośbą o usprawiedliwienie nieobecności "
        f"{pan_pani} {name_gen} (nr albumu {data['album']}) na zajęciach "
        f"z przedmiotu {data['subject'].strip()}, prowadzonych przez {lecturer_acc} "
        f"w godzinach {time_start}\u2013{time_end} {class_date_str} r."
    )
    reason_line = f"Nieobecność była spowodowana {reason_instr}."

    PAGE_W, PAGE_H = A4
    LEFT = 70
    RIGHT = PAGE_W - 70
    FULL_W = RIGHT - LEFT

    tmpl_reader = PdfrwReader(str(TEMPLATE_PDF))
    tmpl_xobj = pagexobj(tmpl_reader.pages[0])

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)

    # Reference measurements (from the signed Truchel document):
    #   Logo top ≈ y 797   Logo bottom ≈ y 688   (height ≈ 109pt)
    #   "Prorektor" ≈ y 668    Footer ≈ y 59
    #
    # Template unscaled:
    #   Logo ≈ y 620-710 (90pt)   "Prorektor" ≈ y 600   Footer ≈ y 150
    #
    # Transform: scale 1.21× anchored at logo top (140, 710) + shift up 87pt
    # This maps:  logo → y 688-797,  "Prorektor" → y 664,  footer → y ~57
    SCALE = 1.21
    AX, AY = 140, 710  # anchor = logo top
    SHIFT_Y = 87        # upward shift

    dx = AX * (1 - SCALE)           # -29.4
    dy = AY * (1 - SCALE) + SHIFT_Y  # -149.1 + 87 = -62.1

    c.saveState()
    c.translate(dx, dy)
    c.scale(SCALE, SCALE)
    c.doForm(makerl(c, tmpl_xobj))
    c.restoreState()

    c.setFillColorRGB(1, 1, 1)
    c.rect(0, 0, PAGE_W, 655, fill=1, stroke=0)
    c.rect(280, 655, PAGE_W - 280, 190, fill=1, stroke=0)

    c.setFont(_FONT_REGULAR, 7)
    c.setFillColor("#00778a")
    c.drawString(105, 63, "www.sgh.waw.pl")
    c.setFillColor("#333333")
    c.setFont(_FONT_REGULAR, 7)
    c.drawString(192, 63,
                 "Szkoła Główna Handlowa w Warszawie, "
                 "al. Niepodległości 162, 02-554 Warszawa")
    c.drawString(192, 54,
                 "tel.: +48 22 564 98 26, rektorat@sgh.waw.pl")

    s_date = ParagraphStyle("d", fontName=_FONT_REGULAR, fontSize=12,
                            alignment=TA_RIGHT, leading=16)
    s_title = ParagraphStyle("t", fontName=_FONT_BOLD, fontSize=14,
                             alignment=TA_CENTER, leading=20)
    s_body = ParagraphStyle("b", fontName=_FONT_REGULAR, fontSize=12,
                            alignment=TA_JUSTIFY, leading=18)
    s_right = ParagraphStyle("r", fontName=_FONT_REGULAR, fontSize=12,
                             alignment=TA_RIGHT, leading=16)
    s_sig = ParagraphStyle("s", fontName=_FONT_REGULAR, fontSize=12,
                           alignment=TA_LEFT, leading=16)
    s_sig_b = ParagraphStyle("sb", fontName=_FONT_BOLD, fontSize=12,
                             alignment=TA_LEFT, leading=16)
    s_sig_r = ParagraphStyle("sr", fontName=_FONT_REGULAR, fontSize=12,
                             alignment=TA_RIGHT, leading=16)
    s_sig_br = ParagraphStyle("sbr", fontName=_FONT_BOLD, fontSize=12,
                              alignment=TA_RIGHT, leading=16)

    y = 635

    y -= _draw(c, date_line, s_date, LEFT, y, FULL_W) + 30

    y -= _draw(c, title, s_title, LEFT, y, FULL_W) + 25

    y -= _draw(c, body, s_body, LEFT, y, FULL_W, 400) + 8

    y -= _draw(c, reason_line, s_body, LEFT, y, FULL_W, 200) + 28

    y -= _draw(c, "Z poważaniem", s_right, LEFT, y, FULL_W) + 95

    y -= _draw(c, PROREKTOR_LINE1, s_sig_br, LEFT, y, FULL_W)
    y -= _draw(c, PROREKTOR_LINE2, s_sig_r, LEFT, y, FULL_W) + 65

    y -= _draw(c, PRESIDENT_NAME, s_sig_b, LEFT, y, FULL_W)
    _draw(c, "Prezes Teatru SGH", s_sig, LEFT, y, FULL_W)

    c.save()
    return buf.getvalue()
