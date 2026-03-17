#!/usr/bin/env python3
"""
Screenplay PDF generator.

Fetches screenplay content from the Notion 'Scenariusz do edycji' page,
generates a PDF in standard screenplay format, uploads to Google Drive,
and replaces the file block on the Notion parent page.
"""

import sys
import os
import tempfile
import textwrap
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from shared.auth import NotionAuth
from shared.change_check import check_page_changed, set_cached_time
from config import SCREENPLAY_PAGE_ID, PARENT_PAGE_ID, PDF_FILENAME

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


# ── Page layout (US Letter, standard screenplay format) ──────────────────
PAGE_W, PAGE_H = letter  # 612 x 792 points
MARGIN_L = 108   # 1.5 in
MARGIN_R = 72    # 1.0 in
MARGIN_T = 72    # 1.0 in
MARGIN_B = 72    # 1.0 in

FONT_SIZE = 12
LINE_H = 12      # single-spaced
CHAR_W = 7.2     # Courier 12pt character width

# Horizontal positions and widths (in characters)
ACTION_X = MARGIN_L
ACTION_CHARS = int((PAGE_W - MARGIN_L - MARGIN_R) / CHAR_W)  # ~60

DIALOGUE_X = int(2.5 * 72)  # 180pt from left edge
DIALOGUE_CHARS = 38

CHARACTER_CENTER_X = int(3.7 * 72)  # 266pt — standard screenplay character cue position

TOP_Y = PAGE_H - MARGIN_T
BOT_Y = MARGIN_B


# ── Font setup ───────────────────────────────────────────────────────────

def setup_fonts():
    """Register a TTF monospace font for Polish character support."""
    candidates = [
        # macOS
        ("/System/Library/Fonts/Supplemental/Courier New.ttf",
         "/System/Library/Fonts/Supplemental/Courier New Bold.ttf",
         "/System/Library/Fonts/Supplemental/Courier New Italic.ttf"),
        # Linux (fonts-liberation)
        ("/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationMono-Italic.ttf"),
    ]

    for regular, bold, italic in candidates:
        if os.path.exists(regular):
            pdfmetrics.registerFont(TTFont("SP", regular))
            if os.path.exists(bold):
                pdfmetrics.registerFont(TTFont("SP-Bold", bold))
            if os.path.exists(italic):
                pdfmetrics.registerFont(TTFont("SP-Italic", italic))
            print(f"  Font: {Path(regular).stem}")
            return "SP"

    print("  Warning: no TTF monospace font found, falling back to built-in Courier")
    return "Courier"


# ── Notion block fetching ────────────────────────────────────────────────

