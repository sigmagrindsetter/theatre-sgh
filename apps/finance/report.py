#!/usr/bin/env python3
"""
Generate budget report pages in Notion. Fully regenerated on each run.

Unofficial (under Budżet nieoficjalny):
- Stan konta — ogólny (overall budget summary)
- Stan konta — indywidualny (parent page with child pages per person)

Official (under Budżet oficjalny):
- Stan konta — oficjalny (per-year budget breakdown)
"""

import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from collections import defaultdict

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from shared.auth import NotionAuth
from shared.change_check import check_databases_changed, set_cached_time

BUDGET_PAGE = "2853f4160a378083a40fc21a2b774f2e"
NIEOFICJALNY_PAGE = "3403f4160a3781efb2fdd7ae99868a80"
OFICJALNY_PAGE = "3403f4160a3781f289a3d1ed5a37e272"
ZOBOWIAZANIA_DB = "3403f4160a378193a7fac6242fdbe786"
TRANSAKCJE_DB = "3403f4160a37814abb64e0517d8fa810"
MEMBERS_DB = "18a3f4160a3780c884bcd88c8e0c49b7"
OFFICIAL_DB = "3403f4160a3781ee969ddcbbee6d4bf9"
BUDGET_TABLE_DB = "3403f4160a378152b060c4af1844408e"

PAGE_TITLE_OVERALL = "Stan konta — ogólny"
PAGE_TITLE_INDIVIDUAL = "Stan konta — indywidualny"
PAGE_TITLE_OFFICIAL = "Stan konta — oficjalny"

NOW = datetime.now()
TODAY = NOW.date()
TODAY_STR = TODAY.isoformat()
NOW_FMT = NOW.strftime("%d.%m.%Y %H:%M")
LONG_TERM_DAYS = 60


# --- Data fetching ---

def query_all(client, database_id, **kwargs):
    pages = []
    has_more = True
    cursor = None
    while has_more:
        params = {"database_id": database_id, **kwargs}
        if cursor:
            params["start_cursor"] = cursor
        resp = client.databases.query(**params)
        pages.extend(resp["results"])
        has_more = resp.get("has_more", False)
        cursor = resp.get("next_cursor")
    return pages


def get_member_names(client):
    pages = query_all(client, MEMBERS_DB)
    names = {}
    for p in pages:
        title = p["properties"].get("Imię i Nazwisko", {}).get("title", [])
        name = title[0]["plain_text"].strip() if title else "?"
        names[p["id"]] = name
    return names


def extract_record(page):
    props = page["properties"]

    opis_parts = props.get("Opis", {}).get("title", [])
    opis = opis_parts[0]["plain_text"].strip() if opis_parts else ""

    kwota = props.get("Kwota", {}).get("number", 0) or 0

    kierunek_sel = props.get("Kierunek", {}).get("select")
    kierunek = kierunek_sel["name"] if kierunek_sel else ""

    typ_sel = props.get("Typ", {}).get("select")
    typ = typ_sel["name"] if typ_sel else "Inne"

    czlonek_rel = props.get("Członek", {}).get("relation", [])
    member_id = czlonek_rel[0]["id"] if czlonek_rel else None

    date_prop = props.get("Termin") or props.get("Data") or {}
    date_val = date_prop.get("date", {})
    record_date = date_val.get("start") if date_val else None

    return {
        "opis": opis,
        "kwota": kwota,
        "kierunek": kierunek,
        "typ": typ,
        "member_id": member_id,
        "date": record_date,
    }


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
    return {"opis": opis, "kwota": kwota, "typ": typ, "status": status, "rok": rok}


def get_budget_per_year(client):
    pages = query_all(client, BUDGET_TABLE_DB)
    result = {}
    for p in pages:
        title = p["properties"].get("Rok", {}).get("title", [])
        rok = title[0]["plain_text"].strip() if title else None
        kwota = p["properties"].get("Kwota", {}).get("number", 0) or 0
        if rok:
            result[rok] = kwota
    return result


# --- Computation ---

def signed(kwota, kierunek):
    """+ for Osoba→Budżet, - for Budżet→Osoba."""
    return -kwota if kierunek == "Budżet → Osoba" else kwota


