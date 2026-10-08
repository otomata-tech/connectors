"""Synthèse des réponses Typeform : agrégat déterministe et lecture bornée.

`summarize` est pur : on le joue sur un formulaire et des réponses écrits ici.
`summarize_responses` est joué contre un transport simulé (aucun réseau) :
pages lues par curseur `before`, arrêt à la dernière page, à `total_items` ou
à `max_pages`, et `truncated` qui dit qu'on s'est arrêté avant la fin.
"""
import json

import pytest

from oto.tools.typeform import TypeformClient
from oto.tools.typeform.stats import OTHER, summarize

FORM = {
    "id": "f1", "title": "Feedback",
    "fields": [
        {"id": "nps", "ref": "nps", "title": "Recommend?", "type": "nps"},
        {"id": "mc", "title": "Session", "type": "multiple_choice",
         "properties": {"choices": [{"label": "Morning"}, {"label": "Afternoon"}, {"label": "Evening"}]}},
        {"id": "st", "title": "Thanks", "type": "statement"},
        {"id": "grp", "title": "About you", "type": "group",
         "properties": {"fields": [
             {"id": "yn", "title": "First time?", "type": "yes_no"},
             {"id": "txt", "title": "Comment", "type": "long_text"}]}},
        {"id": "rk", "title": "Rank", "type": "ranking"},
        {"id": "multi", "title": "Topics", "type": "multiple_choice",
         "properties": {"choices": [{"label": "A"}, {"label": "B"}]}},
    ],
}


def _r(token, day, answers, score=None):
    out = {"response_id": token, "token": token, "landed_at": f"{day}T08:00:00Z",
           "submitted_at": f"{day}T09:00:00Z", "answers": answers}
    if score is not None:
        out["calculated"] = {"score": score}
    return out


RESPONSES = [
    _r("t1", "2026-10-02", [
        {"field": {"id": "nps", "type": "nps"}, "type": "number", "number": 10},
        {"field": {"id": "mc", "type": "multiple_choice"}, "type": "choice", "choice": {"label": "Morning"}},
        {"field": {"id": "yn", "type": "yes_no"}, "type": "boolean", "boolean": True},
        {"field": {"id": "txt", "type": "long_text"}, "type": "text", "text": "secret opinion"},
        {"field": {"id": "rk", "type": "ranking"}, "type": "choices", "choices": {"labels": ["X", "Y"]}},
        {"field": {"id": "multi", "type": "multiple_choice"}, "type": "choices",
         "choices": {"labels": ["A", "B"], "other": "C"}},
    ], score=3),
    _r("t2", "2026-10-02", [
        {"field": {"id": "nps", "type": "nps"}, "type": "number", "number": 5},
        {"field": {"id": "mc", "type": "multiple_choice"}, "type": "choice", "choice": {"other": "Night"}},
        {"field": {"id": "yn", "type": "yes_no"}, "type": "boolean", "boolean": False},
        {"field": {"id": "rk", "type": "ranking"}, "type": "choices", "choices": {"labels": ["Y", "X"]}},
        {"field": {"id": "gone", "type": "short_text", "ref": "old"}, "type": "text", "text": "x"},
    ], score=1),
    _r("t3", "2026-10-01", [
        {"field": {"id": "nps", "type": "nps"}, "type": "number", "number": 8},
        {"field": {"id": "mc", "type": "multiple_choice"}, "type": "choice", "choice": {"label": "Morning"}},
        {"field": {"id": "rk", "type": "ranking"}, "type": "choices", "choices": {"labels": ["X", "Y"]}},
    ]),
]


def _by_id(summary):
    return {f["id"]: f for f in summary["fields"]}


def test_ordre_du_formulaire_sans_enonces_ni_groupes():
    ids = [f["id"] for f in summarize(FORM, RESPONSES)["fields"]]
    assert ids == ["nps", "mc", "yn", "txt", "rk", "multi", "gone"]


def test_taux_de_reponse_par_question():
    f = _by_id(summarize(FORM, RESPONSES))
    assert (f["nps"]["answered"], f["nps"]["answer_rate"]) == (3, 1.0)
    assert (f["txt"]["answered"], f["txt"]["answer_rate"]) == (1, 0.3333)
    assert (f["multi"]["answered"], f["multi"]["answer_rate"]) == (1, 0.3333)
    assert f["yn"]["parent_id"] == "grp"


def test_repartition_des_choix_avec_les_choix_jamais_pris_et_autre():
    mc = _by_id(summarize(FORM, RESPONSES))["mc"]
    assert mc["choices"] == [
        {"label": "Morning", "count": 2, "share": 0.6667},
        {"label": "Afternoon", "count": 0, "share": 0.0},
        {"label": "Evening", "count": 0, "share": 0.0},
        {"label": OTHER, "count": 1, "share": 0.3333},
    ]
    multi = _by_id(summarize(FORM, RESPONSES))["multi"]
    assert [(c["label"], c["count"]) for c in multi["choices"]] == [("A", 1), ("B", 1), (OTHER, 1)]


