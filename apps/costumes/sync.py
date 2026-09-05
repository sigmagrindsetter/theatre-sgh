#!/usr/bin/env python3
import sys
import httpx
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from shared.auth import NotionAuth
from shared.change_check import check_databases_changed, set_cached_time
from config import (
    OBSADY_DATABASE_ID,
    MEMBERS_DATABASE_ID,
    AKTORZY_DATABASE_ID,
    SIZE_COLUMNS,
    DRIVE_TEMP_FOLDER_ID,
)


def query_all_pages(client, database_id, **kwargs):
    pages = []
    has_more = True
    next_cursor = None
    while has_more:
        params = {"database_id": database_id, **kwargs}
        if next_cursor:
            params["start_cursor"] = next_cursor
        resp = client.databases.query(**params)
        pages.extend(resp["results"])
        has_more = resp.get("has_more", False)
        next_cursor = resp.get("next_cursor")
    return pages


def get_cast_people(client):
    pages = query_all_pages(client, OBSADY_DATABASE_ID)
    people = {}
    for page in pages:
        for prop_name, prop_value in page["properties"].items():
            if prop_value.get("type") == "people":
                for person in prop_value.get("people", []):
                    pid = person.get("id")
                    name = person.get("name")
                    if pid and name:
                        people[pid] = name.strip()
    print(f"Found {len(people)} unique people in Obsady")
    return people


def get_members_data(client):
    pages = query_all_pages(client, MEMBERS_DATABASE_ID)
    members = {}
    for page in pages:
        props = page["properties"]
        person_list = props.get("Person", {}).get("people", [])
        if not person_list:
            continue
        pid = person_list[0]["id"]

        data = {}

        name_prop = props.get("Imię i Nazwisko", {}).get("title", [])
        if name_prop:
            data["_full_name"] = name_prop[0]["plain_text"].strip()

        for col in SIZE_COLUMNS:
            val = props.get(col, {}).get("number")
            if val is not None:
                data[col] = val

        photo = props.get("Zdjęcie sylwetkowe", {}).get("files", [])
        if photo:
            f = photo[0]
            if f.get("type") == "file":
                data["_photo_url"] = f["file"]["url"]
                data["_photo_name"] = f.get("name", "photo.jpg")
            elif f.get("type") == "external":
                data["_photo_url"] = f["external"]["url"]
                data["_photo_name"] = f.get("name", "photo.jpg")

        members[pid] = data
    print(f"Found {len(members)} members with data")
    return members


def get_existing_aktorzy(client):
    pages = query_all_pages(client, AKTORZY_DATABASE_ID)
    existing = {}
    for page in pages:
        people = page["properties"].get("Konto Notion", {}).get("people", [])
        if people:
            existing[people[0]["id"]] = page["id"]
    print(f"Found {len(existing)} existing rows in Aktorzy")
    return existing


def get_drive_service():
    """Get Drive service using teatr.sgh OAuth credentials (has storage quota)."""
    import os
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


def get_existing_drive_photos(drive, folder_id):
    existing = {}
    page_token = None
    while True:
        resp = drive.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            fields="nextPageToken, files(id, name)",
            pageToken=page_token,
        ).execute()
        for f in resp.get("files", []):
            existing[f["name"]] = f["id"]
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return existing


def upload_photo_to_drive(drive, folder_id, image_bytes, filename):
    from googleapiclient.http import MediaInMemoryUpload

    media = MediaInMemoryUpload(image_bytes, mimetype="image/jpeg", resumable=False)
    file_meta = {"name": filename, "parents": [folder_id]}
    uploaded = drive.files().create(body=file_meta, media_body=media, fields="id").execute()
    file_id = uploaded["id"]

    drive.permissions().create(
        fileId=file_id,
        body={"type": "anyone", "role": "reader"},
    ).execute()

    # Direct thumbnail URL that renders as an image in Notion
    return file_id, f"https://lh3.googleusercontent.com/d/{file_id}"


def delete_drive_file(drive, file_id):
    drive.files().delete(fileId=file_id).execute()


def download_image(url):
    resp = httpx.get(url, follow_redirects=True, timeout=30)
    resp.raise_for_status()
    return resp.content