def is_due(record):
    """Check if a zobowiązanie has fallen due (Termin <= today)."""
    return record["date"] and record["date"] <= TODAY_STR


def find_oldest_unpaid_date(zob_due, trans):
    """Find date of the oldest not-fully-paid due obligation.

    Walks through due obligations chronologically per direction,
    consuming available transaction sum. Returns the date of the
    first obligation not fully covered, or None.
    """
    oldest = None
    for kierunek in ["Osoba → Budżet", "Budżet → Osoba"]:
        zob_k = sorted(
            [r for r in zob_due if r["kierunek"] == kierunek],
            key=lambda r: r["date"] or "",
        )
        remaining = sum(r["kwota"] for r in trans if r["kierunek"] == kierunek)
        for r in zob_k:
            if remaining >= r["kwota"]:
                remaining -= r["kwota"]
            else:
                if r["date"] and (oldest is None or r["date"] < oldest):
                    oldest = r["date"]
                break
    return oldest


def compute_person_stats(mid, zobowiazania, transakcje):
    """Compute stats for a single person."""
    my_zob = [r for r in zobowiazania if r["member_id"] == mid]
    my_trans = [r for r in transakcje if r["member_id"] == mid]

    # Only due obligations count toward balance
    my_zob_due = [r for r in my_zob if is_due(r)]
    my_zob_future = [r for r in my_zob if not is_due(r)]

    zob_due_sum = sum(signed(r["kwota"], r["kierunek"]) for r in my_zob_due)
    trans_sum = sum(signed(r["kwota"], r["kierunek"]) for r in my_trans)
    bilans = zob_due_sum - trans_sum

    oldest_unpaid = find_oldest_unpaid_date(my_zob_due, my_trans)

    # Breakdowns by (typ, kierunek)
    def by_type(records):
        result = defaultdict(lambda: {"count": 0, "total": 0})
        for r in records:
            k = (r["typ"], r["kierunek"])
            result[k]["count"] += 1
            result[k]["total"] += r["kwota"]
        return dict(result)

    return {
        "zob_due": by_type(my_zob_due),
        "zob_future": by_type(my_zob_future),
        "trans": by_type(my_trans),
        "bilans": bilans,
        "zob_due_sum": zob_due_sum,
        "trans_sum": trans_sum,
        "oldest_unpaid": oldest_unpaid,
    }


def compute_overall(zobowiazania, transakcje, persons):
    total_ob = sum(r["kwota"] for r in transakcje if r["kierunek"] == "Osoba → Budżet")
    total_bo = sum(r["kwota"] for r in transakcje if r["kierunek"] == "Budżet → Osoba")
    saldo = total_ob - total_bo

    def by_type(records, kierunek):
        result = defaultdict(lambda: {"count": 0, "total": 0})
        for r in records:
            if r["kierunek"] == kierunek:
                result[r["typ"]]["count"] += 1
                result[r["typ"]]["total"] += r["kwota"]
        return dict(result)

    zob_due = [r for r in zobowiazania if is_due(r)]
    zob_future = [r for r in zobowiazania if not is_due(r)]

    zaleglosci_count = sum(1 for p in persons.values() if p["bilans"] > 0)
    zaleglosci_total = sum(p["bilans"] for p in persons.values() if p["bilans"] > 0)
    oldest_unpaid_all = min(
        (p["oldest_unpaid"] for p in persons.values() if p.get("oldest_unpaid")),
        default=None,
    )

    return {
        "saldo": saldo,
        "total_ob": total_ob,
        "total_bo": total_bo,
        "wplywy_by_type": by_type(transakcje, "Osoba → Budżet"),
        "wyplywy_by_type": by_type(transakcje, "Budżet → Osoba"),
        "zob_due_ob": sum(r["kwota"] for r in zob_due if r["kierunek"] == "Osoba → Budżet"),
        "zob_due_bo": sum(r["kwota"] for r in zob_due if r["kierunek"] == "Budżet → Osoba"),
        "zob_future_ob": sum(r["kwota"] for r in zob_future if r["kierunek"] == "Osoba → Budżet"),
        "zaleglosci_count": zaleglosci_count,
        "zaleglosci_total": zaleglosci_total,
        "oldest_unpaid": oldest_unpaid_all,
    }