def test_notes_moyenne_distribution_et_nps():
    nps = _by_id(summarize(FORM, RESPONSES))["nps"]
    assert nps["numbers"] == {"count": 3, "mean": 7.6667, "min": 5, "max": 10}
    assert nps["distribution"] == [{"value": 5, "count": 1}, {"value": 8, "count": 1},
                                   {"value": 10, "count": 1}]
    assert nps["nps"] == {"promoters": 1, "detractors": 1, "passives": 1, "score": 0.0}


def test_classement_rang_moyen():
    rk = _by_id(summarize(FORM, RESPONSES))["rk"]
    assert rk["ranking"] == [{"label": "X", "mean_rank": 1.3333, "count": 3},
                             {"label": "Y", "mean_rank": 1.6667, "count": 3}]
    assert "choices" not in rk


def test_oui_non_et_texte_jamais_recopie():
    s = summarize(FORM, RESPONSES)
    assert _by_id(s)["yn"]["booleans"] == {"true": 1, "false": 1}
    assert "secret opinion" not in json.dumps(s)


def test_question_retiree_du_formulaire_signalee():
    gone = _by_id(summarize(FORM, RESPONSES))["gone"]
    assert gone["in_form"] is False and gone["ref"] == "old" and gone["answered"] == 1


def test_reponses_par_jour_et_score():
    s = summarize(FORM, RESPONSES)
    assert s["responses_per_day"] == [{"date": "2026-10-01", "count": 1},
                                      {"date": "2026-10-02", "count": 2}]
    assert s["score"] == {"count": 2, "mean": 2.0, "min": 1, "max": 3}


def test_aucune_reponse():
    s = summarize(FORM, [])
    assert s["responses_analyzed"] == 0 and s["responses_per_day"] == []
    assert all(f["answered"] == 0 and f["answer_rate"] == 0.0 for f in s["fields"])


# --- Lecture bornée -------------------------------------------------------


class _Resp:
    def __init__(self, payload):
        self.status_code = 200
        self._payload = payload
        self.content = json.dumps(payload).encode()
        self.text = self.content.decode()

    def json(self):
        return self._payload


def _serve(monkeypatch, pages, total):
    """Le formulaire, puis les pages de réponses dans l'ordre."""
    seen = []
    queue = [FORM] + [{"total_items": total, "page_count": len(pages), "items": p} for p in pages]

    def _request(self, method, url, **kwargs):
        seen.append({"method": method, "url": url, **kwargs})
        return _Resp(queue.pop(0))

    monkeypatch.setattr("requests.Session.request", _request)
    return seen


def _page(prefix, n):
    return [_r(f"{prefix}{i}", "2026-10-01", []) for i in range(n)]


def test_lit_jusqua_la_derniere_page_par_curseur(monkeypatch):
    seen = _serve(monkeypatch, [_page("a", 2), _page("b", 1)], total=3)
    out = TypeformClient(access_token="t").summarize_responses(
        "f1", page_size=2, since="2026-10-01T00:00:00", response_type=["completed"])
    assert [c["url"] for c in seen] == ["https://api.typeform.com/forms/f1"] + \
        ["https://api.typeform.com/forms/f1/responses"] * 2
    assert seen[1]["params"] == {"page_size": 2, "since": "2026-10-01T00:00:00",
                                 "response_type": "completed"}
    assert seen[2]["params"]["before"] == "a1"
    assert (out["responses_analyzed"], out["pages_read"], out["truncated"]) == (3, 2, False)
    assert out["total_items"] == 3 and "note" not in out
    assert out["form_title"] == "Feedback"
    assert out["filters"] == {"since": "2026-10-01T00:00:00", "until": None,
                              "response_type": "completed"}


def test_sarrete_a_total_items_sans_page_vide_de_plus(monkeypatch):
    seen = _serve(monkeypatch, [_page("a", 2)], total=2)
    out = TypeformClient(access_token="t").summarize_responses("f1", page_size=2)
    assert len(seen) == 2 and out["truncated"] is False


def test_max_pages_atteint_le_dit(monkeypatch):
    seen = _serve(monkeypatch, [_page("a", 2), _page("b", 2)], total=10)
    out = TypeformClient(access_token="t").summarize_responses("f1", page_size=2, max_pages=2)
    assert len(seen) == 3
    assert out["truncated"] is True and out["responses_analyzed"] == 4
    assert "max_pages (2)" in out["note"] and "4 of 10" in out["note"]


@pytest.mark.parametrize("kwargs", [{"max_pages": 0}, {"max_pages": 51}, {"max_pages": True},
                                    {"page_size": 0}, {"page_size": 1001}])
def test_bornes_refusees_avant_lappel(monkeypatch, kwargs):
    seen = _serve(monkeypatch, [], total=0)
    with pytest.raises(ValueError):
        TypeformClient(access_token="t").summarize_responses("f1", **kwargs)
    assert seen == []
