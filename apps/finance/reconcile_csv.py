#!/usr/bin/env python3
import re
import sys
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from shared.auth import NotionAuth

ZOBOWIAZANIA_DB = "3403f4160a378193a7fac6242fdbe786"
TRANSAKCJE_DB = "3403f4160a37814abb64e0517d8fa810"

KUNA_ID = "18f3f416-0a37-80ac-9436-f7a6c9dc330a"  # Sokołów: nieopłacone wg CSV

MONTHS_PL = {1: "styczeń", 2: "luty", 3: "marzec", 4: "kwiecień", 5: "maj",
             6: "czerwiec", 10: "październik", 11: "listopad", 12: "grudzień"}

SKLADKI = [
    ("1933f416-0a37-8095-8fda-dd98725330e1", "Dymek Dorian",     "2026-04"),
    ("1ab3f416-0a37-80a1-b7c1-f41a1ca932d8", "Maciejewska Kaja", "2026-05"),
    ("18d3f416-0a37-8049-a9f2-e0074308e0f1", "Duk Kasia",        "2026-03"),
    ("18d3f416-0a37-8049-a9f2-e0074308e0f1", "Duk Kasia",        "2026-04"),
    ("1ab3f416-0a37-80b1-9f4a-fa8a2ccacbde", "Bagińska Kornela", "2026-04"),
    ("1ab3f416-0a37-80b1-9f4a-fa8a2ccacbde", "Bagińska Kornela", "2026-05"),
    ("1ab3f416-0a37-80ef-a7d3-f7912816494b", "Kuman Michał",     "2026-03"),
    ("1ab3f416-0a37-80ef-a7d3-f7912816494b", "Kuman Michał",     "2026-04"),
    ("1ab3f416-0a37-8077-bca9-eb47ab24dd4d", "Ciszewski Piotr",  "2026-03"),
    ("1ab3f416-0a37-8077-bca9-eb47ab24dd4d", "Ciszewski Piotr",  "2026-05"),
    ("18f3f416-0a37-80ac-9436-f7a6c9dc330a", "Kuna Szymon",      "2026-01"),
    ("18f3f416-0a37-80ac-9436-f7a6c9dc330a", "Kuna Szymon",      "2026-02"),
    ("18f3f416-0a37-80ac-9436-f7a6c9dc330a", "Kuna Szymon",      "2026-03"),
    ("18f3f416-0a37-80ac-9436-f7a6c9dc330a", "Kuna Szymon",      "2026-04"),
    ("18e3f416-0a37-80fa-9e93-d261118a5ab6", "Grygo Marcin",     "2026-03"),
    ("18e3f416-0a37-80fa-9e93-d261118a5ab6", "Grygo Marcin",     "2026-04"),
]

WYDATKI = [
    ("Odkup butów od Ani",                       "2026-04-13", 100),
    ("Dokup rekwizytów",                         "2026-04-16", 165),
    ("Kije od flag",                             "2026-04-17", 30),
    ("Kosmetyki mieszcańskie",                   "2026-04-19", 18.28),
    ("Transport rekwizytów- premiera",           "2026-04-19", 304),
    ("Zapłata dla Franka za szermierkę",         "2026-04-19", 200),
    ("Kwiaty na prapremierę",                    "2026-04-21", 366),
    ("Psikadło do Mieszczanina(Opłacił Wojtek)", "2026-05-13", 40),
]


def query_all(client, db):
    out, cur, more = [], None, True
    while more:
        params = {"database_id": db}
        if cur:
            params["start_cursor"] = cur
        r = client.databases.query(**params)
        out.extend(r["results"])
        more = r.get("has_more", False)
        cur = r.get("next_cursor")
    return out


def title_of(page):
    t = page["properties"].get("Opis", {}).get("title", [])
    return "".join(x["plain_text"] for x in t)


def sel_of(page, key):
    s = page["properties"].get(key, {}).get("select")
    return s["name"] if s else None


def member_of(page):
    rel = page["properties"].get("Członek", {}).get("relation", [])
    return rel[0]["id"] if rel else None


def kwota_of(page):
    return page["properties"].get("Kwota", {}).get("number")


