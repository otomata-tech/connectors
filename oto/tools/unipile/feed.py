"""Parsing of the LinkedIn home feed (Voyager passthrough).

Extracted from `client.py` — content unchanged. `parse_feed` and the private helpers
remain re-exported by `client.py` (frozen import path, see the tests
`test_unipile_feed.py` which import `_activity_urn_from` & co. from there).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Optional

logger = logging.getLogger(__name__)


# ---- feed parsing (Voyager normalized graph) ----------------------------
# Voyager returns a NORMALIZED graph: `data.feedDashMainFeedByMainFeed.elements[]`
# (the updates) + `data.included[]` (entities dereferenced by URN, e.g. the
# socialDetail that carries the counters). The mapping is DEFENSIVE by design:
# the Voyager schema is not contractual, so each field is extracted
# best-effort (nested access tolerant of missing keys) and an item that breaks
# the mapping is logged + returned in degraded mode rather than making everything
# fail. If the overall shape is unexpected, we surface the raw payload.


def _unpack_cursor(cursor: Optional[str]) -> tuple[int, Optional[str]]:
    """Opaque cursor `"<start>|<paginationToken>"` → (start, token). Tolerant:
    cursor None/empty → (0, None); without `|` → treated as a bare token (start 0)."""
    if not cursor:
        return 0, None
    if "|" in cursor:
        start_s, token = cursor.split("|", 1)
        try:
            start = int(start_s)
        except (TypeError, ValueError):
            start = 0
        return start, (token or None)
    return 0, cursor


def _deep_get(obj: Any, *keys: str, default: Any = None) -> Any:
    """Tolerant nested access: returns `default` as soon as a link is missing or
    is not a dict (never a KeyError/TypeError on a partial Voyager graph)."""
    cur = obj
    for k in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(k)
        if cur is None:
            return default
    return cur


def _text_of(node: Any) -> Optional[str]:
    """Voyager often wraps text in `{text: "..."}` (sometimes nested).
    Accepts a bare string, `{text: str}` or `{text: {text: str}}`."""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        t = node.get("text")
        if isinstance(t, str):
            return t
        if isinstance(t, dict) and isinstance(t.get("text"), str):
            return t["text"]
    return None


def _activity_urn_from(el: dict) -> Optional[str]:
    """Extract `urn:li:activity:<id>` from a Voyager update.

    Leads (in order): updateMetadata.urn / updateMetadata.shareUrn /
    the update's `entityUrn` (`urn:li:fsd_update:(urn:li:activity:...,...)`)."""
    for path in (("updateMetadata", "urn"), ("updateMetadata", "shareUrn")):
        v = _deep_get(el, *path)
        if isinstance(v, str) and "urn:li:activity:" in v:
            return _extract_activity(v)
    eu = el.get("entityUrn")
    if isinstance(eu, str):
        return _extract_activity(eu)
    return None


def _extract_activity(s: str) -> Optional[str]:
    """Isolate `urn:li:activity:<id>` from a string (composite or bare URN)."""
    marker = "urn:li:activity:"
    idx = s.find(marker)
    if idx < 0:
        return None
    rest = s[idx + len(marker):]
    digits = ""
    for ch in rest:
        if ch.isdigit():
            digits += ch
        else:
            break
    return f"{marker}{digits}" if digits else None


