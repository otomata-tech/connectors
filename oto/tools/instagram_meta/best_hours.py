"""Best posting hours and days — a LOCAL computation, on data already read.

Meta doesn't expose this answer: we derive it from the average engagement (likes +
comments) of the posts already fetched. It is therefore a **heuristic**,
and it is named as such even in what it returns: `sample_size` travels
with the result, because a ranking over four posts and a ranking over
forty read the same way and are not worth the same.

No network call here — the function takes the list of media the caller has
read, which makes it testable without mocking anything and avoids a second round trip
when the caller already has the posts at hand.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

#: Monday = 0, like `datetime.weekday()`.
JOURS = ("lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche")


def compute_best_hours(media: list[dict]) -> dict:
    """`{by_hour, by_weekday, sample_size}`, sorted by decreasing average engagement.

    Hours are those of the timestamps returned by Meta (UTC): converting them
    would require knowing which timezone the account posts in, which the API doesn't
    say — and an assumed conversion would shift the ranking by an hour or two
    with nothing signaling it.

    A post without a timestamp is IGNORED and doesn't count in the averages,
    but stays in `sample_size`: it is the number of posts examined, not
    the number retained — a gap between the two is then visible by comparing the cumulated
    `posts`, instead of being erased."""
    if not media:
        return {"by_hour": [], "by_weekday": [], "sample_size": 0}

    par_heure: dict[int, dict[str, int]] = defaultdict(lambda: {"total": 0, "count": 0})
    par_jour: dict[int, dict[str, int]] = defaultdict(lambda: {"total": 0, "count": 0})

    for m in media:
        horodatage = m.get("timestamp")
        if not horodatage:
            continue
        try:
            dt = datetime.fromisoformat(str(horodatage).replace("Z", "+00:00"))
        except ValueError:
            continue
        engagement = (m.get("like_count") or 0) + (m.get("comments_count") or 0)
        par_heure[dt.hour]["total"] += engagement
        par_heure[dt.hour]["count"] += 1
        par_jour[dt.weekday()]["total"] += engagement
        par_jour[dt.weekday()]["count"] += 1

    return {
        "by_hour": _classe(par_heure, "hour", lambda h: h),
        "by_weekday": _classe(par_jour, "weekday", lambda wd: JOURS[wd]),
        "sample_size": len(media),
    }


def _classe(compteurs: dict, cle: str, libelle) -> list[dict[str, Any]]:
    return sorted(
        ({cle: libelle(k), "avg_engagement": round(v["total"] / v["count"]),
          "posts": v["count"]}
         for k, v in compteurs.items()),
        key=lambda x: -x["avg_engagement"])
