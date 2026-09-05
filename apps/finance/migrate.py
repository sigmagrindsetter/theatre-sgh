#!/usr/bin/env python3
import sys
import re
from pathlib import Path
from datetime import date

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from shared.auth import NotionAuth

TRACKER_DB = "ce8e11b7b3a64c5984fe0f33608b0e56"
BUDGET_DB = "cd4b06da897842f6ad1acce19188b093"
MEMBERS_DB = "18a3f4160a3780c884bcd88c8e0c49b7"
ZOBOWIAZANIA_DB = "31b3f4160a3781e498a9c2be10bc5bce"
KSIEGA_DB = "31b3f4160a3781faae0bde8c49b1c452"

def get_obligation_months():
    today = date.today()
    current_month = date(today.year, today.month, 1)
    start = date(2025, 10, 1)
    months = []
    d = start
    while d <= current_month:
        if d.month not in (7, 8, 9):
            months.append(d)
        if d.month == 12:
            d = date(d.year + 1, 1, 1)
        else:
            d = date(d.year, d.month + 1, 1)
    return months

OBLIGATION_MONTHS = get_obligation_months()
MONTHLY_DUE = 50


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


def get_tracker_data(client):
    pages = query_all_pages(client, TRACKER_DB)
    tracker = {}
    for page in pages:
        props = page["properties"]
        pid = page["id"]

        name_parts = props.get("Osoba", {}).get("title", [])
        name = name_parts[0]["plain_text"].strip() if name_parts else "?"

        skladki_od = None
        sd = props.get("Składki od", {}).get("date")
        if sd:
            skladki_od = sd.get("start")

        member_rel = props.get("Członek", {}).get("relation", [])
        member_id = member_rel[0]["id"] if member_rel else None

        tracker[pid] = {
            "name": name,
            "skladki_od": skladki_od,
            "member_id": member_id,
        }
    print(f"Tracker: {len(tracker)} rows")
    return tracker


def get_members_data(client):
    pages = query_all_pages(client, MEMBERS_DB)
    members = {}
    for page in pages:
        props = page["properties"]
        pid = page["id"]

        name_parts = props.get("Imię i Nazwisko", {}).get("title", [])
        name = name_parts[0]["plain_text"].strip() if name_parts else "?"

        active = props.get("Aktywny", {}).get("checkbox", False)

        rola = props.get("Rola w organizacji", {})
        rola_val = ""
        if rola.get("type") == "select" and rola.get("select"):
            rola_val = rola["select"].get("name", "")
        elif rola.get("type") == "multi_select":
            rola_val = " ".join(o.get("name", "") for o in rola.get("multi_select", []))

        members[pid] = {
            "name": name,
            "active": active,
            "rola": rola_val,
        }
    print(f"Members: {len(members)} rows")
    return members


def get_budget_data(client):
    pages = query_all_pages(client, BUDGET_DB)
    entries = []
    for page in pages:
        props = page["properties"]

        opis_parts = props.get("Opis", {}).get("title", [])
        opis = opis_parts[0]["plain_text"].strip() if opis_parts else ""

        kwota = props.get("Kwota", {}).get("number")

        typ_prop = props.get("Typ", {}).get("select")
        typ = typ_prop.get("name", "") if typ_prop else ""

        data_wyst = props.get("Data wystawienia", {}).get("date")
        data_wystawienia = data_wyst.get("start") if data_wyst else None

        data_opl = props.get("Data opłacenia", {}).get("date")
        data_oplacenia = data_opl.get("start") if data_opl else None

        osoba_rel = props.get("Osoba", {}).get("relation", [])
        tracker_page_id = osoba_rel[0]["id"] if osoba_rel else None

        entries.append({
            "opis": opis,
            "kwota": kwota,
            "typ": typ,
            "data_wystawienia": data_wystawienia,
            "data_oplacenia": data_oplacenia,
            "tracker_page_id": tracker_page_id,
        })
    print(f"Budget: {len(entries)} entries")
    return entries


