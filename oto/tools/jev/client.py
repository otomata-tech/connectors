"""Jev (TypeSafe): a DECISION model, not a text model.

Send a **state** and **typed questions**; get typed answers with probabilities. No text,
no reasoning, no tool calls: it replaces "ask a model and parse its reply", not the model
running the work.

Three primitives:
- **noul**: does the condition hold? → `{"noul": 0.96}` (probability of yes);
- **choice**: which option? → chosen option, probability per option, confidence;
- **score**: where on an ordered scale? → weighted position, probability per level, legend.

Billing: input only, output is free. The state is billed ONCE per request and each extra
question costs ~48 tokens, so send the whole rubric in one call. Every response carries
`usage.cost`, the REAL cost in USD: bill on that, never on a copied price list.

Served through OpenRouter (`/v1/systemone`) today; `base_url` and `path` can be overridden
to call TypeSafe directly (same request and response schema).

Requires: requests
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import requests

from ..common.credentials import require
from ..common import raise_for_upstream

#: Known primitives. Any other type is refused before the call, so a typo is never billed.
TYPES = ("noul", "choice", "score")

#: The DATED snapshot, not the bare id: `typesafe/jev-1.13` moves, and a calibrated
#: threshold must not drift with it.
DEFAULT_MODEL = "typesafe/jev-1.13-20260917"

#: The only model publishers this client serves: the key never reaches a text model.
SERVED_PUBLISHERS = ("typesafe/", "~typesafe/")


class JevClient:
    """Jev client (System One / Decisions), Bearer auth with an OpenRouter `sk-or-…` key."""

    BASE_URL = "https://openrouter.ai/api"
    PATH = "/v1/systemone"

    def __init__(self, api_key: str = None, base_url: str = None, path: str = None):
        """
        Args:
            api_key: OpenRouter key.
            base_url: API root (default OpenRouter; the only setting to change to call
                TypeSafe directly).
            path: decision request path under that root.
        """
        self.api_key = require(api_key, "OPENROUTER_API_KEY")
        self.base_url = (base_url or self.BASE_URL).rstrip("/")
        self.path = path or self.PATH
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        })

    # --- transport ----------------------------------------------------------

    def _post(self, body: Dict[str, Any], *, timeout: int = 60) -> Dict[str, Any]:
        # (connect, read): an unreachable host must not eat the whole read budget.
        resp = self.session.post(f"{self.base_url}{self.path}", json=body,
                                 timeout=(10, timeout))
        raise_for_upstream(resp, service="jev")
        return resp.json() if resp.content else {}

    # --- the only surface ---------------------------------------------------

    def decide(self, state: Dict[str, Any], questions: Dict[str, Any],
               model: Optional[str] = None, *, timeout: int = 60) -> Dict[str, Any]:
        """ONE decision: one state, N questions asked together.

        ⚠️ Questions in one request are answered in parallel and cannot see each other's
        answers. A dependent question needs a second call.

        Args:
            state: the context to judge (flat or nested object, ≤ 32,000 tokens with
                the questions).
            questions: `{name: {"type": "noul|choice|score", "instructions": str,
                "criteria": …}}`. `criteria` is a dict for `noul` (keys `true` /
                `false`) and `choice` (one key per option), an ordered LIST for `score`.
            model: another model id (default: the dated snapshot).
            timeout: read budget, in seconds.

        Returns:
            The response body: `{id, model, provider, answers, usage}`. `usage` carries
            `input_tokens`, `output_tokens` and `cost` (real USD). `model` names the
            snapshot that actually answered: keep it next to the answer.

        Raises:
            ValueError: question with an unknown type, or without `criteria`.
            UpstreamHTTPError: upstream refusal (400 invalid rubric or state over
                32,000 tokens, 401 key, 402 no credits, 429, 5xx).
        """
        name = model or DEFAULT_MODEL
        self.check_model(name)
        self.check_questions(questions)
        return self._post({"model": name, "state": state, "questions": questions},
                          timeout=timeout)

    # --- guards -------------------------------------------------------------

    @staticmethod
    def check_model(model: str) -> None:
        """Refuse any model that is not a TypeSafe decision model (checked by publisher,
        not by a model list that would go stale)."""
        if not str(model).startswith(SERVED_PUBLISHERS):
            raise ValueError(
                f"model {model!r}: this connector only serves TypeSafe decision models "
                f"({' or '.join(SERVED_PUBLISHERS)}…). A text model cannot answer a "
                "typed question.")

    @staticmethod
    def check_questions(questions: Dict[str, Any]) -> None:
        """Refuse a malformed rubric BEFORE the call, naming the faulty question.

        ⚠️ `criteria` is required here: upstream answers without it, with a probability
        that means nothing.
        """
        if not isinstance(questions, dict) or not questions:
            raise ValueError("`questions`: at least one question is required")
        for name, q in questions.items():
            if not isinstance(q, dict):
                raise ValueError(f"question {name!r}: an object is expected")
            t = q.get("type")
            if t not in TYPES:
                raise ValueError(
                    f"question {name!r}: unknown type {t!r}, expected "
                    f"{' | '.join(TYPES)}")
            if not q.get("instructions"):
                raise ValueError(f"question {name!r}: `instructions` is empty")
            crit = q.get("criteria")
            if t == "score":
                if not isinstance(crit, list) or len(crit) < 2:
                    raise ValueError(
                        f"question {name!r} (score): `criteria` must be an ordered LIST "
                        "of at least two levels")
            elif not isinstance(crit, dict) or len(crit) < 2:
                raise ValueError(
                    f"question {name!r} ({t}): `criteria` needs at least two entries "
                    + ("(`true` and `false`)" if t == "noul" else "(one per option)"))