def fetch_all_blocks(notion, page_id):
    """Fetch all blocks from a page with pagination."""
    blocks = []
    cursor = None
    while True:
        kwargs = {"block_id": page_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        result = notion.blocks.children.list(**kwargs)
        blocks.extend(result["results"])
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")
    return blocks


def get_text(rich_texts):
    return "".join(t["plain_text"] for t in rich_texts)


def is_all_italic(rich_texts):
    return rich_texts and all(
        t.get("annotations", {}).get("italic", False) for t in rich_texts
    )


def split_by_italic(rich_texts):
    """Split rich text into groups by italic formatting."""
    groups = []
    current_italic = None
    current_parts = []

    for t in rich_texts:
        italic = t.get("annotations", {}).get("italic", False)
        if italic != current_italic and current_parts:
            groups.append((current_italic, "".join(current_parts)))
            current_parts = []
        current_italic = italic
        current_parts.append(t["plain_text"])

    if current_parts:
        groups.append((current_italic, "".join(current_parts)))
    return groups


# ── Element extraction ───────────────────────────────────────────────────

def extract_elements(blocks):
    """Convert Notion blocks to typed screenplay elements.

    Element types: TITLE, TITLE_LINE, SCENE_HEADING, CHARACTER,
                   DIALOGUE, ACTION, SCENE_BREAK
    """
    elements = []
    state = "title"  # title | normal | dialogue

    for block in blocks:
        btype = block["type"]
        rich_texts = block.get(btype, {}).get("rich_text", [])

        if btype == "heading_1":
            elements.append(("TITLE", get_text(rich_texts)))

        elif btype == "heading_2":
            elements.append(("SCENE_HEADING", get_text(rich_texts).upper()))
            state = "normal"

        elif btype == "heading_3":
            elements.append(("CHARACTER", get_text(rich_texts).upper()))
            state = "dialogue"

        elif btype == "divider":
            elements.append(("SCENE_BREAK", ""))
            state = "normal"

        elif btype == "paragraph":
            if not rich_texts:
                continue
            text = get_text(rich_texts).strip()
            if not text:
                continue

            if state == "title":
                elements.append(("TITLE_LINE", text))

            elif is_all_italic(rich_texts):
                elements.append(("ACTION", text))
                state = "normal"

            elif state == "dialogue":
                # Handle mixed italic/non-italic in a single paragraph
                groups = split_by_italic(rich_texts)
                for is_italic, segment in groups:
                    segment = segment.strip()
                    if not segment:
                        continue
                    if is_italic:
                        elements.append(("ACTION", segment))
                        state = "normal"
                    else:
                        elements.append(("DIALOGUE", segment))
            else:
                elements.append(("ACTION", text))

        # Other block types (callout, quote, list, etc.) → treat as action
        elif btype in (
            "bulleted_list_item", "numbered_list_item", "quote",
            "callout", "toggle",
        ):
            if rich_texts:
                text = get_text(rich_texts).strip()
                if text:
                    elements.append(("ACTION", text))

    return elements


# ── PDF rendering ────────────────────────────────────────────────────────

class ScreenplayRenderer:
    def __init__(self, output_path, font_name):
        self.c = canvas.Canvas(output_path, pagesize=letter)
        self.font = font_name
        self.y = TOP_Y
        self.page = 0

    def _new_page(self):
        self.c.showPage()
        self.page += 1
        self.y = TOP_Y
        # Page numbers top-right from page 2 onward
        if self.page >= 2:
            self.c.setFont(self.font, FONT_SIZE)
            self.c.drawRightString(
                PAGE_W - MARGIN_R, PAGE_H - 36, f"{self.page}."
            )

    def _need_space(self, lines=1):
        """Start a new page if not enough room for `lines` lines."""
        if self.y - (lines * LINE_H) < BOT_Y:
            self._new_page()

    def _blank(self, n=1):
        self.y -= LINE_H * n

    def _draw_lines(self, x, lines, centered=False):
        self.c.setFont(self.font, FONT_SIZE)
        for line in lines:
            self._need_space()
            if centered:
                self.c.drawCentredString(x, self.y, line)
            else:
                self.c.drawString(x, self.y, line)
            self.y -= LINE_H

    def _wrap(self, text, width):
        """Wrap text to given character width, handling embedded newlines."""
        result = []
        for paragraph in text.split("\n"):
            paragraph = paragraph.strip()
            if paragraph:
                result.extend(textwrap.wrap(paragraph, width) or [paragraph])
        return result or [""]

    # ── Title page ───────────────────────────────────────────────────

    def _render_title_page(self, elements):
        title = ""
        lines = []

        for etype, text in elements:
            if etype == "TITLE":
                title = text.upper()
            elif etype == "TITLE_LINE":
                lines.append(text)

        # Title at roughly 40% from top, larger font
        self.y = PAGE_H * 0.55
        self.c.setFont(self.font, 24)
        self.c.drawCentredString(PAGE_W / 2, self.y, title)
        self.y -= 24 * 3

        # Author and info lines
        self.c.setFont(self.font, FONT_SIZE)
        for line in lines:
            # Split "Napisane przez X" / "Written by X" into two lines
            for prefix in ("Napisane przez ", "Written by "):
                if line.startswith(prefix):
                    self.c.drawCentredString(PAGE_W / 2, self.y, prefix.strip())
                    self.y -= LINE_H
                    self.c.drawCentredString(PAGE_W / 2, self.y, line[len(prefix):])
                    self.y -= LINE_H * 4
                    break
            else:
                self.c.drawCentredString(PAGE_W / 2, self.y, line)
                self.y -= LINE_H * 4

        self._new_page()

    # ── Content rendering ────────────────────────────────────────────

    def render(self, elements):
        # Split title page from content (everything before the first SCENE_BREAK)
        title_elements = []
        content_start = 0
        for i, (etype, _) in enumerate(elements):
            if etype == "SCENE_BREAK":
                content_start = i + 1
                break
            title_elements.append(elements[i])
        else:
            # No scene break found — all content, no title page
            content_start = 0
            title_elements = []

        if title_elements:
            self._render_title_page(title_elements)

        for etype, text in elements[content_start:]:
            if etype == "SCENE_HEADING":
                self._blank(2)
                self._need_space(2)
                lines = self._wrap(text, ACTION_CHARS)
                self._draw_lines(ACTION_X, lines)
                self._blank()

            elif etype == "CHARACTER":
                self._blank()
                self._need_space(3)  # name + at least one dialogue line
                self._draw_lines(CHARACTER_CENTER_X, [text], centered=True)

            elif etype == "DIALOGUE":
                lines = self._wrap(text, DIALOGUE_CHARS)
                self._need_space(len(lines))
                self._draw_lines(DIALOGUE_X, lines)

            elif etype == "ACTION":
                self._blank()
                lines = self._wrap(text, ACTION_CHARS)
                self._need_space(len(lines))
                self._draw_lines(ACTION_X, lines)

            elif etype == "SCENE_BREAK":
                self._blank()

        self.c.save()


# ── Google Drive ─────────────────────────────────────────────────────────

def get_drive_service():
    """Get Drive service using teatr.sgh OAuth credentials."""
    from googleapiclient.discovery import build
    from google.oauth2.credentials import Credentials

    creds = Credentials(
        token=None,
        refresh_token=os.getenv("GOOGLE_DRIVE_REFRESH_TOKEN"),
        client_id="32555940559.apps.googleusercontent.com",
        client_secret="ZmssLNjJy2998hD4CTg2ejr2",
        token_uri="https://oauth2.googleapis.com/token",
    )
    return build("drive", "v3", credentials=creds)


def upload_pdf(drive, file_path, filename):
    """Upload or update PDF on Drive. Returns (file_id, download_url)."""
    from googleapiclient.http import MediaFileUpload

    # Find existing file by name
    resp = drive.files().list(
        q=f"name='{filename}' and mimeType='application/pdf' and trashed=false",
        fields="files(id)",
    ).execute()
    existing = resp.get("files", [])

    media = MediaFileUpload(file_path, mimetype="application/pdf")

    if existing:
        file_id = existing[0]["id"]
        drive.files().update(fileId=file_id, media_body=media).execute()
        print(f"  Updated Drive file: {file_id}")
    else:
        uploaded = drive.files().create(
            body={"name": filename},
            media_body=media,
            fields="id",
        ).execute()
        file_id = uploaded["id"]
        drive.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
        ).execute()
        print(f"  Created Drive file: {file_id}")

    return file_id, f"https://drive.google.com/uc?export=download&id={file_id}"