def _posted_at_from_activity(activity_urn: Optional[str]) -> Optional[str]:
    """Decode the timestamp encoded in the LinkedIn activity id: the 41 most
    significant bits of the 64-bit id = a timestamp in ms (`id >> 22`). Robust trick,
    independent of the relative label ('2h') displayed by Voyager."""
    if not activity_urn:
        return None
    try:
        aid = int(activity_urn.rsplit(":", 1)[-1])
    except (TypeError, ValueError):
        return None
    ms = aid >> 22
    # guard: a plausible epoch ms (> 2001-09, < 2100)
    if not (1_000_000_000_000 < ms < 4_102_444_800_000):
        return None
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _social_counts(el: dict, included_by_urn: dict) -> tuple[Optional[int], Optional[int]]:
    """(reactions_count, comments_count) from the socialDetail — inlined or
    dereferenced via `*socialDetail` in `included`. Best-effort."""
    sd = el.get("socialDetail")
    if sd is None:
        ref = el.get("*socialDetail")
        if isinstance(ref, str):
            sd = included_by_urn.get(ref)
    counts = _deep_get(sd, "totalSocialActivityCounts", default={}) or {}
    comments = counts.get("numComments")
    reactions = None
    rtc = counts.get("reactionTypeCounts")
    if isinstance(rtc, list) and rtc:
        try:
            reactions = sum(int(r.get("count", 0)) for r in rtc if isinstance(r, dict))
        except (TypeError, ValueError):
            reactions = None
    if reactions is None:
        reactions = counts.get("numLikes")
    return reactions, comments


def _annotated_entity(node: Any) -> Optional[str]:
    """Name of the FIRST annotated entity of a Voyager text. Voyager delivers its
    labels as annotated text — `{text: "Jean Dupont commented on this", attributes:
    [{start, length, …}]}` — where the 1st annotation covers the actor. We slice
    it out rather than guess with a regular expression (independent of the
    interface language). None if the shape is not that one."""
    if not isinstance(node, dict):
        return None
    text = node.get("text")
    if isinstance(text, dict):  # `{text: {text, attributes}}`
        return _annotated_entity(text)
    attrs = node.get("attributes")
    if not isinstance(text, str) or not isinstance(attrs, list) or not attrs:
        return None
    first = attrs[0]
    if not isinstance(first, dict):
        return None
    start, length = first.get("start"), first.get("length")
    if not isinstance(start, int) or not isinstance(length, int) or length <= 0:
        return None
    name = text[start:start + length].strip()
    return name or None


def _feed_context(el: dict) -> tuple[Optional[str], Optional[str]]:
    """(feed_reason, surfaced_by) — WHY this post shows up in MY feed.

    A stranger's post almost always appears through a connection's REBOUND: "X
    commented on this", "X reacted", reshare. This reason is the core of
    rebound social selling (who in my network interacts with whom) and it was lost
    in the mapping (feedback #280): `feed_reason` = the Voyager label verbatim,
    `surfaced_by` = the name of the connection that caused it to surface.

    Best-effort: `header` (usual location of the rebound label) then
    `socialContext`. Neither ⇒ (None, None) = post surfaced directly."""
    for node in (el.get("header"), el.get("socialContext")):
        reason = _text_of(node)
        if reason:
            return reason, _annotated_entity(node)
    return None, None


def _comment_authors(el: dict, included_by_urn: dict,
                     activity_urn: Optional[str]) -> list[str]:
    """Authors of the comments visible on this update, in order of appearance.

    The feed doesn't carry the full comments, but Voyager attaches the
    HIGHLIGHTED comments (those that make the post surface): without the whole
    thread, keeping WHO commented is enough to answer "who in my network interacts
    with whom" (feedback #280). Two leads: the `socialDetail` (inline or
    dereferenced) then the `comment` objects of `included` attached to this activity
    (their `entityUrn` carries the activity id). Best-effort, deduplicated."""
    names: list[str] = []

    def _add(commenter: Any) -> None:
        if isinstance(commenter, str):  # `*commenter` reference → included
            commenter = included_by_urn.get(commenter)
        name = (_text_of(_deep_get(commenter, "name"))
                or _text_of(_deep_get(commenter, "title"))
                or _text_of(commenter))
        if name and name not in names:
            names.append(name)

    sd = el.get("socialDetail")
    if sd is None:
        ref = el.get("*socialDetail")
        if isinstance(ref, str):
            sd = included_by_urn.get(ref)
    for c in _deep_get(sd, "comments", "elements", default=[]) or []:
        if isinstance(c, dict):
            _add(c.get("commenter") or c.get("*commenter"))

    if activity_urn:
        for urn, obj in included_by_urn.items():
            if "comment" in urn.lower() and activity_urn in urn and isinstance(obj, dict):
                _add(obj.get("commenter") or obj.get("*commenter"))
    return names