WYDATEK_STATUSES = ["Planowane", "Zaakceptowane", "Wydane", "Zaksięgowane"]


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
            wydatki_by_status[s] = {
                "count": len(matching),
                "total": sum(r["kwota"] for r in matching),
            }

        wydatki_sum = sum(r["kwota"] for r in wydatki)
        zwroty_sum = sum(r["kwota"] for r in zwroty)

        # Spreadsheet formulas:
        # Stan konta = Środki - Suma wydatków
        # Do zwrotu = Suma wydatków - Suma zwrotów
        stan_konta = srodki - wydatki_sum
        do_zwrotu = wydatki_sum - zwroty_sum

        stats_per_year[rok] = {
            "srodki": srodki,
            "wydatki_by_status": wydatki_by_status,
            "wydatki_sum": wydatki_sum,
            "wydatki_count": len(wydatki),
            "zwroty_count": len(zwroty),
            "zwroty_sum": zwroty_sum,
            "stan_konta": stan_konta,
            "do_zwrotu": do_zwrotu,
        }

    return stats_per_year


# --- Formatting ---

def fmt(amount):
    if amount == 0:
        return "0 zł"
    sign = "+" if amount > 0 else ""
    if amount == int(amount):
        return f"{sign}{int(amount)} zł"
    return f"{sign}{amount:.2f} zł"


def fmt_abs(amount):
    if amount == int(amount):
        return f"{int(abs(amount))} zł"
    return f"{abs(amount):.2f} zł"


def fmt_plain(amount):
    """Format as number + zł, no sign prefix."""
    if amount == int(amount):
        return f"{int(amount)} zł"
    return f"{amount:.2f} zł"


def bilans_label(bilans):
    if bilans == 0:
        return "uregulowane"
    elif bilans > 0:
        return f"zaległość {fmt_abs(bilans)}"
    else:
        return f"nadpłata {fmt_abs(bilans)}"


# --- Notion block helpers ---

def text_block(content, bold=False):
    annotations = {"bold": True} if bold else {}
    return {
        "object": "block", "type": "paragraph",
        "paragraph": {
            "rich_text": [{"type": "text", "text": {"content": content}, "annotations": annotations}]
        },
    }


def heading2(content):
    return {
        "object": "block", "type": "heading_2",
        "heading_2": {"rich_text": [{"type": "text", "text": {"content": content}}]},
    }


def heading3(content):
    return {
        "object": "block", "type": "heading_3",
        "heading_3": {"rich_text": [{"type": "text", "text": {"content": content}}]},
    }


def bullet(content):
    return {
        "object": "block", "type": "bulleted_list_item",
        "bulleted_list_item": {
            "rich_text": [{"type": "text", "text": {"content": content}}]
        },
    }


def divider():
    return {"object": "block", "type": "divider", "divider": {}}


def callout(content, icon="📊"):
    return {
        "object": "block", "type": "callout",
        "callout": {
            "rich_text": [{"type": "text", "text": {"content": content}}],
            "icon": {"type": "emoji", "emoji": icon},
        },
    }


# --- Notion page management ---

def clear_page(client, page_id):
    """Delete ALL blocks from a page (handles pagination)."""
    while True:
        children = client.blocks.children.list(block_id=page_id, page_size=100)
        blocks = children["results"]
        if not blocks:
            break
        for block in blocks:
            client.blocks.delete(block_id=block["id"])


def write_blocks(client, page_id, blocks):
    for i in range(0, len(blocks), 100):
        client.blocks.children.append(block_id=page_id, children=blocks[i:i + 100])


def find_child_page(client, parent_id, title):
    """Find a child page by title under parent."""
    has_more = True
    cursor = None
    while has_more:
        params = {"block_id": parent_id, "page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        children = client.blocks.children.list(**params)
        for block in children["results"]:
            if block["type"] == "child_page" and block["child_page"]["title"] == title:
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
        properties={"title": {"title": [{"text": {"content": title}}]}},
    )
    return page["id"]


# --- Page builders ---

