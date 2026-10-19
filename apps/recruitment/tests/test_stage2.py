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


def test_stage1_comments_show_without_author_and_stage1_grades_never(alice, notion):
    notion.add(STAGE1, grade_props(X, BOB, 2, "zielony, ale ma potencjał"))
    notion.add(STAGE1, grade_props(X, CAROL, 1, "Świetna dykcja"))
    notion.add(STAGE1, grade_props(X, ALICE, 7, ""))
    notion.add(STAGE1, grade_props(Y, BOB, 4, "inny kandydat"))
    html = alice.get(f"/k/{X}").text
    stage1 = html.split('id="stage1"')[1]
    assert "<li>Świetna dykcja</li><li>zielony, ale ma potencjał</li>" in stage1  # by text, not by author
    assert "inny kandydat" not in html
    assert "Bartek" not in html and "Celina" not in html
    assert "6,00" in html and "liczba ocen: 2" in html  # stage II grades 3 and 9; stage I's 2, 1, 7 left out
    assert "Brak komentarzy z I etapu." in alice.get(f"/k/{Z}").text


def test_own_comment_is_not_repeated_among_the_others(alice, notion):
    html = alice.get(f"/k/{X}").text
    assert html.count("komentarz-alicji") == 1
    assert ">komentarz-alicji</textarea>" in html
    bob = as_user(BOB, "Bartek N").get(f"/k/{X}").text
    assert "<li>komentarz-alicji</li>" in bob and "<li>SEKRET-BARTKA</li>" not in bob


def test_average_counts_every_reviewers_stage2_grade_and_own_saves_at_once(alice, notion):
    assert "6,00 <span class=\"muted\">(2)</span>" in alice.get("/").text
    assert alice.put(f"/api/oceny/{Y}", json={"ocena": 7, "komentarz": "nowy"}, headers=SAME_ORIGIN).status_code == 200
    html = alice.get("/").text
    assert "7,00 <span class=\"muted\">(1)</span>" in html
    assert "<li>nowy</li>" in as_user(CAROL, "Celina N").get(f"/k/{Y}").text
    assert "brak ocen" in alice.get(f"/k/{Z}").text


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


def test_list_shows_no_comment_and_no_single_grade_of_another_reviewer(alice, notion):
    notion.add(STAGE1, grade_props(X, BOB, 10, "STARY-KOMENTARZ"))
    html = alice.get("/").text
    assert "SEKRET-BARTKA" not in html and "STARY-KOMENTARZ" not in html
    assert "9/10" not in html and "10/10" not in html