# --- WHAT a post is made of (the update's `content` block) -------------------
# Voyager stores a post's media in `content`, under a key that NAMES the component
# type (`imageComponent`, `pollComponent`, `carouselContent`… — 42 names seen
# on a real feed). This block was entirely dropped by the mapping: a post with 2,775
# reactions whose text boils down to "🧐" (the whole point is in the image) became
# UNCLASSIFIABLE for an agent — the most engaging post of a page, invisible.
# The raw block weighs ~4,700 characters (images in 4 resolutions + tracking): we only
# keep the normalized TYPE + the meaningful label when there is one, ~100
# characters. The type is DERIVED from the key name (`Component`/`Content` suffix
# removed, camelCase → snake_case), not from an exhaustive table to maintain: a
# never-seen component returns its own normalized name rather than a silent "unknown".
# The table below therefore ONLY carries the synonyms to fold.
_CONTENT_ALIASES = {
    "linked_in_video": "video",     # native LinkedIn video
    "external_video": "video",      # embedded YouTube & co.
    "native_video": "video",
    "slideshow": "carousel",        # image slideshow = a carousel
}
# DOMINANCE order when an update carries several components: the first of this
# list wins. Ranked by decreasing triage power — what calls for a
# specific action (poll, document, article) before mere decoration (image). An
# unknown type comes after all the known ones (it informs, but we don't know yet how much).
_CONTENT_PRIORITY = ("poll", "document", "article", "newsletter", "event", "job",
                     "celebration", "video", "carousel", "image", "entity")
# Label keys probed on the dominant component (article title, poll question,
# document title, image alt text…).
_CONTENT_LABEL_KEYS = ("title", "question", "headline", "name",
                       "altText", "accessibilityText")
_CONTENT_LABEL_MAX = 140   # bounds the worst case (≈ the limit of a poll question)
_CAMEL_SPLIT = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def _content_key_to_type(key: str) -> Optional[str]:
    """`imageComponent` → `image`, `linkedInVideoComponent` → `video`,
    `carouselContent` → `carousel`. None if the key is not a content component
    (`resharedUpdate`, `$type`… stay out of the count)."""
    for suffix in ("Component", "Content"):
        if key.endswith(suffix) and len(key) > len(suffix):
            base = _CAMEL_SPLIT.sub("_", key[: -len(suffix)]).lower()
            return _CONTENT_ALIASES.get(base, base)
    return None


def _content_label(node: Any) -> Optional[str]:
    """Meaningful label of a component, if available AT NO COST (already
    in the payload): article title, poll question, document title,
    image alt text. Probed on the component, then ONE level down — its
    sub-objects (`document.title`) and the 1st element of its lists
    (`images[0].accessibilityText`), where Voyager stores these labels.
    Truncated to `_CONTENT_LABEL_MAX`. None if the component carries none."""
    if not isinstance(node, dict):
        return None
    candidates = [node]
    for value in node.values():
        if isinstance(value, dict):
            candidates.append(value)
        elif isinstance(value, list) and value and isinstance(value[0], dict):
            candidates.append(value[0])
    for obj in candidates:
        for key in _CONTENT_LABEL_KEYS:
            label = _text_of(obj.get(key))
            if isinstance(label, str) and label.strip():
                return label.strip()[:_CONTENT_LABEL_MAX]
    return None