def build_overall_blocks(overall):
    blocks = [callout(f"Stan na: {NOW_FMT}. Generowane automatycznie.")]

    blocks.append(heading2("Saldo"))
    blocks.append(text_block(fmt(overall["saldo"]), bold=True))

    blocks.append(heading2("Wpływy (Osoba → Budżet)"))
    for typ, data in sorted(overall["wplywy_by_type"].items()):
        blocks.append(bullet(f"{typ}: {data['count']} wpłat, {fmt_abs(data['total'])}"))
    blocks.append(text_block(f"Razem: {fmt_abs(overall['total_ob'])}"))

    blocks.append(heading2("Wypływy (Budżet → Osoba)"))
    for typ, data in sorted(overall["wyplywy_by_type"].items()):
        blocks.append(bullet(f"{typ}: {data['count']} wypłat, {fmt_abs(data['total'])}"))
    blocks.append(text_block(f"Razem: {fmt_abs(overall['total_bo'])}"))

    blocks.append(heading2("Zobowiązania"))
    blocks.append(bullet(f"Zapadłe (Osoba → Budżet): {fmt_abs(overall['zob_due_ob'])}"))
    blocks.append(bullet(f"Zapadłe (Budżet → Osoba): {fmt_abs(overall['zob_due_bo'])}"))
    blocks.append(bullet(f"Przyszłe (Osoba → Budżet): {fmt_abs(overall['zob_future_ob'])}"))

    if overall["zaleglosci_count"] > 0:
        blocks.append(divider())
        msg = f"Zaległości: {overall['zaleglosci_count']} osób, łącznie {fmt_abs(overall['zaleglosci_total'])}"
        if overall.get("oldest_unpaid"):
            oldest_fmt = date.fromisoformat(overall["oldest_unpaid"]).strftime("%d.%m.%Y")
            msg += f"\nNajstarsza zaległość: {oldest_fmt}"
        blocks.append(callout(msg, "⚠️"))

    return blocks


def type_line(typ, kierunek, data):
    direction = "→ budżet" if kierunek == "Osoba → Budżet" else "→ osoba"
    return f"{typ} ({direction}): {data['count']}x, {fmt_abs(data['total'])}"


def build_person_blocks(name, stats):
    blocks = [callout(f"Stan na: {NOW_FMT}. Generowane automatycznie.")]

    if not stats:
        blocks.append(text_block("Brak zobowiązań i transakcji."))
        return blocks

    blocks.append(text_block(f"Bilans: {fmt(stats['bilans'])} ({bilans_label(stats['bilans'])})", bold=True))

    if stats.get("oldest_unpaid"):
        unpaid_fmt = date.fromisoformat(stats["oldest_unpaid"]).strftime("%d.%m.%Y")
        blocks.append(text_block(f"Najstarsza zaległość: {unpaid_fmt}"))

    # Due obligations
    if stats["zob_due"]:
        blocks.append(heading2("Zobowiązania (zapadłe)"))
        for (typ, kierunek), data in sorted(stats["zob_due"].items()):
            blocks.append(bullet(type_line(typ, kierunek, data)))

    # Future obligations
    if stats["zob_future"]:
        blocks.append(heading2("Zobowiązania (planowane)"))
        for (typ, kierunek), data in sorted(stats["zob_future"].items()):
            blocks.append(bullet(type_line(typ, kierunek, data)))

    # Transactions
    if stats["trans"]:
        blocks.append(heading2("Transakcje"))
        for (typ, kierunek), data in sorted(stats["trans"].items()):
            blocks.append(bullet(type_line(typ, kierunek, data)))

    return blocks


