#!/usr/bin/env python3
"""
Individual character scripts generator.

Reads the theatrical script from a Notion page, cross-references with the
Obsady (cast) database, and generates a separate PDF for each role containing
only the scenes and lines relevant to that character — with cue lines from
other characters for context.

PDFs use standard theatrical formatting (A4, serif font) and are uploaded to
Google Drive, then linked on a dedicated Notion page.
"""

import io
import os
import re
import sys
import unicodedata
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(Path(__file__).parent))

from shared.auth import NotionAuth
from shared.change_check import check_page_changed, set_cached_time
from config import (
    SOURCE_PAGE_ID,
    OBSADY_DATABASE_ID,
    OUTPUT_PARENT_PAGE_ID,
    OUTPUT_PAGE_TITLE,
    DRIVE_FOLDER_NAME,
    ROLE_OVERRIDES,
    COMBINED_CHARACTERS,
    TECHNICAL_ROLES,
)

from reportlab.lib.pagesizes import A4
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_JUSTIFY
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.colors import Color
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    PageBreak,
    KeepTogether,
)
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------

_FONTS_REGISTERED = False


def register_fonts():
    global _FONTS_REGISTERED
    if _FONTS_REGISTERED:
        return
    candidates = [
        ("/System/Library/Fonts/Supplemental/Times New Roman.ttf",
         "/System/Library/Fonts/Supplemental/Times New Roman Bold.ttf",
         "/System/Library/Fonts/Supplemental/Times New Roman Italic.ttf",
         "/System/Library/Fonts/Supplemental/Times New Roman Bold Italic.ttf"),
        ("/usr/share/fonts/truetype/liberation/LiberationSerif-Regular.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSerif-Bold.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf",
         "/usr/share/fonts/truetype/liberation/LiberationSerif-BoldItalic.ttf"),
        ("/usr/share/fonts/truetype/liberation2/LiberationSerif-Regular.ttf",
         "/usr/share/fonts/truetype/liberation2/LiberationSerif-Bold.ttf",
         "/usr/share/fonts/truetype/liberation2/LiberationSerif-Italic.ttf",
         "/usr/share/fonts/truetype/liberation2/LiberationSerif-BoldItalic.ttf"),
    ]
    for regular, bold, italic, bold_italic in candidates:
        if os.path.exists(regular):
            pdfmetrics.registerFont(TTFont("Serif", regular))
            pdfmetrics.registerFont(TTFont("SerifBold", bold if os.path.exists(bold) else regular))
            pdfmetrics.registerFont(TTFont("SerifItalic", italic if os.path.exists(italic) else regular))
            pdfmetrics.registerFont(TTFont("SerifBI", bold_italic if os.path.exists(bold_italic) else regular))
            _FONTS_REGISTERED = True
            return
    raise RuntimeError("No serif TTF font found")


# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------

GRAY = Color(0.45, 0.45, 0.45)