# ── Notion file block update ────────────────────────────────────────────

def update_notion_file(notion, parent_page_id, drive_url):
    """Replace the file block on the parent Notion page."""
    blocks = notion.blocks.children.list(block_id=parent_page_id, page_size=100)

    file_block_id = None
    insert_after_id = None

    for b in blocks["results"]:
        if b["type"] == "file":
            file_block_id = b["id"]
        if b["type"] == "child_page":
            insert_after_id = b["id"]

    # Delete existing file block
    if file_block_id:
        notion.blocks.delete(block_id=file_block_id)
        print(f"  Deleted old file block")

    # Create new external file block
    new_block = {
        "type": "file",
        "file": {
            "type": "external",
            "external": {"url": drive_url},
        },
    }

    kwargs = {"block_id": parent_page_id, "children": [new_block]}
    if insert_after_id:
        kwargs["after"] = insert_after_id

    notion.blocks.children.append(**kwargs)
    print(f"  Created new file block")


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    print("=" * 60)
    print("Screenplay PDF Sync")
    print("=" * 60)

    notion = NotionAuth.get_client()

    # 1. Check for changes
    force = "--force" in sys.argv
    changed, timestamp = check_page_changed(notion, SCREENPLAY_PAGE_ID, "screenplay")

    if not changed and not force:
        print(f"No changes since {timestamp}, skipping")
        return

    print(f"Changes detected (last edit: {timestamp})")

    # 2. Setup fonts
    font_name = setup_fonts()

    # 3. Fetch screenplay content
    blocks = fetch_all_blocks(notion, SCREENPLAY_PAGE_ID)
    print(f"  Fetched {len(blocks)} blocks")

    # 4. Extract elements
    elements = extract_elements(blocks)
    print(f"  Extracted {len(elements)} elements")

    # 5. Generate PDF
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        pdf_path = f.name

    renderer = ScreenplayRenderer(pdf_path, font_name)
    renderer.render(elements)
    pdf_size = os.path.getsize(pdf_path)
    print(f"  Generated PDF: {pdf_size:,} bytes")

    # 6. Upload to Drive
    drive = get_drive_service()
    _, drive_url = upload_pdf(drive, pdf_path, PDF_FILENAME)

    # 7. Update Notion file block
    update_notion_file(notion, PARENT_PAGE_ID, drive_url)

    # 8. Update cache
    set_cached_time("screenplay", timestamp)

    # Cleanup
    os.unlink(pdf_path)

    print("=" * 60)
    print("Sync complete!")


if __name__ == "__main__":
    main()