def build_official_blocks(stats_per_year):
    blocks = [callout(f"Stan na: {NOW_FMT}. Generowane automatycznie.")]

    for rok, stats in sorted(stats_per_year.items()):
        blocks.append(heading2(f"Rok {rok}"))
        blocks.append(text_block(f"Środki: {fmt_plain(stats['srodki'])}", bold=True))

        blocks.append(heading3("Wydatki"))
        has_status_detail = False
        for status in WYDATEK_STATUSES:
            data = stats["wydatki_by_status"][status]
            if data["count"] > 0:
                pct = (data["total"] / stats["srodki"] * 100) if stats["srodki"] else 0
                blocks.append(bullet(
                    f"{status}: {data['count']}x, {fmt_abs(data['total'])} ({pct:.1f}% budżetu)"
                ))
                has_status_detail = True
        if has_status_detail and stats["wydatki_count"] > 0:
            blocks.append(text_block(
                f"Razem: {stats['wydatki_count']}x, {fmt_abs(stats['wydatki_sum'])}", bold=True
            ))

        blocks.append(heading3("Zwroty"))
        if stats["zwroty_count"] > 0:
            blocks.append(text_block(
                f"{stats['zwroty_count']}x, {fmt_abs(stats['zwroty_sum'])}", bold=True
            ))
        else:
            blocks.append(text_block("Brak"))

        blocks.append(heading3("Podsumowanie"))
        blocks.append(bullet(f"Stan konta: {fmt_plain(stats['stan_konta'])}"))
        blocks.append(bullet(f"Do zwrotu: {fmt_plain(stats['do_zwrotu'])}"))

        blocks.append(divider())

    return blocks


# --- Main ---

def delete_old_page(client, parent_id, title):
    """Delete a child page by title if it exists (cleanup after reparenting)."""
    page_id = find_child_page(client, parent_id, title)
    if page_id:
        client.blocks.delete(block_id=page_id)
        print(f"  Deleted old '{title}' from parent")


def touch_reports(client):
    """Update timestamp callout on existing report pages without regenerating."""
    for parent_id, title in [
        (NIEOFICJALNY_PAGE, PAGE_TITLE_OVERALL),
        (NIEOFICJALNY_PAGE, PAGE_TITLE_INDIVIDUAL),
        (OFICJALNY_PAGE, PAGE_TITLE_OFFICIAL),
    ]:
        page_id = find_child_page(client, parent_id, title)
        if not page_id:
            continue
        children = client.blocks.children.list(block_id=page_id, page_size=1)
        if children["results"] and children["results"][0]["type"] == "callout":
            block = children["results"][0]
            client.blocks.update(
                block_id=block["id"],
                callout={
                    "rich_text": [{"type": "text", "text": {
                        "content": f"Sprawdzono: {NOW_FMT}. Brak zmian od ostatniego raportu.",
                    }}],
                    "icon": block["callout"]["icon"],
                },
            )
            print(f"  Touched '{title}'")


