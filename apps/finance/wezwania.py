#!/usr/bin/env python3
"""
Wezwania do uregulowania salda składkowego — wysyłka e-mailem.

Funkcja klikana ręcznie w GitHub Actions (workflow_dispatch → wezwania.yml).
Dla każdej osoby z dodatnim saldem (zaległość > 0) generuje pseudodokument PDF
(snapshot z chwili uruchomienia) i wysyła go mailem.

Argumenty:  python wezwania.py <tryb> [<limit>]
  tryb:
    dry-run  — generuje i wypisuje, NIC nie wysyła (nie wymaga hasła)
    test     — wysyła WSZYSTKIE wezwania na jeden adres testowy
    live     — wysyła do każdej osoby na jej adres z bazy Członków
  limit (opcjonalnie) — przetwórz tylko pierwszych N osób

Sekrety (env):
  NOTION_API_TOKEN    — token Notion (jest już w repo)
  GMAIL_APP_PASSWORD  — hasło aplikacji Gmail konta nadawcy (wymagane dla test/live)
"""

import os
import smtplib
import sys
import unicodedata
from datetime import datetime
from email.message import EmailMessage
from pathlib import Path

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))
sys.path.insert(0, str(Path(__file__).parent))

from shared.auth import NotionAuth

import balance
from account_pdf import (SECRETARY_NAME, SECRETARY_PHONE, fmt_date, fmt_zl,
                         render_wezwanie_pdf)

GMAIL_ACCOUNT = os.getenv("GMAIL_SENDER", "teatr.sgh@gmail.com")
# Adres nadawcy z dopiskiem windykacyjnym (Gmail sub-addressing przez "+").
# Logowanie SMTP odbywa się na konto bazowe — "+windykacja" to ten sam adres
# i ta sama skrzynka, więc hasło aplikacji działa bez zmian; w polu "Od"
# odbiorca widzi dopisek windykacyjny.
_acc_local, _, _acc_domain = GMAIL_ACCOUNT.partition("@")
FROM_ADDR = f"{_acc_local}+windykacja@{_acc_domain}"
FROM_HEADER = f"Teatr SGH — Windykacja składkowa <{FROM_ADDR}>"
TEST_RECIPIENT = "ss114165@student.sgh.waw.pl"
SUBJECT = "Wezwanie do uregulowania składek — Teatr Scena Główna Handlowa"
SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465


def slugify(text):
    nfkd = unicodedata.normalize("NFKD", text.lower().replace("ł", "l"))
    ascii_text = nfkd.encode("ascii", "ignore").decode("ascii")
    return "".join(c if c.isalnum() else "-" for c in ascii_text).strip("-")


def build_body(account, generated_str, test_for=None):
    """Treść maila. Zawiera zaległą kwotę i zestawienie pozycji."""
    first = account["name"].split()[0]
    total = account["balance_due"]
    owed = [u for u in account["unpaid_due"]
            if u["kierunek"] == balance.OSOBA_BUDZET]
    credit = [u for u in account["unpaid_due"]
              if u["kierunek"] == balance.BUDZET_OSOBA]

    lines = []
    if test_for:
        lines += [f"[WIADOMOŚĆ TESTOWA — docelowy adresat: {test_for}]", ""]
    lines += [
        f"Cześć {first}!",
        "",
        f"Według stanu rozliczeń Budżetu nieoficjalnego Teatru Scena Główna "
        f"Handlowa z dnia {generated_str} na Twoim koncie składkowym widnieje "
        f"nieuregulowana zaległość.",
        "",
        f"    ZALEGŁA KWOTA DO OPŁACENIA: {fmt_zl(total)}",
        "",
        "Zestawienie zaległych pozycji:",
    ]
    for u in owed:
        lines.append(f"  • {fmt_date(u['date'])} · {u['opis']} — {fmt_zl(u['kwota'])}")
    for u in credit:
        lines.append(f"  • (zwrot należny od budżetu) {u['opis']} "
                      f"— −{fmt_zl(u['kwota'])}")
    lines += [
        "",
        "Prosimy o uregulowanie należności przelewem na telefon / BLIK-iem "
        "na konto sekretarza:",
        f"    {SECRETARY_NAME}, tel. {SECRETARY_PHONE}",
        f"    Tytuł przelewu: składki — {account['name']}",
        "",
        "W załączonym dokumencie PDF znajduje się pełna historia płatności "
        "oraz objaśnienie sposobu wyliczenia zaległości.",
        "",
        "W razie jakichkolwiek wątpliwości co do rozliczenia prosimy "
        "o wyjaśnienie sprawy z sekretarzem.",
        "",
        "Pozdrawiamy,",
        "Sekretariat Teatru Scena Główna Handlowa",
    ]
    return "\n".join(lines)


