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
from shared.change_check import check_databases_changed, set_cached_time

import balance
from balance import BUDZET_OSOBA, is_due, signed, query_all
from account_pdf import render_history_pdf, fmt_zl, fmt_date

BUDGET_PAGE = "2853f4160a378083a40fc21a2b774f2e"
NIEOFICJALNY_PAGE = "3403f4160a3781efb2fdd7ae99868a80"
OFICJALNY_PAGE = "3403f4160a3781f289a3d1ed5a37e272"
MEMBERS_DB = "18a3f4160a3780c884bcd88c8e0c49b7"
OFFICIAL_DB = "3403f4160a3781ee969ddcbbee6d4bf9"
BUDGET_TABLE_DB = "3403f4160a378152b060c4af1844408e"

PAGE_TITLE_OVERALL = "Stan konta — ogólny"
PAGE_TITLE_INDIVIDUAL = "Stan konta — indywidualny"
PAGE_TITLE_OFFICIAL = "Stan konta — oficjalny"

DRIVE_FOLDER_NAME = "Theatre SGH - Stan konta"

NOW = datetime.now()
NOW_FMT = NOW.strftime("%d.%m.%Y %H:%M")


def text_block(content, bold=False):
    return {
        "object": "block", "type": "paragraph",
        "paragraph": {"rich_text": [{
            "type": "text", "text": {"content": content},
            "annotations": {"bold": True} if bold else {},
        }]},
    }


def heading2(content):
    return {"object": "block", "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text",
                                         "text": {"content": content}}]}}


def heading3(content):
    return {"object": "block", "type": "heading_3",
            "heading_3": {"rich_text": [{"type": "text",
                                         "text": {"content": content}}]}}


def bullet(content):
    return {"object": "block", "type": "bulleted_list_item",
            "bulleted_list_item": {"rich_text": [{"type": "text",
                                                  "text": {"content": content}}]}}


def divider():
    return {"object": "block", "type": "divider", "divider": {}}


def callout(content, icon="📊"):
    return {"object": "block", "type": "callout",
            "callout": {"rich_text": [{"type": "text",
                                       "text": {"content": content}}],
                        "icon": {"type": "emoji", "emoji": icon}}}


def file_block(url, caption):
    return {"object": "block", "type": "file",
            "file": {"type": "external", "external": {"url": url},
                     "caption": [{"type": "text", "text": {"content": caption}}]}}


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
    resp = drive.files().list(
        q=f"name='{DRIVE_FOLDER_NAME}' and "
          f"mimeType='application/vnd.google-apps.folder' and trashed=false",
        fields="files(id)").execute()
    if resp.get("files"):
        return resp["files"][0]["id"]
    meta = {"name": DRIVE_FOLDER_NAME,
            "mimeType": "application/vnd.google-apps.folder"}
    folder = drive.files().create(body=meta, fields="id").execute()
    drive.permissions().create(
        fileId=folder["id"], body={"type": "anyone", "role": "reader"}).execute()
    print(f"  Utworzono folder Drive '{DRIVE_FOLDER_NAME}'")
    return folder["id"]


def upload_pdf(drive, folder_id, pdf_bytes, filename):
    from googleapiclient.http import MediaInMemoryUpload
    resp = drive.files().list(
        q=f"name='{filename}' and '{folder_id}' in parents and trashed=false",
        fields="files(id)").execute()
    for f in resp.get("files", []):
        drive.files().delete(fileId=f["id"]).execute()
    media = MediaInMemoryUpload(pdf_bytes, mimetype="application/pdf",
                                resumable=False)
    uploaded = drive.files().create(
        body={"name": filename, "parents": [folder_id]},
        media_body=media, fields="id").execute()
    drive.permissions().create(
        fileId=uploaded["id"], body={"type": "anyone", "role": "reader"}).execute()
    return f"https://drive.google.com/file/d/{uploaded['id']}/view"


def slugify(text):
    nfkd = unicodedata.normalize("NFKD", text.lower().replace("ł", "l"))
    ascii_text = nfkd.encode("ascii", "ignore").decode("ascii")
    return "".join(c if c.isalnum() else "-" for c in ascii_text).strip("-")


