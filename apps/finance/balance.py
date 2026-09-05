from datetime import date

ZOBOWIAZANIA_DB = "3403f4160a378193a7fac6242fdbe786"
TRANSAKCJE_DB = "3403f4160a37814abb64e0517d8fa810"
MEMBERS_DB = "18a3f4160a3780c884bcd88c8e0c49b7"

OSOBA_BUDZET = "Osoba → Budżet"
BUDZET_OSOBA = "Budżet → Osoba"

TODAY = date.today().isoformat()


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


def _plain(rich):
    return "".join(x["plain_text"] for x in rich)


def _extract(page, datefield):
    p = page["properties"]
    kier = p.get("Kierunek", {}).get("select")
    typ = p.get("Typ", {}).get("select")
    rel = p.get("Członek", {}).get("relation", [])
    d = p.get(datefield, {}).get("date") or {}
    return {
        "id": page["id"],
        "opis": _plain(p.get("Opis", {}).get("title", [])),
        "kwota": p.get("Kwota", {}).get("number") or 0,
        "kierunek": kier["name"] if kier else "",
        "typ": typ["name"] if typ else "Inne",
        "member_id": rel[0]["id"] if rel else None,
        "date": d.get("start"),
    }


def signed(kwota, kierunek):
    """+ gdy gotówka wpływa do budżetu, − gdy wypływa."""
    return kwota if kierunek == OSOBA_BUDZET else -kwota


def _sortkey(r):
    return r["date"] or "9999-99-99"


def is_due(record):
    return bool(record["date"]) and record["date"] <= TODAY


def compute_unpaid(obligations, transactions):
    unpaid = []
    for kier in (OSOBA_BUDZET, BUDZET_OSOBA):
        obs = sorted((o for o in obligations if o["kierunek"] == kier), key=_sortkey)
        pool = sum(t["kwota"] for t in transactions if t["kierunek"] == kier)
        for o in obs:
            if pool >= o["kwota"]:
                pool -= o["kwota"]
            else:
                unpaid.append({**o, "kwota": round(o["kwota"] - pool, 2)})
                pool = 0
        if pool > 0.005:
            unpaid.append({
                "id": None, "opis": "Nadpłata", "typ": "Nadpłata", "date": None,
                "member_id": None, "kwota": round(pool, 2),
                "kierunek": BUDZET_OSOBA if kier == OSOBA_BUDZET else OSOBA_BUDZET,
            })
    return unpaid


def load(client):
    """Pobiera dane z Notion i liczy salda. Zwraca słownik:

    {
      members:  {member_id: imię i nazwisko},
      accounts: {member_id|None: konto},     # konto = dict opisany niżej
      zobowiazania, transakcje:  pełne listy rekordów,
      saldo, saldo_po_obecnych, saldo_po_planowanych: float,
    }

    Konto: {member_id, name, zobowiazania[], transakcje[], unpaid_due[],
            unpaid_all[], balance_due, balance_all}
    """
    members, emails = {}, {}
    for m in query_all(client, MEMBERS_DB):
        p = m["properties"]
        members[m["id"]] = _plain(
            p.get("Imię i Nazwisko", {}).get("title", [])).strip()
        email = (p.get("Adres e-mail", {}).get("email")
                 or p.get("Adres email studencki SGH", {}).get("email"))
        if not email:
            # fallback: e-mail powiązanego konta Notion (właściwość Person)
            for u in p.get("Person", {}).get("people", []):
                person_email = (u.get("person") or {}).get("email")
                if person_email:
                    email = person_email
                    break
        if email:
            emails[m["id"]] = email.strip()

    zob = [_extract(p, "Termin") for p in query_all(client, ZOBOWIAZANIA_DB)]
    trn = [_extract(p, "Data") for p in query_all(client, TRANSAKCJE_DB)]

    account_ids = {r["member_id"] for r in zob + trn}
    accounts = {}
    for aid in account_ids:
        a_zob = sorted((r for r in zob if r["member_id"] == aid), key=_sortkey)
        a_trn = sorted((r for r in trn if r["member_id"] == aid), key=_sortkey)
        due = [o for o in a_zob if is_due(o)]
        unpaid_due = compute_unpaid(due, a_trn)
        unpaid_all = compute_unpaid(a_zob, a_trn)
        accounts[aid] = {
            "member_id": aid,
            "name": (members.get(aid) or "Bez przypisanej osoby"),
            "zobowiazania": a_zob,
            "transakcje": a_trn,
            "unpaid_due": unpaid_due,
            "unpaid_all": unpaid_all,
            "balance_due": round(
                sum(signed(u["kwota"], u["kierunek"]) for u in unpaid_due), 2),
            "balance_all": round(
                sum(signed(u["kwota"], u["kierunek"]) for u in unpaid_all), 2),
        }

    saldo = round(sum(signed(t["kwota"], t["kierunek"]) for t in trn), 2)
    saldo_po_obecnych = round(
        saldo + sum(a["balance_due"] for a in accounts.values()), 2)
    saldo_po_planowanych = round(
        saldo + sum(a["balance_all"] for a in accounts.values()), 2)

    return {
        "members": members,
        "emails": emails,
        "accounts": accounts,
        "zobowiazania": zob,
        "transakcje": trn,
        "saldo": saldo,
        "saldo_po_obecnych": saldo_po_obecnych,
        "saldo_po_planowanych": saldo_po_planowanych,
    }