def main():
    from concurrent.futures import ThreadPoolExecutor

    client = NotionAuth.get_client()

    # --- Change detection ---
    force = "--force" in sys.argv
    all_dbs = [ZOBOWIAZANIA_DB, TRANSAKCJE_DB, MEMBERS_DB, OFFICIAL_DB, BUDGET_TABLE_DB]
    changed, timestamp = check_databases_changed(client, all_dbs, "finance")

    if not changed and not force:
        print(f"No changes since {timestamp}, skipping regeneration")
        touch_reports(client)
        return

    print(f"Changes detected (latest edit: {timestamp})")

    # --- Cleanup: remove old report pages from BUDGET_PAGE ---
    print("\n=== Cleanup old pages ===")
    delete_old_page(client, BUDGET_PAGE, PAGE_TITLE_OVERALL)
    delete_old_page(client, BUDGET_PAGE, PAGE_TITLE_INDIVIDUAL)

    # --- Fetch unofficial data ---
    print("\n=== Fetching unofficial data ===")
    member_names = get_member_names(client)
    zob_records = [extract_record(p) for p in query_all(client, ZOBOWIAZANIA_DB)]
    trans_records = [extract_record(p) for p in query_all(client, TRANSAKCJE_DB)]
    print(f"  Zobowiązania: {len(zob_records)}, Transakcje: {len(trans_records)}")

    all_mids = set()
    for r in zob_records + trans_records:
        if r["member_id"]:
            all_mids.add(r["member_id"])

    print("\n=== Computing unofficial stats ===")
    persons = {}
    for mid in all_mids:
        name = member_names.get(mid, "?")
        stats = compute_person_stats(mid, zob_records, trans_records)
        persons[mid] = {"name": name, **stats}

    overall = compute_overall(zob_records, trans_records, persons)
    print(f"  Persons: {len(persons)}, Saldo: {fmt(overall['saldo'])}")

    # --- Unofficial: overall page (under Budżet nieoficjalny) ---
    print(f"\n=== '{PAGE_TITLE_OVERALL}' (nieoficjalny) ===")
    overall_page_id = find_or_create_page(client, NIEOFICJALNY_PAGE, PAGE_TITLE_OVERALL)
    clear_page(client, overall_page_id)
    blocks = build_overall_blocks(overall)
    write_blocks(client, overall_page_id, blocks)
    print(f"  {len(blocks)} blocks")

    # --- Unofficial: individual pages (under Budżet nieoficjalny) ---
    print(f"\n=== '{PAGE_TITLE_INDIVIDUAL}' (nieoficjalny) ===")
    individual_page_id = find_or_create_page(client, NIEOFICJALNY_PAGE, PAGE_TITLE_INDIVIDUAL)

    clear_page(client, individual_page_id)
    write_blocks(client, individual_page_id, [
        callout(f"Stan na: {NOW_FMT}. Substrony generowane automatycznie."),
    ])

    # Classify members into groups
    groups = {"long": [], "short": [], "ok": []}
    for mid, name in sorted(member_names.items(), key=lambda x: x[1]):
        stats = persons.get(mid)
        if not stats:
            continue
        if stats["bilans"] > 0:
            unpaid = stats.get("oldest_unpaid")
            if unpaid and (TODAY - date.fromisoformat(unpaid)).days >= LONG_TERM_DAYS:
                groups["long"].append((mid, name, stats))
            else:
                groups["short"].append((mid, name, stats))
        else:
            groups["ok"].append((mid, name, stats))

    group_titles = [
        ("long", "Zaległości długoterminowe"),
        ("short", "Zaległości krótkoterminowe"),
        ("ok", "Opłacone w terminie"),
    ]

    for key, title in group_titles:
        if not groups[key]:
            continue
        group_page_id = find_or_create_page(client, individual_page_id, title)
        clear_page(client, group_page_id)
        write_blocks(client, group_page_id, [
            callout(f"{len(groups[key])} osób. Generowane automatycznie."),
        ])
        print(f"\n  [{title}] ({len(groups[key])})")

        # Find/create pages (sequential), then update content (parallel)
        page_tasks = []
        for mid, name, stats in groups[key]:
            person_page_id = find_or_create_page(client, group_page_id, name)
            page_tasks.append((person_page_id, name, stats))

        def _update_person(task):
            pid, n, s = task
            clear_page(client, pid)
            write_blocks(client, pid, build_person_blocks(n, s))
            return n, s["bilans"]

        with ThreadPoolExecutor(max_workers=3) as pool:
            for name, bilans in pool.map(_update_person, page_tasks):
                print(f"    {name}: {fmt(bilans)} ({bilans_label(bilans)})")

    # --- Fetch official data ---
    print("\n=== Fetching official data ===")
    budget_per_year = get_budget_per_year(client)
    official_records = [extract_official_record(p) for p in query_all(client, OFFICIAL_DB)]
    print(f"  Records: {len(official_records)}, Budget: {budget_per_year}")

    # --- Official: report page (under Budżet oficjalny) ---
    print(f"\n=== '{PAGE_TITLE_OFFICIAL}' (oficjalny) ===")
    official_stats = compute_official_stats(official_records, budget_per_year)
    official_page_id = find_or_create_page(client, OFICJALNY_PAGE, PAGE_TITLE_OFFICIAL)
    clear_page(client, official_page_id)
    blocks = build_official_blocks(official_stats)
    write_blocks(client, official_page_id, blocks)
    print(f"  {len(blocks)} blocks")

    for rok, stats in sorted(official_stats.items()):
        print(f"  {rok}: środki {fmt_plain(stats['srodki'])}, "
              f"wydatki {fmt_abs(stats['wydatki_sum'])}, "
              f"stan konta {fmt_plain(stats['stan_konta'])}, "
              f"do zwrotu {fmt_plain(stats['do_zwrotu'])}")

    set_cached_time("finance", timestamp)
    print("\nDone.")


if __name__ == "__main__":
    main()
