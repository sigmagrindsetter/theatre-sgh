"""Editing from the candidate list (REVIEW_PANEL_SPEC.md §11): own stage II grade, any number of new comments."""
import pytest

from fake_notion import people, text, title
from test_reviewer import (ALICE, ALICE_X_ROW, BOB, KANDYDACI, KOMENTARZE, SAME_ORIGIN, X, Y, Z, alice,  # noqa: F401
                           as_user, candidate_props, notion, row)

REJECTED = "55555555-5555-4555-8555-555555555555"


def comment_props(cid, uid, komentarz, klucz):
    return {"Nazwa": title(f"{cid} — {uid}"), "Kandydat": {"type": "relation", "relation": [{"id": cid}]},
            "Oceniający": people(uid), "Komentarz": text(komentarz),
            "Czas": {"type": "date", "date": {"start": "2026-10-21T18:00:00+02:00"}}, "Klucz": text(klucz)}


def toggle(html, cid):
    start = html.index(f'data-id="{cid}"', html.index(f'href="/k/{cid}">'))
    details = html[html.index("<details>", start):]
    return details[:details.index("</details>")]


def test_grade_from_the_list_keeps_the_comment_and_counts_at_once(alice, notion):
    assert alice.put(f"/api/oceny/{X}", json={"ocena": 8}, headers=SAME_ORIGIN).status_code == 200
    [(_, page_id, props)] = notion.calls_of("update")
    assert page_id == ALICE_X_ROW and "Komentarz" not in props
    assert row(notion, ALICE_X_ROW)["properties"]["Ocena"]["number"] == 8
    assert "komentarz-alicji" in text_of(row(notion, ALICE_X_ROW)["properties"]["Komentarz"])
    html = alice.get("/").text
    assert '<option value="8" selected>8</option>' in html and "<td>8,50</td>" in html
    assert "<li>komentarz-alicji</li>" in as_user(BOB, "Bartek N").get(f"/k/{X}").text


def test_every_save_counts_at_once_not_only_the_first(alice, notion):
    for ocena in (7, 9, None):
        assert alice.put(f"/api/oceny/{Y}", json={"ocena": ocena}, headers=SAME_ORIGIN).status_code == 200
        html = alice.get("/").text
        y = html[html.index(f'href="/k/{Y}">'):]
        y = y[:y.index("</tr>")]
        if ocena is None:
            assert " selected>" not in y and '<span class="muted">—</span>' in y
        else:
            assert f'<option value="{ocena}" selected>' in y and f"<td>{ocena},00</td>" in y
    assert len(notion.calls_of("create")) == 1


def test_comments_are_added_many_per_reviewer_and_shown_without_author(alice, notion):
    for komentarz in ("  pierwszy  ", "drugi"):
        response = alice.post(f"/api/komentarze/{X}", json={"komentarz": komentarz}, headers=SAME_ORIGIN)
        assert response.status_code == 200 and response.json()["saved_at"]
    creates = notion.calls_of("create")
    assert [c[1] for c in creates] == [KOMENTARZE, KOMENTARZE]
    keys = {c[2]["Klucz"]["rich_text"][0]["text"]["content"] for c in creates}
    assert len(keys) == 2
    for _, _, props in creates:
        assert props["Kandydat"] == {"relation": [{"id": X}]} and props["Oceniający"] == {"people": [{"id": ALICE}]}
    assert creates[0][2]["Komentarz"]["rich_text"][0]["text"]["content"] == "pierwszy"

    # the rows lag in queries; the author sees them at once, sorted in with the rest
    assert "<li>drugi</li><li>pierwszy</li><li>SEKRET-BARTKA</li>" in toggle(alice.get("/").text, X)
    notion.settle()
    bob = as_user(BOB, "Bartek N").get(f"/k/{X}").text
    assert "<li>drugi</li>" in bob and "<li>pierwszy</li>" in bob and "Alicja" not in bob


def test_comments_from_notion_count_once_and_only_live_rows(alice, notion):
    notion.add(KOMENTARZE, comment_props(X, BOB, "od-bartka", "k1"))
    notion.add(KOMENTARZE, comment_props(X, BOB, "od-bartka", "k1"))  # twin of a create resent after a 5xx
    notion.add(KOMENTARZE, comment_props(X, BOB, "drugi-od-bartka", "k2"))
    notion.add(KOMENTARZE, comment_props(Y, BOB, "o-zofii", "k3"))
    notion.add(KOMENTARZE, comment_props(X, BOB, "usuniety", "k4"))["archived"] = True
    details = toggle(alice.get("/").text, X)
    assert details.count("<li>od-bartka</li>") == 1 and "<li>drugi-od-bartka</li>" in details
    assert "o-zofii" not in details and "usuniety" not in details


def test_toggle_without_comments_still_has_the_form(alice):
    details = toggle(alice.get("/").text, Z)
    assert "<summary>Komentarze</summary>" in details and "Brak komentarzy" in details
    assert f'<form class="add-comment" method="post" data-id="{Z}">' in details


@pytest.mark.parametrize("body", [{"komentarz": ""}, {"komentarz": "   "}, {"komentarz": "x" * 2001}, {},
                                  {"komentarz": 5}, {"komentarz": "ok", "ocena": 3}])
def test_comment_rejects_invalid_bodies(alice, notion, body):
    assert alice.post(f"/api/komentarze/{X}", json=body, headers=SAME_ORIGIN).status_code == 422
    assert notion.calls_of("create") == []


def test_comment_needs_same_origin_member_and_invited_candidate(alice, notion):
    notion.add(KANDYDACI, candidate_props("Robert Odrzucony", "2026-09-03T10:00:00.000+02:00", "rrrrrrrrrrr",
                                          "youtube", invited=False), REJECTED)
    body = {"komentarz": "x"}
    assert alice.post(f"/api/komentarze/{X}", json=body, headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert alice.post(f"/api/komentarze/{X}", content="komentarz=x", headers=SAME_ORIGIN).status_code == 415
    assert alice.post(f"/api/komentarze/{REJECTED}", json=body, headers=SAME_ORIGIN).status_code == 404
    assert as_user("e0000000-0000-4000-8000-000000000005", "Ewa").post(
        f"/api/komentarze/{X}", json=body, headers=SAME_ORIGIN).status_code == 403
    assert notion.calls_of("create") == []


def text_of(prop):
    return "".join(t["plain_text"] for t in prop["rich_text"])
