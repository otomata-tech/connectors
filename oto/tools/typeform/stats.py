"""Deterministic aggregate of a form's responses: answer rate per question,
distribution of choices, statistics of numeric answers, responses per day.

Pure functions over a form definition and a list of responses: no call, no
model, no free text read. A text answer (short or long text, email, url, file,
date…) is only counted as answered; its content never enters the summary.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List, Optional

#: Field types whose answer is a number read on a scale: the distribution of
#: values is given, and the NPS for `nps`.
SCALE_TYPES = frozenset({"opinion_scale", "rating", "nps"})

#: Field types that never carry an answer.
NO_ANSWER_TYPES = frozenset({"statement", "group"})

#: Label counted for a choice answered with free text ("Other").
OTHER = "(other)"


def _ratio(part: int, whole: int) -> float:
    return round(part / whole, 4) if whole else 0.0


def _flatten(fields: Iterable[Dict[str, Any]], parent: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fields in form order, sub-fields of groups and matrices included."""
    flat = []
    for field in fields or []:
        sub = (field.get("properties") or {}).get("fields")
        if field.get("type") not in NO_ANSWER_TYPES and not sub:
            flat.append({**field, "_parent": parent})
        if sub:
            flat.extend(_flatten(sub, field.get("id")))
    return flat


def _day(response: Dict[str, Any]) -> Optional[str]:
    stamp = response.get("submitted_at") or response.get("landed_at")
    return stamp[:10] if isinstance(stamp, str) and len(stamp) >= 10 else None


def _numbers(values: List[float]) -> Dict[str, Any]:
    return {"count": len(values), "mean": round(sum(values) / len(values), 4),
            "min": min(values), "max": max(values)}


class _FieldTally:
    """What one field's answers add up to."""

    def __init__(self, field: Dict[str, Any], in_form: bool):
        self.field = field
        self.in_form = in_form
        self.answered = 0
        self.labels: Counter = Counter()
        self.ranks: Dict[str, List[int]] = defaultdict(list)
        self.numbers: List[float] = []
        self.booleans: Counter = Counter()

    def add(self, answer: Dict[str, Any]) -> None:
        self.answered += 1
        kind = answer.get("type")
        if kind == "choice":
            choice = answer.get("choice") or {}
            self.labels[choice.get("label") if choice.get("label") is not None else OTHER] += 1
        elif kind == "choices":
            choices = answer.get("choices") or {}
            labels = list(choices.get("labels") or [])
            if self.field.get("type") == "ranking":
                for rank, label in enumerate(labels, start=1):
                    self.ranks[label].append(rank)
            else:
                self.labels.update(labels)
            if choices.get("other") is not None:
                self.labels[OTHER] += 1
        elif kind == "number" and isinstance(answer.get("number"), (int, float)) \
                and not isinstance(answer.get("number"), bool):
            self.numbers.append(answer["number"])
        elif kind == "boolean" and isinstance(answer.get("boolean"), bool):
            self.booleans["true" if answer["boolean"] else "false"] += 1

    def summary(self, analyzed: int) -> Dict[str, Any]:
        field = self.field
        out: Dict[str, Any] = {
            "id": field.get("id"), "ref": field.get("ref"), "title": field.get("title"),
            "type": field.get("type"), "answered": self.answered,
            "answer_rate": _ratio(self.answered, analyzed),
        }
        if field.get("_parent"):
            out["parent_id"] = field["_parent"]
        if not self.in_form:
            out["in_form"] = False
        if self.labels:
            known = [c.get("label") for c in (field.get("properties") or {}).get("choices") or []
                     if c.get("label") is not None]
            order = known + sorted(set(self.labels) - set(known), key=lambda l: (-self.labels[l], l))
            out["choices"] = [{"label": label, "count": self.labels[label],
                               "share": _ratio(self.labels[label], self.answered)}
                              for label in order]
        if self.ranks:
            out["ranking"] = sorted(
                ({"label": label, "mean_rank": round(sum(r) / len(r), 4), "count": len(r)}
                 for label, r in self.ranks.items()),
                key=lambda item: (item["mean_rank"], item["label"]))
        if self.numbers:
            out["numbers"] = _numbers(self.numbers)
            if field.get("type") in SCALE_TYPES:
                values = Counter(self.numbers)
                out["distribution"] = [{"value": v, "count": values[v]} for v in sorted(values)]
            if field.get("type") == "nps":
                promoters = sum(1 for v in self.numbers if v >= 9)
                detractors = sum(1 for v in self.numbers if v <= 6)
                out["nps"] = {"promoters": promoters, "detractors": detractors,
                              "passives": len(self.numbers) - promoters - detractors,
                              "score": round(100 * (promoters - detractors) / len(self.numbers), 1)}
        if self.booleans:
            out["booleans"] = {"true": self.booleans["true"], "false": self.booleans["false"]}
        return out


def summarize(form: Dict[str, Any], responses: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The aggregate of `responses` against the fields of `form` (as `get_form`
    returns it): `{responses_analyzed, responses_per_day, score, fields}`.

    Fields come in form order, then answers to fields no longer in the form
    (`in_form: false`).
    """
    tallies: Dict[str, _FieldTally] = {}
    for field in _flatten(form.get("fields") or []):
        if field.get("id"):
            tallies[field["id"]] = _FieldTally(field, in_form=True)
    per_day: Counter = Counter()
    scores: List[float] = []
    for response in responses:
        day = _day(response)
        if day:
            per_day[day] += 1
        score = (response.get("calculated") or {}).get("score")
        if isinstance(score, (int, float)) and not isinstance(score, bool):
            scores.append(score)
        for answer in response.get("answers") or []:
            ref = answer.get("field") or {}
            field_id = ref.get("id")
            if not field_id:
                continue
            if field_id not in tallies:
                tallies[field_id] = _FieldTally(
                    {"id": field_id, "ref": ref.get("ref"), "type": ref.get("type")},
                    in_form=False)
            tallies[field_id].add(answer)
    analyzed = len(responses)
    out: Dict[str, Any] = {
        "responses_analyzed": analyzed,
        "responses_per_day": [{"date": d, "count": per_day[d]} for d in sorted(per_day)],
    }
    if scores:
        out["score"] = _numbers(scores)
    out["fields"] = [tally.summary(analyzed) for tally in tallies.values()]
    return out
