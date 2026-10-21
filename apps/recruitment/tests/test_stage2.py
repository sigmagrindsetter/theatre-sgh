"""Stage II (REVIEW_PANEL_SPEC.md §11): invited candidates only, interview groups, shared averages and
anonymous comments, stage I comments without grades."""
import config
from fake_notion import title
from panel import store
from test_reviewer import (ALICE, BOB, CAROL, KANDYDACI, SAME_ORIGIN, STAGE1, X, Y, Z, alice, as_user,  # noqa: F401
                           candidate_props, grade_props, notion)

REJECTED = "55555555-5555-4555-8555-555555555555"


def signup(name, group=None, email=None, at="2026-10-19T22:00:00.000+02:00"):
    people = [{"object": "user", "id": "u", "person": {"email": email}}] if email else []
    return {"Imię i nazwisko": title(name), "Auto-osoba": {"type": "people", "people": people},
            "Wybór grupy": {"type": "select", "select": {"name": group} if group else None},
            "Auto-czas": {"type": "date", "date": {"start": at}}}


def section(html, heading):
    """The HTML from a group heading up to the next heading."""
    start = html.index(f"<h2>{heading} ")
    return html[start:html.index("<h2>", start + 1)]


def test_only_invited_candidates_are_listed_or_reachable(alice, notion):
    notion.add(KANDYDACI, candidate_props("Robert Odrzucony", "2026-09-03T10:00:00.000+02:00", "rrrrrrrrrrr", "youtube",
                                          invited=False), REJECTED)
    assert "Robert Odrzucony" not in alice.get("/").text
    assert alice.get(f"/k/{REJECTED}").status_code == 404
    response = alice.put(f"/api/oceny/{REJECTED}", json={"ocena": 5, "komentarz": ""}, headers=SAME_ORIGIN)
    assert response.status_code == 404
    assert notion.calls_of("create") == [] and notion.calls_of("update") == []
    assert all(p["total"] == 3 for p in store.progress())


def test_comments_of_both_stages_form_one_list_without_author_or_stage(alice, notion):
    notion.add(STAGE1, grade_props(X, BOB, 2, "zielony, ale ma potencjał"))
    notion.add(STAGE1, grade_props(X, CAROL, 1, "Świetna dykcja"))
    notion.add(STAGE1, grade_props(X, ALICE, 7, ""))
    notion.add(STAGE1, grade_props(Y, BOB, 4, "inny kandydat"))
    html = alice.get(f"/k/{X}").text
    comments = html.split('id="comments"')[1]
    # stage II (Bob's) and stage I comments mixed, ordered by text only
    assert ("<li>SEKRET-BARTKA</li><li>Świetna dykcja</li><li>zielony, ale ma potencjał</li></ul>"
            in comments)
    assert "etap" not in comments
    assert "inny kandydat" not in html
    assert "Bartek" not in html and "Celina" not in html
    averages = html.split('id="averages"')[1].split("</section>")[0]
    assert "<dt>II etap (z Twoją)</dt><dd><strong>6,00</strong></dd>" in averages  # stage II grades 3 and 9
    assert "<dt>I etap</dt><dd><strong>3,33</strong></dd>" in averages  # stage I grades 2, 1 and 7
    shared = averages + comments  # the 1–10 buttons above are the reviewer's own scale
    assert all(f">{g}<" not in shared and f"{g}/10" not in html for g in (1, 2, 7))
    assert "liczba ocen" not in html
    assert "Brak komentarzy." in alice.get(f"/k/{Z}").text


def test_own_comment_is_not_repeated_among_the_others(alice, notion):
    html = alice.get(f"/k/{X}").text
    assert html.count("komentarz-alicji") == 1
    assert ">komentarz-alicji</textarea>" in html
    bob = as_user(BOB, "Bartek N").get(f"/k/{X}").text
    assert "<li>komentarz-alicji</li>" in bob and "<li>SEKRET-BARTKA</li>" not in bob


def test_average_counts_every_reviewers_stage2_grade_and_own_saves_at_once(alice, notion):
    assert "<td>6,00</td>" in alice.get("/").text
    assert alice.put(f"/api/oceny/{Y}", json={"ocena": 7, "komentarz": "nowy"}, headers=SAME_ORIGIN).status_code == 200
    html = alice.get("/").text
    assert "<td>7,00</td>" in html
    assert "<li>nowy</li>" in as_user(CAROL, "Celina N").get(f"/k/{Y}").text
    assert "<dt>II etap (z Twoją)</dt><dd><strong><span class=\"muted\">—</span></strong></dd>" in alice.get(f"/k/{Z}").text