def main():
    write = "--write" in sys.argv
    client = NotionAuth.get_client()

    zob = query_all(client, ZOBOWIAZANIA_DB)
    trn = query_all(client, TRANSAKCJE_DB)

    zob_skladka = {}
    for z in zob:
        if sel_of(z, "Typ") == "Składka" and member_of(z):
            mp = re.search(r"\d{4}-\d{2}", title_of(z))
            if mp:
                zob_skladka[(member_of(z), mp.group(0))] = z
    trn_skladka = set()
    for t in trn:
        if sel_of(t, "Typ") == "Składka" and member_of(t):
            d = (t["properties"].get("Data", {}).get("date") or {}).get("start")
            if d:
                trn_skladka.add((member_of(t), d[:7]))

    plan = []

    print("=" * 64)
    print("1. SKŁADKI — brakujące transakcje miesięczne")
    print("=" * 64)
    for mid, name, period in SKLADKI:
        m = int(period[5:7])
        opis = f"Składka {MONTHS_PL[m]} – {name}"
        if (mid, period) not in zob_skladka:
            print(f"  ! POMINIĘTO {opis}: brak zobowiązania dla {period}")
            continue
        if (mid, period) in trn_skladka:
            print(f"  = już jest: {opis}")
            continue
        props = {
            "Opis": {"title": [{"text": {"content": opis}}]},
            "Kwota": {"number": 50},
            "Kierunek": {"select": {"name": "Osoba → Budżet"}},
            "Typ": {"select": {"name": "Składka"}},
            "Data": {"date": {"start": f"{period}-01"}},
            "Członek": {"relation": [{"id": mid}]},
        }
        plan.append(("transakcja", f"{opis}  (50 zł, {period}-01)", props, TRANSAKCJE_DB))
        print(f"  + {opis}  (50 zł)")

    print("=" * 64)
    print("2. SOKOŁÓW — brakujące transakcje wyjazdowe")
    print("=" * 64)
    sok_zob = [z for z in zob if sel_of(z, "Typ") == "Wyjazd"
               and title_of(z).startswith("Składka Sokołów")]
    sok_trn_opis = {title_of(t) for t in trn if sel_of(t, "Typ") == "Wyjazd"}
    for z in sorted(sok_zob, key=title_of):
        opis = title_of(z)
        if opis in sok_trn_opis:
            print(f"  = już jest: {opis}")
            continue
        if member_of(z) == KUNA_ID:
            print(f"  - pominięto (CSV: nieopłacone): {opis}")
            continue
        kwota = kwota_of(z)
        props = {
            "Opis": {"title": [{"text": {"content": opis}}]},
            "Kwota": {"number": kwota},
            "Kierunek": {"select": {"name": "Osoba → Budżet"}},
            "Typ": {"select": {"name": "Wyjazd"}},
            "Data": {"date": {"start": "2026-03-20"}},
            "Członek": {"relation": [{"id": member_of(z)}]},
        }
        plan.append(("transakcja", f"{opis}  ({kwota} zł)", props, TRANSAKCJE_DB))
        print(f"  + {opis}  ({kwota} zł)")

    print("=" * 64)
    print("3. WYDATKI — nowe zakupy (zobowiązanie + transakcja, Członek pusty)")
    print("=" * 64)
    zak_zob_opis = {title_of(z) for z in zob if sel_of(z, "Typ") == "Zakup"}
    for opis, data, kwota in WYDATKI:
        if opis in zak_zob_opis:
            print(f"  = już jest: {opis}")
            continue
        zob_props = {
            "Opis": {"title": [{"text": {"content": opis}}]},
            "Kwota": {"number": kwota},
            "Kierunek": {"select": {"name": "Budżet → Osoba"}},
            "Typ": {"select": {"name": "Zakup"}},
            "Termin": {"date": {"start": data}},
        }
        trn_props = {
            "Opis": {"title": [{"text": {"content": opis}}]},
            "Kwota": {"number": kwota},
            "Kierunek": {"select": {"name": "Budżet → Osoba"}},
            "Typ": {"select": {"name": "Zakup"}},
            "Data": {"date": {"start": data}},
        }
        plan.append(("zobowiązanie", f"{opis}  ({kwota} zł, {data})", zob_props, ZOBOWIAZANIA_DB))
        plan.append(("transakcja", f"{opis}  ({kwota} zł, {data})", trn_props, TRANSAKCJE_DB))
        print(f"  + zobowiązanie + transakcja: {opis}  ({kwota} zł)")

    n_zob = sum(1 for k, *_ in plan if k == "zobowiązanie")
    n_trn = sum(1 for k, *_ in plan if k == "transakcja")
    print("=" * 64)
    print(f"PLAN: {n_zob} zobowiązań + {n_trn} transakcji = {len(plan)} rekordów")

    if not write:
        print("\n[dry run] Nic nie utworzono. Uruchom z --write, aby zapisać.")
        return 0

    print("\nTworzenie rekordów...")
    for kind, label, props, db in plan:
        client.pages.create(parent={"database_id": db}, properties=props)
        print(f"  + [{kind}] {label}")
    print(f"\nGotowe. Utworzono {len(plan)} rekordów.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