def create_zobowiazania(client, tracker, members):
    created = 0
    errors = 0

    for tid, tdata in tracker.items():
        member_id = tdata["member_id"]
        if not member_id:
            print(f"  Skip {tdata['name']}: no member link")
            continue

        skladki_od_str = tdata["skladki_od"]
        if not skladki_od_str:
            print(f"  Skip {tdata['name']}: no Składki od date")
            continue

        skladki_od = date.fromisoformat(skladki_od_str)
        member = members.get(member_id, {})
        name = member.get("name", tdata["name"])

        for month in OBLIGATION_MONTHS:
            if month < skladki_od:
                continue

            props = {
                "Opis": {"title": [{"text": {"content": f"Składka {month.strftime('%Y-%m')} – {name}"}}]},
                "Członek": {"relation": [{"id": member_id}]},
                "Okres": {"date": {"start": month.isoformat()}},
                "Kwota": {"number": MONTHLY_DUE},
            }
            try:
                client.pages.create(parent={"database_id": ZOBOWIAZANIA_DB}, properties=props)
                created += 1
            except Exception as e:
                print(f"  Error creating obligation for {name} {month}: {e}")
                errors += 1

    print(f"Zobowiązania: {created} created, {errors} errors")
    return errors


def extract_person_from_opis(opis):
    match = re.search(r'[–\-]\s*(.+)', opis)
    if match:
        return match.group(1).strip()
    return None


def create_ksiega(client, budget_entries, tracker, members):
    tracker_to_member = {}
    for tid, tdata in tracker.items():
        if tdata["member_id"]:
            tracker_to_member[tid] = tdata["member_id"]

    name_to_member = {}
    for mid, mdata in members.items():
        name_to_member[mdata["name"].lower()] = mid
    for tid, tdata in tracker.items():
        if tdata["member_id"]:
            name_to_member[tdata["name"].lower()] = tdata["member_id"]

    created = 0
    errors = 0

    for entry in budget_entries:
        typ = entry["typ"]
        kwota = entry["kwota"]
        if kwota is None:
            continue

        if typ == "Składka":
            if not entry["data_oplacenia"]:
                continue  # skip unpaid składki — no actual transfer happened
            kierunek = "Wpływ"
            member_id = tracker_to_member.get(entry["tracker_page_id"])
            entry_date = entry["data_oplacenia"]
        elif typ == "Wydatek":
            kierunek = "Wypływ"
            person_name = extract_person_from_opis(entry["opis"])
            member_id = None
            if person_name:
                member_id = name_to_member.get(person_name.lower())
            entry_date = entry["data_wystawienia"]
        elif typ == "Wpływ nadzwyczajny":
            kierunek = "Wpływ"
            person_name = extract_person_from_opis(entry["opis"])
            member_id = None
            if person_name:
                member_id = name_to_member.get(person_name.lower())
            entry_date = entry["data_oplacenia"] or entry["data_wystawienia"]
        else:
            print(f"  Unknown type: {typ} for '{entry['opis']}'")
            continue

        props = {
            "Opis": {"title": [{"text": {"content": entry["opis"]}}]},
            "Kwota": {"number": kwota},
            "Kierunek": {"select": {"name": kierunek}},
        }
        if entry_date:
            props["Data"] = {"date": {"start": entry_date}}
        if member_id:
            props["Członek"] = {"relation": [{"id": member_id}]}

        try:
            client.pages.create(parent={"database_id": KSIEGA_DB}, properties=props)
            created += 1
        except Exception as e:
            print(f"  Error creating '{entry['opis']}': {e}")
            errors += 1

    print(f"Księga: {created} created, {errors} errors")
    return errors


def main():
    notion = NotionAuth.get_client()

    print("=== Fetching source data ===")
    tracker = get_tracker_data(notion)
    members = get_members_data(notion)
    budget = get_budget_data(notion)

    print("\n=== Creating Zobowiązania ===")
    z_errors = create_zobowiazania(notion, tracker, members)

    print("\n=== Creating Księga ===")
    k_errors = create_ksiega(notion, budget, tracker, members)

    total_errors = z_errors + k_errors
    print(f"\n{'='*60}")
    print(f"Migration complete. Errors: {total_errors}")
    return total_errors == 0


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