def test_list_shows_both_averages_without_counts(alice, notion):
    notion.add(STAGE1, grade_props(X, BOB, 8, ""))
    notion.add(STAGE1, grade_props(X, CAROL, 5, ""))
    html = alice.get("/").text
    assert "<th>Średnia II etap</th><th>Średnia I etap</th>" in html
    row = html[html.index(f'href="/k/{X}">Adam Pierwszy'):]
    row = row[:row.index("</tr>")]
    assert "<td>6,00</td>\n      <td>6,50</td>" in row
    assert "(2)" not in html and "liczba ocen" not in html
    assert "8/10" not in html and "5/10" not in html and row.count(" selected>") == 1  # Alicja's own 3


def test_candidates_are_grouped_by_interview_signup(alice, notion):
    notion.add(config.ZAPISY_DATABASE_ID, signup("pierwszy  ADAM", "Grupa II 18:45-19:30"))
    notion.add(config.ZAPISY_DATABASE_ID, signup("Zosia D.", "Grupa I 17:45-18:30", email="ZOF@example.com"))
    notion.add(config.ZAPISY_DATABASE_ID, signup("Nieznana Osoba", "Grupa III 19:45-20:30"))
    notion.add(config.ZAPISY_DATABASE_ID, signup(""))
    html = alice.get("/").text
    assert html.index("<h2>Grupa I 17:45-18:30") < html.index("<h2>Grupa II 18:45-19:30") < html.index("<h2>Bez grupy")
    assert "Zofia Druga" in section(html, "Grupa I 17:45-18:30")
    assert "Adam Pierwszy" in section(html, "Grupa II 18:45-19:30")
    assert "Dawid Dyskowy" in section(html, "Bez grupy")
    assert "<h2>Zapisy bez kandydata</h2>" in html and "<li>Nieznana Osoba – Grupa III 19:45-20:30</li>" in html
    assert "(bez imienia)" not in html
    assert "<dt>Rozmowa</dt><dd>Grupa II 18:45-19:30</dd>" in alice.get(f"/k/{X}").text
    assert "<dt>Rozmowa</dt><dd>Bez grupy</dd>" in alice.get(f"/k/{Z}").text


def test_name_match_handles_polish_letters_and_newest_signup_with_a_group_wins(alice, notion):
    props = candidate_props("Łucja Żółć", "2026-09-04T10:00:00.000+02:00", "lllllllllll", "youtube")
    notion.add(KANDYDACI, props, REJECTED)
    notion.add(config.ZAPISY_DATABASE_ID, signup("lucja zolc", "Grupa I 17:45-18:30", at="2026-10-19T10:00:00.000+02:00"))
    notion.add(config.ZAPISY_DATABASE_ID, signup("Łucja Żółć", "Grupa III 19:45-20:30", at="2026-10-19T11:00:00.000+02:00"))
    notion.add(config.ZAPISY_DATABASE_ID, signup("Łucja Żółć", None, at="2026-10-19T12:00:00.000+02:00"))
    chosen, unmatched = store.groups()
    assert chosen == {REJECTED: "Grupa III 19:45-20:30"} and unmatched == []


def test_unreadable_signups_still_list_every_candidate(alice, notion, monkeypatch, capsys):
    def broken():
        raise RuntimeError("Notion is down: secret detail")

    monkeypatch.setattr(store, "_signup_list", broken)
    html = alice.get("/").text
    assert "Nie udało się odczytać zapisów na rozmowy" in html
    assert all(name in html for name in ("Adam Pierwszy", "Zofia Druga", "Dawid Dyskowy"))
    assert "Bez grupy" not in html
    assert "Rozmowa" not in alice.get(f"/k/{X}").text
    out = capsys.readouterr().out
    assert "RuntimeError" in out and "secret detail" not in out


def test_list_toggles_comments_without_author_and_no_single_grade(alice, notion):
    notion.add(STAGE1, grade_props(X, BOB, 10, "STARY-KOMENTARZ"))
    notion.add(STAGE1, grade_props(Y, CAROL, 4, "o Zofii"))
    html = alice.get("/").text
    x = html[html.index(f'href="/k/{X}">Adam Pierwszy'):html.index(f'href="/k/{Y}">Zofia Druga')]
    details = x[x.index("<details>"):x.index("</details>")]
    assert "<summary>Komentarze</summary>" in details
    assert "<ul class=\"comments\"><li>SEKRET-BARTKA</li><li>STARY-KOMENTARZ</li></ul>" in details
    assert "etap" not in details
    assert "komentarz-alicji" not in html  # own comment lives in the card's text box
    assert "o Zofii" not in x and "<li>o Zofii</li>" in html
    candidates = html.split("Postęp oceniania")[0]  # the progress table names reviewers, with counts only
    assert "Bartek" not in candidates and "Celina" not in candidates
    assert "9/10" not in html and "10/10" not in html and "4/10" not in html
    assert html.count(" selected>") == 1  # only Alicja's own 3
    z = html[html.index(f'href="/k/{Z}">Dawid Dyskowy'):]
    assert "Brak komentarzy" in z[:z.index("</tr>", z.index("</tr>") + 1)]
