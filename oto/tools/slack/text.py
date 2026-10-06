"""Preprocessing of outgoing Slack text — pure, no I/O.

Two distinct concerns in the same place (`SlackClient.post_message`): what
Slack wrongly reads in the text (`_escape_false_emoji_shortcodes`), and what
exceeds its recommended length (`_chunk_text`). Sibling modules rather than
buried in `client.py` (already > 500 lines) — see the repo's CLAUDE.md,
convention "heavy parsing moved to a sibling module".
"""
from __future__ import annotations

import re
from typing import List

# Slack reads any `:token:` whose token only contains shortcode characters
# as an emoji — EVEN when it is not a known name. Probed on
# 25/08 (oto-backend#711, signal #575): "20:02 to 20:51: …" renders the second
# ":51:" as `{"type": "emoji", "name": "51"}`, swallowing the two digits and the
# closing `:` — the `:` of "20:51" serves as the OPENING delimiter for the
# punctuation `:` that follows. A purely numeric token is almost never a
# REAL Slack emoji: the two exceptions in the default set (`:100:` 💯,
# `:1234:` 🔢) are allowlisted, everything else is broken by a zero-width
# space — invisible on display, but it stops Slack from pairing
# the two `:`.
_KNOWN_NUMERIC_EMOJI = {"100", "1234"}
_NUMERIC_SHORTCODE_RE = re.compile(r":(\d+):")
_ZERO_WIDTH_SPACE = "\u200b"  # escaped explicitly — never a literal invisible character in source

# Limit RECOMMENDED by Slack for `text` (chat.postMessage docs: "For best
# results, limit … to 4,000 characters"). Beyond it, Slack refuses nothing: it
# TRUNCATES silently at 40,000 characters, with no error or hint for the lost
# part — worse than refusing. `post_message` therefore splits itself before this threshold
# (oto-backend#711, signal #613) rather than let Slack truncate.
MAX_TEXT_LEN = 4000


def escape_false_emoji_shortcodes(text: str) -> str:
    """Stops Slack from reading a number between two `:` (time, punctuation…)
    as an emoji shortcode. Only touches purely numeric tokens outside the
    allowlist — a real named shortcode (`:smile:`, `:+1:`) passes through
    intact, only the numeric false positive is broken."""
    def _break(m: "re.Match[str]") -> str:
        digits = m.group(1)
        if digits in _KNOWN_NUMERIC_EMOJI:
            return m.group(0)
        return ":" + _ZERO_WIDTH_SPACE + digits + ":"
    return _NUMERIC_SHORTCODE_RE.sub(_break, text)


def chunk_text(text: str, limit: int = MAX_TEXT_LEN) -> List[str]:
    """Split `text` into chunks ≤ `limit`, cutting on a newline
    or a space near the boundary rather than mid-word — hard cut
    only if nothing usable is found in the second half
    of the window."""
    if len(text) <= limit:
        return [text]
    chunks: List[str] = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = rest.rfind(" ", 0, limit)
        if cut < limit // 2:
            cut = limit
        chunks.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    if rest:
        chunks.append(rest)
    return chunks