def make_styles():
    return {
        "act": ParagraphStyle(
            "act", fontName="SerifBold", fontSize=16,
            alignment=TA_CENTER, leading=22, spaceBefore=18, spaceAfter=12,
        ),
        "scene": ParagraphStyle(
            "scene", fontName="SerifBold", fontSize=13,
            alignment=TA_CENTER, leading=18, spaceBefore=14, spaceAfter=8,
        ),
        "stage_dir": ParagraphStyle(
            "stage_dir", fontName="SerifItalic", fontSize=11,
            alignment=TA_LEFT, leading=15, spaceBefore=3, spaceAfter=3,
            leftIndent=20, textColor=GRAY,
        ),
        "char_name": ParagraphStyle(
            "char_name", fontName="SerifBold", fontSize=12,
            alignment=TA_CENTER, leading=16, spaceBefore=10, spaceAfter=2,
        ),
        "char_name_own": ParagraphStyle(
            "char_name_own", fontName="SerifBold", fontSize=12,
            alignment=TA_CENTER, leading=16, spaceBefore=10, spaceAfter=2,
        ),
        "dialogue": ParagraphStyle(
            "dialogue", fontName="Serif", fontSize=12,
            alignment=TA_JUSTIFY, leading=16, spaceBefore=0, spaceAfter=2,
        ),
        "inline_dir": ParagraphStyle(
            "inline_dir", fontName="SerifItalic", fontSize=11,
            alignment=TA_LEFT, leading=14, spaceBefore=1, spaceAfter=1,
            leftIndent=20, textColor=GRAY,
        ),
        "cue_name": ParagraphStyle(
            "cue_name", fontName="SerifBold", fontSize=10,
            alignment=TA_CENTER, leading=14, spaceBefore=8, spaceAfter=1,
            textColor=GRAY,
        ),
        "cue_text": ParagraphStyle(
            "cue_text", fontName="SerifItalic", fontSize=10,
            alignment=TA_JUSTIFY, leading=13, spaceBefore=0, spaceAfter=2,
            textColor=GRAY,
        ),
        "title": ParagraphStyle(
            "title", fontName="SerifBold", fontSize=22,
            alignment=TA_CENTER, leading=28,
        ),
        "subtitle": ParagraphStyle(
            "subtitle", fontName="Serif", fontSize=14,
            alignment=TA_CENTER, leading=20,
        ),
        "music": ParagraphStyle(
            "music", fontName="SerifItalic", fontSize=10,
            alignment=TA_LEFT, leading=14, spaceBefore=2, spaceAfter=2,
            leftIndent=20, textColor=GRAY,
        ),
    }


# ---------------------------------------------------------------------------
# Notion fetching
# ---------------------------------------------------------------------------

