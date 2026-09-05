#!/usr/bin/env python3
import os
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(Path(__file__).parent))

from shared.auth import NotionAuth
from config import (
    DATABASE_ID,
    STATUS_PENDING,
    STATUS_INSUFFICIENT,
    STATUS_GENERATED,
    DRIVE_FOLDER_NAME,
)
from pdf_generator import generate_pdf


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
    print(f"Created Drive folder '{DRIVE_FOLDER_NAME}' ({folder_id})")
    return folder_id


def upload_pdf(drive, folder_id, pdf_bytes, filename):
    from googleapiclient.http import MediaInMemoryUpload

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
        .create(body={"name": filename, "parents": [folder_id]}, media_body=media, fields="id")
        .execute()
    )
    file_id = uploaded["id"]

    drive.permissions().create(
        fileId=file_id, body={"type": "anyone", "role": "reader"}
    ).execute()

    view_url = f"https://drive.google.com/file/d/{file_id}/view"
    return file_id, view_url


def query_actionable_records(notion):
    pages = []
    for status_name in [STATUS_PENDING, STATUS_INSUFFICIENT]:
        has_more = True
        cursor = None
        while has_more:
            params = {
                "database_id": DATABASE_ID,
                "filter": {
                    "property": "status",
                    "status": {"equals": status_name},
                },
            }
            if cursor:
                params["start_cursor"] = cursor
            resp = notion.databases.query(**params)
            pages.extend(resp["results"])
            has_more = resp.get("has_more", False)
            cursor = resp.get("next_cursor")
    return pages


def extract_data(page):
    props = page["properties"]
    errors = []

    title_items = props.get("Imię i nazwisko", {}).get("title", [])
    name = title_items[0]["plain_text"].strip() if title_items else ""
    if not name:
        errors.append("Brak imienia i nazwiska")

    album = props.get("nr albumu", {}).get("number")
    if not album:
        errors.append("Brak numeru albumu")

    rt = props.get("Prowadzący zajęcia", {}).get("rich_text", [])
    lecturer = rt[0]["plain_text"].strip() if rt else ""
    if not lecturer:
        errors.append("Brak prowadzącego zajęcia")

    rt2 = props.get("Nazwa zajęć", {}).get("rich_text", [])
    subject = rt2[0]["plain_text"].strip() if rt2 else ""
    if not subject:
        errors.append("Brak nazwy zajęć")

    date_prop = props.get("Data i godziny zajęć", {}).get("date") or {}
    start_str = date_prop.get("start")
    end_str = date_prop.get("end")
    date_start = date_end = None
    if start_str and end_str:
        try:
            date_start = datetime.fromisoformat(start_str)
            date_end = datetime.fromisoformat(end_str)
        except ValueError:
            errors.append("Nieprawidłowy format daty")
    else:
        errors.append("Brak daty i godzin zajęć (wymagany zakres: data z godziną od–do)")

    rt3 = props.get("Powód usprawiedliwienia", {}).get("rich_text", [])
    reason = rt3[0]["plain_text"].strip() if rt3 else ""

    data = {
        "name": name,
        "album": album,
        "lecturer": lecturer,
        "subject": subject,
        "date_start": date_start,
        "date_end": date_end,
        "reason": reason,
    }
    return data, errors


def sanitize_filename(text):
    nfkd = unicodedata.normalize("NFKD", text.lower())
    ascii_text = nfkd.encode("ascii", "ignore").decode("ascii")
    return "".join(c if c.isalnum() else "-" for c in ascii_text).strip("-")


def should_skip(page, bot_user_id):
    status = page["properties"]["status"]["status"]["name"]
    if status != STATUS_INSUFFICIENT:
        return False
    # If our integration was the last editor, skip — no human changes since we set the status
    last_editor_id = page.get("last_edited_by", {}).get("id")
    return last_editor_id == bot_user_id


def sync():
    notion = NotionAuth.get_client()
    bot_user_id = notion.users.me()["id"]
    records = query_actionable_records(notion)
    print(f"Found {len(records)} actionable records")

    if not records:
        print("Nothing to process")
        return True

    drive = get_drive_service()
    folder_id = get_or_create_folder(drive)

    generated = 0
    insufficient = 0
    skipped = 0
    errors = 0

    for page in records:
        page_id = page["id"]
        status = page["properties"]["status"]["status"]["name"]

        if should_skip(page, bot_user_id):
            skipped += 1
            continue

        data, validation_errors = extract_data(page)
        label = data.get("name") or page_id[:8]

        if validation_errors:
            if status != STATUS_INSUFFICIENT:
                try:
                    notion.pages.update(
                        page_id=page_id,
                        properties={"status": {"status": {"name": STATUS_INSUFFICIENT}}},
                    )
                except Exception as e:
                    print(f"  Failed to update status for {label}: {e}")
                    errors += 1
                    continue
            print(f"  Insufficient: {label} — {'; '.join(validation_errors)}")
            insufficient += 1
            continue

        try:
            pdf_bytes = generate_pdf(data)
        except Exception as e:
            print(f"  PDF generation failed for {label}: {e}")
            errors += 1
            continue

        date_slug = data["date_start"].strftime("%Y-%m-%d")
        filename = f"usprawiedliwienie-{sanitize_filename(data['name'])}-{date_slug}.pdf"
        try:
            file_id, view_url = upload_pdf(drive, folder_id, pdf_bytes, filename)
        except Exception as e:
            print(f"  Drive upload failed for {label}: {e}")
            errors += 1
            continue

        try:
            notion.pages.update(
                page_id=page_id,
                properties={
                    "Dokument do podpisania": {
                        "files": [
                            {
                                "name": filename,
                                "type": "external",
                                "external": {"url": view_url},
                            }
                        ]
                    },
                    "status": {"status": {"name": STATUS_GENERATED}},
                },
            )
            print(f"  Generated: {label} → {filename}")
            generated += 1
        except Exception as e:
            print(f"  Notion update failed for {label}: {e}")
            errors += 1

    print(
        f"\nSync complete: {generated} generated, {insufficient} insufficient, "
        f"{skipped} skipped, {errors} errors"
    )
    return errors == 0


if __name__ == "__main__":
    print("=" * 60)
    print("Excuses Sync — Generate excuse letter PDFs")
    print("=" * 60)
    success = sync()
    sys.exit(0 if success else 1)