def build_message(account, generated_str, to_addr, test_for=None):
    msg = EmailMessage()
    msg["From"] = FROM_HEADER
    msg["To"] = to_addr
    subject = SUBJECT
    if test_for:
        subject = f"[TEST] {subject}"
    msg["Subject"] = subject
    msg.set_content(build_body(account, generated_str, test_for))
    pdf = render_wezwanie_pdf(account, generated_str)
    msg.add_attachment(pdf, maintype="application", subtype="pdf",
                       filename=f"wezwanie-{slugify(account['name'])}.pdf")
    return msg


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "dry-run"
    limit = None
    if len(sys.argv) > 2 and sys.argv[2].strip():
        limit = int(sys.argv[2].strip())
    if mode not in ("dry-run", "test", "live"):
        print(f"Nieznany tryb: {mode!r}. Użyj: dry-run | test | live")
        return 1

    client = NotionAuth.get_client()
    data = balance.load(client)
    generated_str = datetime.now().strftime("%d.%m.%Y %H:%M")

    debtors = sorted(
        (a for a in data["accounts"].values()
         if a["member_id"] and a["balance_due"] > 0.005),
        key=lambda a: a["name"])
    if limit is not None:
        debtors = debtors[:limit]

    total_debt = sum(a["balance_due"] for a in debtors)
    print(f"Tryb: {mode}")
    print(f"Osób z zaległością: {len(debtors)}, łączna kwota "
          f"{fmt_zl(total_debt)}\n")

    for a in debtors:
        addr = data["emails"].get(a["member_id"], "(brak adresu e-mail)")
        print(f"  {a['name']:28} {fmt_zl(a['balance_due']):>13}   {addr}")

    if mode == "dry-run":
        print("\n[dry-run] Nic nie wysłano. Tryby wysyłki: test, live.")
        return 0

    password = os.getenv("GMAIL_APP_PASSWORD")
    if not password:
        print("\nBŁĄD: brak sekretu GMAIL_APP_PASSWORD — nie mogę wysłać maili.")
        print("Dodaj hasło aplikacji Gmail jako sekret repozytorium (instrukcja "
              "w opisie workflow).")
        return 1

    print(f"\nŁączenie z {SMTP_HOST} jako {GMAIL_ACCOUNT} (Od: {FROM_ADDR})...")
    sent, failed = 0, 0
    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as smtp:
        smtp.login(GMAIL_ACCOUNT, password)
        for a in debtors:
            real = data["emails"].get(a["member_id"])
            if mode == "live" and not real:
                print(f"  ! POMINIĘTO {a['name']}: brak adresu e-mail")
                failed += 1
                continue
            to_addr = TEST_RECIPIENT if mode == "test" else real
            test_for = real or "(brak adresu)" if mode == "test" else None
            try:
                msg = build_message(a, generated_str, to_addr, test_for)
                smtp.send_message(msg)
                print(f"  → {a['name']:28} {fmt_zl(a['balance_due']):>13}  "
                      f"wysłano na {to_addr}")
                sent += 1
            except Exception as e:
                print(f"  ! BŁĄD {a['name']}: {e}")
                failed += 1

    print(f"\nGotowe. Wysłano: {sent}, błędów: {failed}.")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
