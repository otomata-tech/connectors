"""GitHub search — repositories, code, issues/PRs, commits, accounts.

This mixin is never instantiated on its own: it is composed into `GitHubClient`, which
provides the transport (`_request`, `_get`, `_check_choice`).

Four things set search apart from the rest of the API, and each one is paid
in cash if ignored:

- ⚠️ **1,000 results maximum, whatever `total_count` announces.** Beyond the
  page that reaches 1,000, GitHub answers **422**. `total_count` is an estimate
  of the corpus, NOT the number of retrievable rows: reading "12,000 results" and
  looping to the end is the classic trap.

- ⚠️ **All responses are OBJECTS** `{total_count, incomplete_results,
  items: [...]}`. `incomplete_results: true` means GitHub **gave up
  the search midway** (timeout): the response is partial and
  says so in no other way.

- ⚠️ **Its own, low usage limit**: 30 requests/minute with a token (10
  without). That is an order of magnitude below the rest of the API — a search
  loop exhausts the quota in a few seconds.

- **CODE search has its own rules**: it only indexes the default
  branch, ignores files larger than 384 KB, requires at least one search
  term (a qualifier alone like `repo:x` is not enough), and — on private
  repositories — requires a token that has access to them.

The qualifier syntax (`repo:`, `org:`, `language:`, `is:`, `state:`…)
belongs to GitHub and is not rewritten here: `q` goes out as-is.
"""
from __future__ import annotations

from typing import Any, Optional

from ..const import (SEARCH_CODE_SORTS, SEARCH_ISSUE_SORTS, SEARCH_MAX_RESULTS,
                     SEARCH_REPO_SORTS, SORT_DIRECTIONS)


class _SearchMixin:
    """Search."""

    @staticmethod
    def _check_query(q: str) -> None:
        """A search without a term is refused HERE.

        GitHub would return a 422 whose message does not say that the problem is
        the absence of `q` — and an empty `q` is almost always a caller bug
        (unsubstituted variable), not an intention.
        """
        if not q or not str(q).strip():
            raise ValueError(
                "`q` required: a GitHub search without a term is refused "
                "(422). Qualifiers alone — `repo:`, `org:`… — are not "
                "enough for code search.")

    def _search(self, path: str, q: str, sort: Optional[str],
                order: Optional[str], per_page: Optional[int],
                page: Optional[int], extra: Optional[dict] = None) -> Any:
        self._check_query(q)
        self._check_choice("order", order, SORT_DIRECTIONS)
        params = {"q": q, "sort": sort, "order": order}
        params.update(extra or {})
        return self._get(path, params, per_page, page)

    def search_repositories(self, q: str, sort: Optional[str] = None,
                            order: Optional[str] = None,
                            per_page: Optional[int] = None,
                            page: Optional[int] = None) -> Any:
        """GET /search/repositories — search repositories.

        `sort`: `stars`, `forks`, `help-wanted-issues`, `updated`. Without `sort`,
        GitHub ranks by relevance.

        ⚠️ Cap of 1,000 results (see module header).
        """
        self._check_choice("sort", sort, SEARCH_REPO_SORTS)
        return self._search("/search/repositories", q, sort, order,
                            per_page, page)

    def search_code(self, q: str, sort: Optional[str] = None,
                    order: Optional[str] = None,
                    per_page: Optional[int] = None,
                    page: Optional[int] = None) -> Any:
        """GET /search/code — search INSIDE the code.

        ⚠️ Three limits specific to this index, which explain most of the
        "why doesn't it find it?" questions:
        only the **default branch** is indexed; files larger than
        **384 KB** are not; and at least one real term is needed, not
        only qualifiers.

        ⚠️ The response does NOT carry the file's content — only its path,
        its repository and snippets. Read the file afterwards with
        `read_text_file`.
        """
        self._check_choice("sort", sort, SEARCH_CODE_SORTS)
        return self._search("/search/code", q, sort, order, per_page, page)

    def search_issues(self, q: str, sort: Optional[str] = None,
                      order: Optional[str] = None,
                      per_page: Optional[int] = None,
                      page: Optional[int] = None) -> Any:
        """GET /search/issues — search issues AND pull requests.

        Both share this index: filter with `is:issue` or `is:pr` in
        `q`. It is, moreover, the simplest way to count a repository's
        issues without being trapped by PRs.
        """
        self._check_choice("sort", sort, SEARCH_ISSUE_SORTS)
        return self._search("/search/issues", q, sort, order, per_page, page)

    def search_users(self, q: str, sort: Optional[str] = None,
                     order: Optional[str] = None,
                     per_page: Optional[int] = None,
                     page: Optional[int] = None) -> Any:
        """GET /search/users — search accounts and organizations.

        `type:user` / `type:org` in `q` to decide between the two.
        """
        return self._search("/search/users", q, sort, order, per_page, page)

    def search_commits(self, q: str, sort: Optional[str] = None,
                       order: Optional[str] = None,
                       per_page: Optional[int] = None,
                       page: Optional[int] = None) -> Any:
        """GET /search/commits — search commits."""
        return self._search("/search/commits", q, sort, order, per_page, page)

    @staticmethod
    def search_is_truncated(payload: Any) -> bool:
        """Is the search response incomplete or truncated?

        True if GitHub gave up midway (`incomplete_results`) **or**
        if the corpus exceeds the 1,000 retrievable results cap. Written here
        so that "I have everything" is never deduced from a misread `total_count` —
        both causes are invisible without this reading.
        """
        if not isinstance(payload, dict):
            return False
        if payload.get("incomplete_results"):
            return True
        total = payload.get("total_count")
        return isinstance(total, int) and total > SEARCH_MAX_RESULTS