def clear_page(client, page_id):
    while True:
        children = client.blocks.children.list(block_id=page_id, page_size=100)
        if not children["results"]:
            break
        for block in children["results"]:
            client.blocks.delete(block_id=block["id"])


def write_blocks(client, page_id, blocks):
    for i in range(0, len(blocks), 100):
        client.blocks.children.append(block_id=page_id, children=blocks[i:i + 100])


def find_child_page(client, parent_id, title):
    has_more, cursor = True, None
    while has_more:
        params = {"block_id": parent_id, "page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        children = client.blocks.children.list(**params)
        for block in children["results"]:
            if (block["type"] == "child_page"
                    and block["child_page"]["title"] == title):
                return block["id"]
        has_more = children.get("has_more", False)
        cursor = children.get("next_cursor")
    return None


def find_or_create_page(client, parent_id, title):
    page_id = find_child_page(client, parent_id, title)
    if page_id:
        return page_id
    page = client.pages.create(
        parent={"page_id": parent_id},
        properties={"title": {"title": [{"text": {"content": title}}]}})
    return page["id"]


def delete_old_page(client, parent_id, title):
    page_id = find_child_page(client, parent_id, title)
    if page_id:
        client.blocks.delete(block_id=page_id)
        print(f"  Usunięto starą stronę '{title}'")


def bal_word(value):
    if abs(value) < 0.005:
        return "uregulowane"
    if value > 0:
        return f"zaległość {fmt_zl(abs(value))}"
    return f"należność od budżetu {fmt_zl(abs(value))}"


def build_overall_blocks(data):
    blocks = [callout(f"Stan na: {NOW_FMT}. Generowane automatycznie.")]

    blocks.append(heading2("Saldo i należności"))
    blocks.append(bullet(
        f"Saldo obecne: {fmt_zl(data['saldo'])} — gotówka na koncie "
        f"sekretarza (suma wszystkich transakcji)."))
    blocks.append(bullet(
        f"Saldo po uiszczeniu obecnych należności: "
        f"{fmt_zl(data['saldo_po_obecnych'])} — po uregulowaniu wszystkich "
        f"zobowiązań zapadłych (Termin ≤ dziś)."))
    blocks.append(bullet(
        f"Saldo po uiszczeniu planowanych należności: "
        f"{fmt_zl(data['saldo_po_planowanych'])} — po uregulowaniu również "
        f"zobowiązań przyszłych."))

    blocks.append(heading2("Niezapłacone zobowiązania budżetu wobec osób"))
    blocks.append(text_block(
        "Przypadki, w których budżet powinien komuś zwrócić pieniądze, "
        "a jeszcze tego nie zrobił."))
    unpaid_bo = []
    for acc in data["accounts"].values():
        for u in acc["unpaid_all"]:
            if u["kierunek"] == BUDZET_OSOBA:
                unpaid_bo.append((acc["name"], u))
    unpaid_bo.sort(key=lambda x: x[1]["date"] or "9999")
    if unpaid_bo:
        for name, u in unpaid_bo:
            blocks.append(bullet(
                f"{fmt_date(u['date'])} · {u['opis']} · "
                f"{fmt_zl(u['kwota'])} · {name}"))
        total = sum(u["kwota"] for _, u in unpaid_bo)
        blocks.append(text_block(
            f"Razem do wypłacenia przez budżet: {fmt_zl(total)} "
            f"({len(unpaid_bo)} pozycji)", bold=True))
    else:
        blocks.append(text_block(
            "Brak — wszystkie zobowiązania budżetu wobec osób są rozliczone."))
    return blocks


def build_person_blocks(account, pdf_url):
    blocks = [callout(f"Stan na: {NOW_FMT}. Generowane automatycznie.")]

    bd, ba = account["balance_due"], account["balance_all"]
    blocks.append(text_block(f"Bilans zapadły: {bal_word(bd)}", bold=True))
    blocks.append(text_block(f"Bilans z planowanymi: {bal_word(ba)}"))

    blocks.append(heading2(f"Zobowiązania ({len(account['zobowiazania'])})"))
    if account["zobowiazania"]:
        for r in account["zobowiazania"]:
            mark = "" if is_due(r) else "  · planowane"
            blocks.append(bullet(
                f"{fmt_date(r['date'])} · {r['opis']} · {r['typ']} · "
                f"{fmt_zl(signed(r['kwota'], r['kierunek']))}{mark}"))
    else:
        blocks.append(text_block("Brak."))

    blocks.append(heading2(f"Transakcje ({len(account['transakcje'])})"))
    if account["transakcje"]:
        for r in account["transakcje"]:
            blocks.append(bullet(
                f"{fmt_date(r['date'])} · {r['opis']} · {r['typ']} · "
                f"{fmt_zl(signed(r['kwota'], r['kierunek']))}"))
    else:
        blocks.append(text_block("Brak."))

    blocks.append(heading2("Historia konta — PDF"))
    blocks.append(file_block(pdf_url, f"Historia konta — {account['name']}"))
    blocks.append(text_block(
        "Kwota dodatnia = należność wobec budżetu (Osoba → Budżet); "
        "ujemna = należność od budżetu (Budżet → Osoba)."))
    return blocks


WYDATEK_STATUSES = ["Planowane", "Zaakceptowane", "Wydane", "Zaksięgowane"]


def extract_official_record(page):
    props = page["properties"]
    opis_parts = props.get("Opis", {}).get("title", [])
    opis = opis_parts[0]["plain_text"].strip() if opis_parts else ""
    kwota = props.get("Kwota", {}).get("number", 0) or 0
    typ_sel = props.get("Typ", {}).get("select")
    typ = typ_sel["name"] if typ_sel else ""
    status_sel = props.get("Status", {}).get("select")
    status = status_sel["name"] if status_sel else None
    rok_sel = props.get("Rok", {}).get("select")
    rok = rok_sel["name"] if rok_sel else None
    return {"opis": opis, "kwota": kwota, "typ": typ,
            "status": status, "rok": rok}


def get_budget_per_year(client):
    result = {}
    for p in query_all(client, BUDGET_TABLE_DB):
        title = p["properties"].get("Rok", {}).get("title", [])
        rok = title[0]["plain_text"].strip() if title else None
        kwota = p["properties"].get("Kwota", {}).get("number", 0) or 0
        if rok:
            result[rok] = kwota
    return result


def compute_official_stats(records, budget_per_year):
    years = sorted(set(r["rok"] for r in records if r["rok"]))
    stats_per_year = {}
    for rok in years:
        year_records = [r for r in records if r["rok"] == rok]
        wydatki = [r for r in year_records if r["typ"] == "Wydatek"]
        zwroty = [r for r in year_records if r["typ"] == "Zwrot"]
        srodki = budget_per_year.get(rok, 0)
        wydatki_by_status = {}
        for s in WYDATEK_STATUSES:
            matching = [r for r in wydatki if r["status"] == s]
            wydatki_by_status[s] = {"count": len(matching),
                                    "total": sum(r["kwota"] for r in matching)}
        wydatki_sum = sum(r["kwota"] for r in wydatki)
        zwroty_sum = sum(r["kwota"] for r in zwroty)
        stats_per_year[rok] = {
            "srodki": srodki, "wydatki_by_status": wydatki_by_status,
            "wydatki_sum": wydatki_sum, "wydatki_count": len(wydatki),
            "zwroty_count": len(zwroty), "zwroty_sum": zwroty_sum,
            "stan_konta": srodki - wydatki_sum,
            "do_zwrotu": wydatki_sum - zwroty_sum,
        }
    return stats_per_year


def build_official_blocks(stats_per_year):
    blocks = [callout(f"Stan na: {NOW_FMT}. Generowane automatycznie.")]
    for rok, stats in sorted(stats_per_year.items()):
        blocks.append(heading2(f"Rok {rok}"))
        blocks.append(text_block(f"Środki: {fmt_zl(stats['srodki'])}", bold=True))
        blocks.append(heading3("Wydatki"))
        has_detail = False
        for status in WYDATEK_STATUSES:
            d = stats["wydatki_by_status"][status]
            if d["count"] > 0:
                pct = (d["total"] / stats["srodki"] * 100) if stats["srodki"] else 0
                blocks.append(bullet(
                    f"{status}: {d['count']}x, {fmt_zl(d['total'])} "
                    f"({pct:.1f}% budżetu)"))
                has_detail = True
        if has_detail and stats["wydatki_count"] > 0:
            blocks.append(text_block(
                f"Razem: {stats['wydatki_count']}x, "
                f"{fmt_zl(stats['wydatki_sum'])}", bold=True))
        blocks.append(heading3("Zwroty"))
        if stats["zwroty_count"] > 0:
            blocks.append(text_block(
                f"{stats['zwroty_count']}x, {fmt_zl(stats['zwroty_sum'])}",
                bold=True))
        else:
            blocks.append(text_block("Brak"))
        blocks.append(heading3("Podsumowanie"))
        blocks.append(bullet(f"Stan konta: {fmt_zl(stats['stan_konta'])}"))
        blocks.append(bullet(f"Do zwrotu: {fmt_zl(stats['do_zwrotu'])}"))
        blocks.append(divider())
    return blocks


def main():
    client = NotionAuth.get_client()

    force = "--force" in sys.argv
    all_dbs = [balance.ZOBOWIAZANIA_DB, balance.TRANSAKCJE_DB, MEMBERS_DB,
               OFFICIAL_DB, BUDGET_TABLE_DB]
    changed, timestamp = check_databases_changed(client, all_dbs, "finance")
    if not changed and not force:
        print(f"Brak zmian od {timestamp}, pomijam regenerację.")
        return
    print(f"Wykryto zmiany (ostatnia edycja: {timestamp})")

    print("\n=== Sprzątanie starych stron ===")
    delete_old_page(client, BUDGET_PAGE, PAGE_TITLE_OVERALL)
    delete_old_page(client, BUDGET_PAGE, PAGE_TITLE_INDIVIDUAL)

    print("\n=== Budżet nieoficjalny: pobieranie danych ===")
    data = balance.load(client)
    person_accounts = sorted(
        (a for a in data["accounts"].values() if a["member_id"]),
        key=lambda a: a["name"])
    print(f"  Kont osób: {len(person_accounts)}, "
          f"Saldo obecne: {fmt_zl(data['saldo'])}")

    print(f"\n=== '{PAGE_TITLE_OVERALL}' ===")
    overall_id = find_or_create_page(client, NIEOFICJALNY_PAGE, PAGE_TITLE_OVERALL)
    clear_page(client, overall_id)
    write_blocks(client, overall_id, build_overall_blocks(data))
    print(f"  Saldo po obecnych: {fmt_zl(data['saldo_po_obecnych'])}, "
          f"po planowanych: {fmt_zl(data['saldo_po_planowanych'])}")

    print(f"\n=== '{PAGE_TITLE_INDIVIDUAL}' ===")
    individual_id = find_or_create_page(
        client, NIEOFICJALNY_PAGE, PAGE_TITLE_INDIVIDUAL)
    clear_page(client, individual_id)
    write_blocks(client, individual_id, [callout(
        f"Stan na: {NOW_FMT}. Strona każdej osoby zawiera pełną historię "
        f"oraz PDF historii konta. Generowane automatycznie.")])

    drive = get_drive_service()
    folder_id = get_or_create_folder(drive)

    for acc in person_accounts:
        pdf_bytes = render_history_pdf(acc, NOW_FMT)
        url = upload_pdf(drive, folder_id, pdf_bytes,
                         f"stan-konta-{slugify(acc['name'])}.pdf")
        page = client.pages.create(
            parent={"page_id": individual_id},
            properties={"title": {"title": [{"text": {"content": acc["name"]}}]}})
        write_blocks(client, page["id"], build_person_blocks(acc, url))
        print(f"  {acc['name']}: {bal_word(acc['balance_due'])}")

    print("\n=== Budżet oficjalny ===")
    budget_per_year = get_budget_per_year(client)
    official_records = [extract_official_record(p)
                        for p in query_all(client, OFFICIAL_DB)]
    official_stats = compute_official_stats(official_records, budget_per_year)
    official_id = find_or_create_page(client, OFICJALNY_PAGE, PAGE_TITLE_OFFICIAL)
    clear_page(client, official_id)
    write_blocks(client, official_id, build_official_blocks(official_stats))
    for rok, s in sorted(official_stats.items()):
        print(f"  {rok}: stan konta {fmt_zl(s['stan_konta'])}")

    set_cached_time("finance", timestamp)
    print("\nGotowe.")


if __name__ == "__main__":
    main()