def _content_facets(content: Any) -> tuple[str, Optional[str]]:
    """(content_type, content_title) of a Voyager `content` block.

    No block / no recognizable component → `("text", None)`: the post carries
    only its text, this is not a mapping failure (a truly unexpected schema, on the other hand,
    makes `_map_feed_item` raise and the item is logged then ignored).
    Several components → the DOMINANT one (`_CONTENT_PRIORITY`, then order of appearance)
    gives the type AND the label: a scalar field stays filterable by equality downstream
    (datastore mirror), whereas a list or an "image+article" is not."""
    if not isinstance(content, dict):
        return "text", None
    found: list[tuple[int, int, str, Any]] = []
    for i, (key, node) in enumerate(content.items()):
        # ⚠️ Voyager declares ALL the keys of its GraphQL schema, almost all of them
        # `null`: a key's PRESENCE means nothing, only its VALUE counts. Without this
        # test, `dynamicPollComponent: null` made a poll out of any post —
        # and since `poll` tops the dominance, 48 posts out of 60 came out as `poll`
        # on the first real run (12/08). Wrong data written at every sync is worse
        # than no data: the agent sorts on it with no way to doubt it.
        if node is None or node == {} or node == [] or node == "":
            continue
        ctype = _content_key_to_type(key)
        if not ctype:
            continue
        rank = (_CONTENT_PRIORITY.index(ctype) if ctype in _CONTENT_PRIORITY
                else len(_CONTENT_PRIORITY))
        found.append((rank, i, ctype, node))
    if not found:
        return "text", None
    found.sort(key=lambda f: (f[0], f[1]))
    _, _, ctype, node = found[0]
    if ctype not in _CONTENT_PRIORITY:
        # Never-seen component: we return its name as Voyager names it (normalized)
        # rather than a silent "unknown" — traceable when LinkedIn adds one.
        logger.debug("unipile feed: unknown content component (%s)", ctype)
    return ctype, _content_label(node)


def _map_feed_item(el: dict, included_by_urn: dict) -> dict:
    """A Voyager update → normalized item. Raises if `el` is not a usable
    update (neither actor nor commentary) — the caller handles the fallback."""
    actor = el.get("actor") if isinstance(el.get("actor"), dict) else {}
    commentary = el.get("commentary") if isinstance(el.get("commentary"), dict) else {}
    if not actor and not commentary:
        raise ValueError("element without actor/commentary (not a feed update)")

    activity_urn = _activity_urn_from(el)
    reactions, comments = _social_counts(el, included_by_urn)
    # WHY this post shows up in MY feed ("Someone commented on this", "Someone
    # reacted"): it's what the user remembers most often — they remember
    # WHO made the post surface, not its author. Without this field, a post found
    # "by rebound" can't be found in the mirror (signal #280: searching for a post
    # seen via a connection's comment → 0 results out of 710 mirror posts).
    # `_feed_context` reads `header` THEN `socialContext` (fallback) and also returns the NAME of
    # the connection that caused it to surface — reading `header` alone lost
    # both.
    feed_reason, surfaced_by = _feed_context(el)
    post_url = (
        f"https://www.linkedin.com/feed/update/{activity_urn}"
        if activity_urn else None
    )
    # REPOST: `author_name` is then the resharer and `text` their share
    # comment — the ORIGINAL author, the one we're looking for, was entirely lost.
    reshared = el.get("resharedUpdate") if isinstance(el.get("resharedUpdate"), dict) else {}
    if not reshared:
        reshared = _deep_get(el, "content", "resharedUpdate", default={}) or {}
    reshared_actor = reshared.get("actor") if isinstance(reshared.get("actor"), dict) else {}
    # …and its COMMENTARY too: on a repost, `text` carries the resharer's note —
    # often empty or "👏" — while the real content, the one the triage rule
    # wants to judge, stayed unreachable. Same type treatment as the carrier post.
    reshared_commentary = (reshared.get("commentary")
                           if isinstance(reshared.get("commentary"), dict) else {})
    content_type, content_title = _content_facets(el.get("content"))
    return {
        "urn": activity_urn or el.get("entityUrn"),
        "author_name": _text_of(actor.get("name")),
        "author_headline": _text_of(actor.get("description")),
        "text": _text_of(commentary.get("text")) or _text_of(commentary),
        "posted_at": _posted_at_from_activity(activity_urn),
        "posted_relative": _text_of(actor.get("subDescription")),
        "reactions_count": reactions,
        "comments_count": comments,
        # Why this post surfaces + who made it surface + who commented
        # (feedback #280: the rebound through a connection was lost in the mapping).
        "feed_reason": feed_reason,
        "surfaced_by": surfaced_by,
        "comment_authors": _comment_authors(el, included_by_urn, activity_urn),
        # WHAT the post is made of: without this, a post whose whole point is in
        # the image (text = "🧐", 2,775 reactions) is unclassifiable — the normalized type
        # + the free label (article title, poll question) make it sortable
        # without pulling in the `content` block (~4,700 characters, 93% tracking and
        # thumbnails). `content_type` is always something (`text` = bare post).
        "content_type": content_type,
        "content_title": content_title,
        "post_url": post_url,
        "is_repost": bool(reshared),
        "original_author_name": _text_of(reshared_actor.get("name")) or None,
        # On a repost, the substance is in the ORIGINAL: its text and the nature of
        # its content. None outside reposts (the field stays present: the downstream mirror
        # projects fixed columns).
        "original_text": (_text_of(reshared_commentary.get("text"))
                          or _text_of(reshared_commentary) or None) if reshared else None,
        "original_content_type": (_content_facets(reshared.get("content"))[0]
                                  if reshared else None),
    }