def build_properties(person_id, person_name, member_data, photo_url=None):
    props = {
        "Imię i Nazwisko": {"title": [{"text": {"content": person_name}}]},
        "Konto Notion": {"people": [{"id": person_id}]},
    }

    for col in SIZE_COLUMNS:
        val = member_data.get(col)
        if val is not None:
            props[col] = {"number": val}
        else:
            props[col] = {"number": None}

    if photo_url:
        props["Zdjęcie sylwetkowe"] = {
            "files": [{"name": "sylwetka", "type": "external", "external": {"url": photo_url}}]
        }

    return props


def sync():
    from concurrent.futures import ThreadPoolExecutor

    notion = NotionAuth.get_client()

    cast_people = get_cast_people(notion)
    members_data = get_members_data(notion)
    existing = get_existing_aktorzy(notion)

    drive = get_drive_service()
    folder_id = DRIVE_TEMP_FOLDER_ID
    drive_photos = get_existing_drive_photos(drive, folder_id)
    print(f"Found {len(drive_photos)} existing photos on Drive")

    person_tasks = []
    active_filenames = set()
    new_photos = 0

    for pid, account_name in cast_people.items():
        member = members_data.get(pid, {})
        name = member.get("_full_name", account_name)
        photo_url = None

        source_photo = member.get("_photo_url")
        if source_photo:
            photo_name = member.get("_photo_name", "photo.jpg")
            drive_filename = f"{pid}_{photo_name}"
            active_filenames.add(drive_filename)

            try:
                if drive_filename in drive_photos:
                    file_id = drive_photos[drive_filename]
                    photo_url = f"https://lh3.googleusercontent.com/d/{file_id}"
                else:
                    image_bytes = download_image(source_photo)
                    file_id, photo_url = upload_photo_to_drive(
                        drive, folder_id, image_bytes, drive_filename
                    )
                    new_photos += 1
            except Exception as e:
                print(f"  Photo transfer failed for {name}: {e}")

        props = build_properties(pid, name, member, photo_url)
        person_tasks.append((pid, name, props))

    if new_photos:
        print(f"Uploaded {new_photos} new photos, reused {len(active_filenames) - new_photos}")
    else:
        print(f"All {len(active_filenames)} photos reused from Drive")

    created = 0
    updated = 0
    errors = 0

    def _update_page(task):
        pid, name, props = task
        try:
            if pid in existing:
                notion.pages.update(page_id=existing[pid], properties=props)
                return "updated", name
            else:
                notion.pages.create(
                    parent={"database_id": AKTORZY_DATABASE_ID},
                    properties=props,
                )
                return "created", name
        except Exception as e:
            return "error", f"{name}: {e}"

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(_update_page, person_tasks))

    for status, name in results:
        if status == "created":
            print(f"  Created: {name}")
            created += 1
        elif status == "updated":
            print(f"  Updated: {name}")
            updated += 1
        else:
            print(f"  Failed: {name}")
            errors += 1

    removed = 0
    to_remove = {pid: page_id for pid, page_id in existing.items() if pid not in cast_people}
    if to_remove and not cast_people:
        print("SAFETY: refusing to archive all rows — source returned 0 people")
    else:
        for pid, page_id in to_remove.items():
            try:
                notion.pages.update(page_id=page_id, archived=True)
                print(f"  Archived: {pid}")
                removed += 1
            except Exception as e:
                print(f"  Failed to archive {pid}: {e}")
                errors += 1

    orphaned = set(drive_photos.keys()) - active_filenames
    for filename in orphaned:
        try:
            delete_drive_file(drive, drive_photos[filename])
        except Exception:
            pass
    if orphaned:
        print(f"Cleaned up {len(orphaned)} orphaned Drive photos")

    print(f"\nSync complete: {created} created, {updated} updated, {removed} archived, {errors} errors")
    return errors == 0


if __name__ == "__main__":
    print("=" * 60)
    print("Costumes Sync - Obsady → Aktorzy")
    print("=" * 60)

    notion = NotionAuth.get_client()
    force = "--force" in sys.argv
    changed, timestamp = check_databases_changed(
        notion, [OBSADY_DATABASE_ID, MEMBERS_DATABASE_ID], "costumes"
    )

    if not changed and not force:
        print(f"No changes since {timestamp}, skipping")
        sys.exit(0)

    print(f"Changes detected (latest edit: {timestamp})")
    success = sync()

    if success:
        set_cached_time("costumes", timestamp)

    sys.exit(0 if success else 1)