def fetch_blocks_recursive(notion, block_id):
    """Fetch all blocks from a page, recursing into children."""
    blocks = []
    cursor = None
    while True:
        kwargs = {"block_id": block_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        result = notion.blocks.children.list(**kwargs)
        for b in result["results"]:
            blocks.append(b)
            if b.get("has_children"):
                b["_children"] = fetch_blocks_recursive(notion, b["id"])
            else:
                b["_children"] = []
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")
    return blocks


def get_text(rich_texts):
    return "".join(t["plain_text"] for t in rich_texts)


# ---------------------------------------------------------------------------
# Script parsing
# ---------------------------------------------------------------------------

def parse_speech_block(rich_texts):
    """Parse a paragraph's rich text into (character_name, parts).

    Parts is a list of (type, text) where type is 'dialogue' or 'direction'.
    """
    char_name = ""
    parts = []

    for t in rich_texts:
        ann = t.get("annotations", {})
        text = t["plain_text"]
        is_bold = ann.get("bold", False)
        is_italic = ann.get("italic", False)

        if is_bold and not is_italic:
            char_name += text.strip()
        elif is_italic:
            cleaned = text.strip()
            if cleaned:
                parts.append(("direction", cleaned))
        else:
            cleaned = text.strip()
            if cleaned:
                parts.append(("dialogue", cleaned))

    return char_name, parts


def parse_blocks(blocks):
    """Convert Notion blocks into a flat list of structured elements.

    Element types:
        ('ACT', text)
        ('SCENE', text)
        ('STAGE_DIR', text)       — scene-level stage direction (callout)
        ('MUSIC', text)           — music cue (callout starting with Muzyka/Melodia)
        ('SPEECH', char_name, [(type, text), ...])
        ('IMAGE',)
    """
    elements = []

    def _process(block_list):
        for b in block_list:
            btype = b["type"]
            rt = b.get(btype, {}).get("rich_text", [])
            text = get_text(rt).strip() if rt else ""

            if btype == "heading_1":
                elements.append(("ACT", text))
            elif btype == "heading_2":
                elements.append(("SCENE", text))
            elif btype == "callout":
                if text:
                    if re.match(r"(?i)(muzyka|melodia)", text):
                        elements.append(("MUSIC", text))
                    else:
                        elements.append(("STAGE_DIR", text))
            elif btype == "image":
                elements.append(("IMAGE",))
            elif btype == "paragraph":
                if not rt:
                    continue
                char_name, parts = parse_speech_block(rt)
                if char_name and char_name == char_name.upper() and any(c.isalpha() for c in char_name):
                    elements.append(("SPEECH", char_name, parts))
                elif text:
                    # Non-character paragraph — treat as stage direction
                    elements.append(("STAGE_DIR", text))

            # Recurse into children
            if b.get("_children"):
                _process(b["_children"])

    _process(blocks)
    return elements


# ---------------------------------------------------------------------------
# Role / character mapping
# ---------------------------------------------------------------------------

def get_obsady_roles(notion):
    """Fetch role names from the Obsady database. Returns list of role names."""
    roles = []
    cursor = None
    while True:
        params = {"database_id": OBSADY_DATABASE_ID, "page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        resp = notion.databases.query(**params)
        for page in resp["results"]:
            for pval in page["properties"].values():
                if pval.get("type") == "title":
                    items = pval.get("title", [])
                    if items:
                        roles.append(items[0]["plain_text"].strip())
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")
    return roles


def normalize_name(name):
    """Normalize a character name for matching."""
    n = name.strip().upper()
    # Replace digits with roman numerals: 1→I, 2→II, 3→III
    n = re.sub(r"(\d+)", lambda m: {1: "I", 2: "II", 3: "III", 4: "IV", 5: "V"}.get(int(m.group()), m.group()), n)
    return n


def build_role_character_map(roles, all_script_characters):
    """Map each Obsady role to a set of script character names."""
    role_map = {}  # role_name -> set of character names

    for role in roles:
        if role in ROLE_OVERRIDES:
            chars = set(ROLE_OVERRIDES[role])
        else:
            # Split by "/" and try matching each part
            parts = [p.strip() for p in role.split("/")]
            chars = set()
            for part in parts:
                normalized = normalize_name(part)
                if normalized in all_script_characters:
                    chars.add(normalized)
                # Try with "PAN"/"PANI" prefix
                elif f"PAN {normalized}" in all_script_characters:
                    chars.add(f"PAN {normalized}")
                elif f"PANI {normalized}" in all_script_characters:
                    chars.add(f"PANI {normalized}")

        # Add combined character mappings
        expanded = set(chars)
        for combined, individuals in COMBINED_CHARACTERS.items():
            if expanded & set(individuals):
                expanded.add(combined)

        role_map[role] = expanded

    return role_map


def get_all_script_characters(elements):
    """Extract all unique character names from parsed elements."""
    return {e[1] for e in elements if e[0] == "SPEECH"}


# ---------------------------------------------------------------------------
# Character script filtering
# ---------------------------------------------------------------------------

def _group_by_scene(elements):
    """Group flat elements into (act_name, scene_name, scene_elems) tuples."""
    scenes = []
    current_act = None
    current_scene = None
    current_elems = []

    for elem in elements:
        if elem[0] == "ACT":
            current_act = elem[1]
        elif elem[0] == "SCENE":
            if current_scene is not None:
                scenes.append((current_act, current_scene, current_elems))
            current_scene = elem[1]
            current_elems = []
        else:
            current_elems.append(elem)

    if current_scene is not None:
        scenes.append((current_act, current_scene, current_elems))

    # Elements before first scene (e.g. prolog stage dirs)
    prolog_elems = []
    for elem in elements:
        if elem[0] in ("ACT", "SCENE"):
            break
        prolog_elems.append(elem)
    if prolog_elems:
        scenes.insert(0, (None, None, prolog_elems))

    return scenes


def _role_in_scene(scene_elems, char_names, role_search_names):
    """Check if a role is present in a scene.

    Matches by:
    - character having a SPEECH in the scene
    - any role name appearing in STAGE_DIR or MUSIC text (e.g. scene
      participant lists, stage directions mentioning the character)
    """
    for e in scene_elems:
        if e[0] == "SPEECH" and e[1] in char_names:
            return True
        if e[0] in ("STAGE_DIR", "MUSIC"):
            text_lower = e[1].lower()
            if any(name in text_lower for name in role_search_names):
                return True
    return False


def _emit_full_scene(scene_elems, char_names):
    """Emit all elements from a scene with speeches tagged as OWN or CUE."""
    output = []
    for e in scene_elems:
        if e[0] == "SPEECH":
            if e[1] in char_names:
                output.append(("OWN_SPEECH", e[1], e[2]))
            else:
                output.append(("CUE_SPEECH", e[1], e[2]))
        elif e[0] in ("STAGE_DIR", "MUSIC"):
            output.append(e)
    return output


def _role_search_names(role_name, char_names):
    """Build lowercase search strings from role name and character names."""
    names = set()
    # From the Obsady role (split by "/")
    for part in role_name.split("/"):
        cleaned = part.strip().lower()
        if cleaned:
            names.add(cleaned)
    # From matched script character names
    for cn in char_names:
        names.add(cn.lower())
    return names


def filter_for_role(elements, char_names, role_name=""):
    """Create a character script with full scene context.

    Includes ALL elements from every scene the character is in (speeches,
    stage directions, music cues).  The character's own speeches are tagged
    OWN_SPEECH; everyone else's are CUE_SPEECH.

    A scene is "active" if the character has speeches there OR is mentioned
    by name in any stage direction / callout.
    """
    scenes = _group_by_scene(elements)
    search_names = _role_search_names(role_name, char_names)

    output = []
    last_act = None

    for act_name, scene_name, scene_elems in scenes:
        if not _role_in_scene(scene_elems, char_names, search_names):
            continue

        if act_name and act_name != last_act:
            output.append(("ACT", act_name))
            last_act = act_name

        if scene_name:
            output.append(("SCENE", scene_name))

        output.extend(_emit_full_scene(scene_elems, char_names))

    return output


def filter_for_technical_role(elements):
    """Create a full cue sheet for the sound/audio role.

    Every callout block (STAGE_DIR or MUSIC) is an audio cue.  The script
    includes ALL scenes that contain at least one callout, with full context
    (all speeches as CUE, all callouts as OWN).
    """
    scenes = _group_by_scene(elements)

    output = []
    last_act = None

    for act_name, scene_name, scene_elems in scenes:
        # Include scene if it has any callout (STAGE_DIR or MUSIC)
        has_callout = any(e[0] in ("STAGE_DIR", "MUSIC") for e in scene_elems)
        if not has_callout:
            continue

        if act_name and act_name != last_act:
            output.append(("ACT", act_name))
            last_act = act_name

        if scene_name:
            output.append(("SCENE", scene_name))

        # All callouts are OWN, all speeches are CUE
        for e in scene_elems:
            if e[0] in ("STAGE_DIR", "MUSIC"):
                output.append(("OWN_SPEECH", "AUDIACJA", [("direction", e[1])]))
            elif e[0] == "SPEECH":
                output.append(("CUE_SPEECH", e[1], e[2]))

    return output


# ---------------------------------------------------------------------------
# PDF generation (theatrical format)
# ---------------------------------------------------------------------------

def esc(text):
    """Escape text for ReportLab Paragraph XML."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def generate_script_pdf(role_name, elements):
    """Generate a theatrical-format PDF for a single role."""
    register_fonts()
    styles = make_styles()

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=70,
        rightMargin=70,
        topMargin=60,
        bottomMargin=60,
    )

    story = []

    # --- Title page ---
    story.append(Spacer(1, 180))
    story.append(Paragraph(esc(role_name.upper()), styles["title"]))
    story.append(Spacer(1, 30))
    story.append(Paragraph("Scenariusz aktorski", styles["subtitle"]))
    story.append(Spacer(1, 15))
    story.append(Paragraph("Mieszczanin szlachcicem", styles["subtitle"]))
    story.append(Spacer(1, 10))
    story.append(Paragraph("Teatr Scena Główna Handlowa", styles["subtitle"]))
    story.append(PageBreak())

    # --- Content ---
    for elem in elements:
        etype = elem[0]

        if etype == "ACT":
            story.append(Spacer(1, 12))
            story.append(Paragraph(esc(elem[1]), styles["act"]))

        elif etype == "SCENE":
            story.append(Spacer(1, 6))
            story.append(Paragraph(esc(elem[1]), styles["scene"]))

        elif etype == "STAGE_DIR":
            story.append(Paragraph(f"({esc(elem[1])})", styles["stage_dir"]))

        elif etype == "MUSIC":
            story.append(Paragraph(f"[{esc(elem[1])}]", styles["music"]))

        elif etype == "CUE_SPEECH":
            char_name = elem[1]
            parts = elem[2]
            # Full cue speech — all dialogue and stage directions, in gray
            block = [Paragraph(esc(char_name), styles["cue_name"])]
            for ptype, ptext in parts:
                if ptype == "direction":
                    block.append(Paragraph(f"({esc(ptext)})", styles["inline_dir"]))
                else:
                    block.append(Paragraph(esc(ptext), styles["cue_text"]))
            if len(block) > 1:
                story.append(KeepTogether(block))

        elif etype == "OWN_SPEECH":
            char_name = elem[1]
            parts = elem[2]
            block = [Paragraph(esc(char_name), styles["char_name_own"])]
            for ptype, ptext in parts:
                if ptype == "direction":
                    block.append(Paragraph(f"({esc(ptext)})", styles["inline_dir"]))
                else:
                    block.append(Paragraph(esc(ptext), styles["dialogue"]))
            story.append(KeepTogether(block))

    doc.build(story)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Google Drive
# ---------------------------------------------------------------------------

def get_drive_service():
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


def get_or_create_folder(drive):
    resp = (
        drive.files()
        .list(
            q=f"name='{DRIVE_FOLDER_NAME}' and mimeType='application/vnd.google-apps.folder' and trashed=false",
            fields="files(id)",
        )
        .execute()
    )
    if resp.get("files"):
        return resp["files"][0]["id"]

    meta = {"name": DRIVE_FOLDER_NAME, "mimeType": "application/vnd.google-apps.folder"}
    folder = drive.files().create(body=meta, fields="id").execute()
    folder_id = folder["id"]
    drive.permissions().create(
        fileId=folder_id, body={"type": "anyone", "role": "reader"}
    ).execute()
    print(f"  Created Drive folder '{DRIVE_FOLDER_NAME}' ({folder_id})")
    return folder_id


def upload_pdf(drive, folder_id, pdf_bytes, filename):
    from googleapiclient.http import MediaInMemoryUpload

    # Replace existing file if present
    resp = (
        drive.files()
        .list(
            q=f"name='{filename}' and '{folder_id}' in parents and trashed=false",
            fields="files(id)",
        )
        .execute()
    )
    for f in resp.get("files", []):
        drive.files().delete(fileId=f["id"]).execute()

    media = MediaInMemoryUpload(pdf_bytes, mimetype="application/pdf", resumable=False)
    uploaded = (
        drive.files()
        .create(
            body={"name": filename, "parents": [folder_id]},
            media_body=media,
            fields="id",
        )
        .execute()
    )
    file_id = uploaded["id"]
    drive.permissions().create(
        fileId=file_id, body={"type": "anyone", "role": "reader"}
    ).execute()

    return file_id, f"https://drive.google.com/file/d/{file_id}/view"


# ---------------------------------------------------------------------------
# Notion output page
# ---------------------------------------------------------------------------

def sanitize_filename(text):
    nfkd = unicodedata.normalize("NFKD", text.lower())
    ascii_text = nfkd.encode("ascii", "ignore").decode("ascii")
    return "".join(c if c.isalnum() else "-" for c in ascii_text).strip("-")


def find_output_page(notion):
    """Search for the output page by title under the parent page."""
    children = notion.blocks.children.list(
        block_id=OUTPUT_PARENT_PAGE_ID, page_size=100
    )
    for block in children["results"]:
        if block["type"] == "child_page":
            title = block["child_page"].get("title", "")
            if title == OUTPUT_PAGE_TITLE:
                return block["id"]
    return None


def create_output_page(notion):
    """Create the output page as a child of the parent page."""
    page = notion.pages.create(
        parent={"page_id": OUTPUT_PARENT_PAGE_ID},
        properties={"title": [{"text": {"content": OUTPUT_PAGE_TITLE}}]},
    )
    print(f"  Created output page: {OUTPUT_PAGE_TITLE}")
    return page["id"]


def clear_page_content(notion, page_id):
    """Remove all blocks from a page."""
    cursor = None
    block_ids = []
    while True:
        kwargs = {"block_id": page_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        result = notion.blocks.children.list(**kwargs)
        block_ids.extend(b["id"] for b in result["results"])
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")

    for bid in block_ids:
        notion.blocks.delete(block_id=bid)


def update_output_page(notion, page_id, role_files):
    """Write role headings and file blocks to the output page.

    role_files: list of (role_name, drive_url, filename)
    """
    clear_page_content(notion, page_id)

    children = []
    for role_name, drive_url, filename in role_files:
        children.append({
            "type": "heading_3",
            "heading_3": {
                "rich_text": [{"type": "text", "text": {"content": role_name}}],
            },
        })
        children.append({
            "type": "file",
            "file": {
                "type": "external",
                "external": {"url": drive_url},
                "name": filename,
            },
        })

    # Notion API allows max 100 children per append
    for i in range(0, len(children), 100):
        notion.blocks.children.append(
            block_id=page_id, children=children[i : i + 100]
        )


# ---------------------------------------------------------------------------
# Main sync
# ---------------------------------------------------------------------------

def sync():
    notion = NotionAuth.get_client()

    # 1. Check for changes
    force = "--force" in sys.argv
    changed, timestamp = check_page_changed(notion, SOURCE_PAGE_ID, "scripts")
    if not changed and not force:
        print(f"No changes since {timestamp}, skipping")
        return True

    print(f"Changes detected (last edit: {timestamp})")

    # 2. Fetch and parse script
    print("Fetching script blocks...")
    blocks = fetch_blocks_recursive(notion, SOURCE_PAGE_ID)
    elements = parse_blocks(blocks)
    print(f"  Parsed {len(elements)} elements")

    all_chars = get_all_script_characters(elements)
    print(f"  Found {len(all_chars)} unique characters")

    # 3. Get roles from Obsady and build mapping
    roles = get_obsady_roles(notion)
    print(f"  {len(roles)} roles in Obsady")

    role_map = build_role_character_map(roles, all_chars)

    # 4. Generate PDFs
    register_fonts()
    drive = get_drive_service()
    folder_id = get_or_create_folder(drive)

    role_files = []  # (role_name, drive_url, filename)
    generated = 0
    skipped = 0

    for role in sorted(roles):
        # Technical roles get a cue-sheet style script
        if role in TECHNICAL_ROLES:
            filtered = filter_for_technical_role(elements)
            own_count = sum(1 for e in filtered if e[0] == "OWN_SPEECH")
            if own_count == 0:
                print(f"  Skipped: {role} (no cues found)")
                skipped += 1
                continue
        else:
            chars = role_map.get(role, set())
            if not chars:
                print(f"  Skipped: {role} (no speaking parts)")
                skipped += 1
                continue

            filtered = filter_for_role(elements, chars, role_name=role)
            own_count = sum(1 for e in filtered if e[0] == "OWN_SPEECH")
            if own_count == 0:
                print(f"  Skipped: {role} (0 speeches after filtering)")
                skipped += 1
                continue

        # Generate PDF
        pdf_bytes = generate_script_pdf(role, filtered)
        filename = f"scenariusz-{sanitize_filename(role)}.pdf"

        # Upload to Drive
        file_id, view_url = upload_pdf(drive, folder_id, pdf_bytes, filename)
        role_files.append((role, view_url, filename))
        print(f"  Generated: {role} ({own_count} cues) → {filename}")
        generated += 1

    # 5. Update Notion output page
    print("Updating Notion output page...")
    page_id = find_output_page(notion)
    if not page_id:
        page_id = create_output_page(notion)
    update_output_page(notion, page_id, role_files)

    # 6. Update cache
    set_cached_time("scripts", timestamp)

    print(
        f"\nSync complete: {generated} generated, {skipped} skipped"
    )
    return True


if __name__ == "__main__":
    print("=" * 60)
    print("Individual Character Scripts Sync")
    print("=" * 60)
    success = sync()
    sys.exit(0 if success else 1)