def _is_promo(el: dict) -> bool:
    """True if the update is a sponsored/promotional insert (LinkedIn ad,
    "Hiring Pro", Promoted posts…) rather than an organic post — to be excluded from the
    feed. Several Voyager markers, best-effort: `inAppPromotion` urn, a
    `promoComponent` in the content, `actionsPosition=PROMO_COMPONENT`, or a
    `sponsoredTracking` block in the tracking metadata."""
    eu = el.get("entityUrn")
    if isinstance(eu, str) and "inAppPromotion" in eu:
        return True
    if _deep_get(el, "content", "promoComponent") is not None:
        return True
    if _deep_get(el, "metadata", "actionsPosition") == "PROMO_COMPONENT":
        return True
    if _deep_get(el, "metadata", "trackingData", "sponsoredTracking") is not None:
        return True
    return False


def parse_feed(resp: Any, count: int = 20, start: int = 0) -> dict:
    """Map the Unipile raw data feed envelope → `{items, cursor, count}`.

    Returns ONLY normalized organic posts: sponsored/promo inserts
    (`_is_promo`) are silently dropped, and an update with an unexpected schema is
    **logged (warning) then ignored** (never a verbose `_raw` in the output).
    If the overall structure is unexpected (no `elements`), we surface
    `{items: [], cursor: None, count: 0, _raw: resp}` + an error log.
    """
    # Unipile envelope {object, data} → Voyager JSON {data, included}.
    voyager = resp.get("data") if isinstance(resp, dict) else None
    feed = _deep_get(voyager, "data", "feedDashMainFeedByMainFeed")
    elements = feed.get("elements") if isinstance(feed, dict) else None
    if not isinstance(elements, list):
        logger.error(
            "unipile feed: unexpected structure (no elements) — raw payload surfaced"
        )
        return {"items": [], "cursor": None, "count": 0, "_raw": resp}

    included = _deep_get(voyager, "included", default=[])
    included_by_urn = {
        it["entityUrn"]: it
        for it in included
        if isinstance(it, dict) and isinstance(it.get("entityUrn"), str)
    }

    items: list[dict] = []
    for el in elements:
        if not isinstance(el, dict) or _is_promo(el):
            continue  # non-dict or sponsored/promo insert → never returned
        try:
            items.append(_map_feed_item(el, included_by_urn))
        except Exception:  # noqa: BLE001 — defensive parsing intended
            logger.warning(
                "unipile feed: mapping of an item failed, ignored", exc_info=True
            )
            continue

    items = items[:count]
    token = _deep_get(feed, "metadata", "paginationToken")
    next_cursor = f"{start + len(items)}|{token}" if token else None
    return {"items": items, "cursor": next_cursor, "count": len(items)}
