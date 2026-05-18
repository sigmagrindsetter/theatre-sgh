#!/usr/bin/env python3
"""
Jednorazowy skrypt — zobowiązania składkowe na wyjazd WTOOPA.

Tworzy w bazie Zobowiązania jeden rekord dla każdej z 20 osób zapisanych
w kolumnie "Teatrzak" formularza (baza 3253f416...). Kwota zależy od pola
"Student członek organizacji" w bazie Członków:
  - zaznaczone  -> 100 zł
  - niezaznaczone -> 200 zł

Opis:     "Składka WTOOPA - {Imię Nazwisko}"
Typ:      Wyjazd
Kierunek: Osoba → Budżet
Termin:   2026-05-18

Uruchom bez argumentów -> podgląd (dry run).
Uruchom z  --write     -> faktyczne utworzenie rekordów.
"""

import sys
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

from shared.auth import NotionAuth

ZOBOWIAZANIA_DB = "3403f4160a378193a7fac6242fdbe786"
TERMIN = "2026-05-18"

# 20 osób z kolumny "Teatrzak" formularza, dopasowane ręcznie do bazy Członków.
# Kwota NIE jest tu zapisana — wyliczana jest z pola "Student członek organizacji".
WTOOPA_MEMBER_IDS = [
    "2923f416-0a37-80f9-b715-c7d81fc00de1",  # Adam Zienkowicz
    "1943f416-0a37-80ee-b033-c6d8a57a09b0",  # Alicja Biernat
    "2923f416-0a37-80ce-9a1c-c46fcc7dcc5e",  # Amelia Hermanowicz
    "1ab3f416-0a37-80a0-8300-c0e9cf1c247f",  # Filip Musiałowski
    "1ab3f416-0a37-80b1-9f4a-fa8a2ccacbde",  # Kornela Bagińska
    "2923f416-0a37-8052-be25-caa27b3071ae",  # Marta Węgierek
    "1ab3f416-0a37-80ef-a7d3-f7912816494b",  # Michał Kuman
    "18a3f416-0a37-8076-957e-d7ee4210fc18",  # Paweł Lesner
    "18d3f416-0a37-8049-a9f2-e0074308e0f1",  # Kasia Duk
    "1933f416-0a37-8095-8fda-dd98725330e1",  # Dorian Dymek
    "28e3f416-0a37-80b6-9117-d7b76366b89d",  # Monika Kowalska
    "1ab3f416-0a37-8098-8dd4-e8b16212a53a",  # Paweł Pawelak
    "18f3f416-0a37-80ac-9436-f7a6c9dc330a",  # Szymon Kuna
    "1913f416-0a37-80ed-8c63-c7030f64bbca",  # Natalia Kamila Oksiejczuk
    "2f63f416-0a37-8009-ae13-e5e1586cb196",  # Jan Zachariasiewicz
    "1ab3f416-0a37-80a1-b7c1-f41a1ca932d8",  # Kaja Maciejewska
    "18e3f416-0a37-80ea-abd7-c5072ef384e2",  # Wiktor Włochacz
    "1903f416-0a37-80a4-b786-f693c6cd6675",  # Wojciech Michałek
    "18c3f416-0a37-807c-92a6-f746b86358ed",  # Marta Zajder
    "18a3f416-0a37-80ec-81dc-e7f836cdf1b5",  # Stanisław Sender
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


def main():
    write = "--write" in sys.argv
    client = NotionAuth.get_client()

    # Zabezpieczenie: nie twórz duplikatów, jeśli WTOOPA już jest w bazie.
    existing = [
        p for p in query_all(client, ZOBOWIAZANIA_DB)
        if "".join(t["plain_text"] for t in
                   p["properties"].get("Opis", {}).get("title", [])
                   ).startswith("Składka WTOOPA")
    ]
    if existing:
        print(f"PRZERWANO: w bazie jest już {len(existing)} zobowiązań 'Składka WTOOPA'.")
        print("Usuń je ręcznie, jeśli chcesz wygenerować ponownie.")
        return 1

    rows = []
    for mid in WTOOPA_MEMBER_IDS:
        member = client.pages.retrieve(page_id=mid)
        props = member["properties"]
        title = props.get("Imię i Nazwisko", {}).get("title", [])
        name = (title[0]["plain_text"].strip() if title else "?")
        student = props.get("Student członek organizacji", {}).get("checkbox", False)
        amount = 100 if student else 200
        rows.append((mid, name, student, amount))

    print(f"{'Osoba':28} {'Student':8} {'Kwota':>7}")
    print("-" * 46)
    for _, name, student, amount in rows:
        print(f"{name:28} {'✓' if student else '—':8} {amount:>5} zł")
    total = sum(a for *_, a in rows)
    print("-" * 46)
    print(f"{'RAZEM ('+str(len(rows))+' osób)':28} {'':8} {total:>5} zł")

    if not write:
        print("\n[dry run] Nic nie utworzono. Uruchom z --write, aby zapisać.")
        return 0

    print("\nTworzenie rekordów...")
    created = 0
    for mid, name, _, amount in rows:
        client.pages.create(
            parent={"database_id": ZOBOWIAZANIA_DB},
            properties={
                "Opis": {"title": [{"text": {"content": f"Składka WTOOPA - {name}"}}]},
                "Kwota": {"number": amount},
                "Typ": {"select": {"name": "Wyjazd"}},
                "Kierunek": {"select": {"name": "Osoba → Budżet"}},
                "Termin": {"date": {"start": TERMIN}},
                "Członek": {"relation": [{"id": mid}]},
            },
        )
        created += 1
        print(f"  + Składka WTOOPA - {name}  ({amount} zł)")
    print(f"\nGotowe. Utworzono {created} zobowiązań.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
