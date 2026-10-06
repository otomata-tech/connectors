"""GitHub pull requests — proposals, reviews, merging.

This mixin is never instantiated on its own: it is composed into `GitHubClient`, which
provides the transport (`_request`, `_get`, `_check_choice`).

⚠️ **A PR is also an issue**: its THREAD comments, labels,
milestones and assignments go through the methods of `issues.py`, with the PR's
number. This module only carries what is specific to it — the diff, reviews,
review comments (line by line), requested reviewers, and merging.

⚠️ **`merge_pull` modifies the target branch, and cannot be undone with one click.**
The three methods do not have the same effect on history: `merge` adds a
merge commit, `squash` squashes the branch into a single commit, `rebase` rewrites
the commits. The choice belongs to the caller, and the connector does not guess it.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ...common import raise_for_upstream
from ..const import (MERGE_METHODS, PULL_SORTS, PULL_STATES, REVIEW_EVENTS,
                     SORT_DIRECTIONS)


class _PullsMixin:
    """Pull requests, reviews, merging."""

    # --- pull requests --------------------------------------------------------

    def list_pulls(self, owner: str, repo: str, state: Optional[str] = None,
                   head: Optional[str] = None, base: Optional[str] = None,
                   sort: Optional[str] = None, direction: Optional[str] = None,
                   per_page: Optional[int] = None,
                   page: Optional[int] = None) -> Any:
        """GET /repos/{owner}/{repo}/pulls — the repository's pull requests.

        `state` is `open` (GitHub default), `closed` or `all`. `head` filters by
        source branch (`user:branch`), `base` by target branch.

        ⚠️ A **merged** PR is `closed`: there is no `merged` state.
        To tell them apart, read `merged_at` (null = closed without merging).
        """
        self._check_choice("state", state, PULL_STATES)
        self._check_choice("sort", sort, PULL_SORTS)
        self._check_choice("direction", direction, SORT_DIRECTIONS)
        return self._get(f"/repos/{owner}/{repo}/pulls", {
            "state": state, "head": head, "base": base,
            "sort": sort, "direction": direction}, per_page, page)

    def get_pull(self, owner: str, repo: str, number: Any) -> Any:
        """GET /repos/{owner}/{repo}/pulls/{number} — one PR, with its counters.

        This form (unlike the list) carries `mergeable`, `merged`,
        `additions`, `deletions`, `changed_files`.

        ⚠️ **`mergeable` may be `null`**: GitHub computes mergeability
        in the background on the first call. `null` means "not known yet" —
        ask again, and above all do not read it as "not mergeable".
        """
        return self._request("GET", f"/repos/{owner}/{repo}/pulls/{number}")

    def create_pull(self, owner: str, repo: str,
                    payload: Dict[str, Any]) -> Any:
        """POST /repos/{owner}/{repo}/pulls — open a pull request.

        Required: `title` (or `issue`), `head`, `base`. Optional: `body`,
        `draft`, `maintainer_can_modify`.

        `head` is the source branch (`user:branch` from a fork),
        `base` the target branch. `draft=True` opens a draft, which does not request
        review until it is marked ready.

        ⚠️ **Notifies** code owners (CODEOWNERS) and repository
        watchers, except as a draft.
        """
        for champ in ("head", "base"):
            if not payload.get(champ):
                raise ValueError(f"`{champ}` required to open a PR.")
        if not payload.get("title") and not payload.get("issue"):
            raise ValueError(
                "`title` required (or `issue`, to convert an existing "
                "issue into a PR).")
        return self._request("POST", f"/repos/{owner}/{repo}/pulls",
                             json=dict(payload))

    def update_pull(self, owner: str, repo: str, number: Any,
                    payload: Dict[str, Any]) -> Any:
        """PATCH /repos/{owner}/{repo}/pulls/{number} — update a PR.

        Fields: `title`, `body`, `state` (`open`/`closed`), `base`,
        `maintainer_can_modify`.

        ⚠️ You do not MERGE through here: `state="closed"` closes without merging.
        Merging is `merge_pull`.
        """
        return self._request("PATCH", f"/repos/{owner}/{repo}/pulls/{number}",
                             json=dict(payload))

    def list_pull_files(self, owner: str, repo: str, number: Any,
                        per_page: Optional[int] = None,
                        page: Optional[int] = None) -> Any:
        """GET /repos/{owner}/{repo}/pulls/{number}/files — modified files + patch.

        ⚠️ **Capped at 3,000 files**, and each file's `patch` is
        omitted beyond a certain size. A massive PR is therefore returned
        incomplete, without error.
        """
        return self._get(f"/repos/{owner}/{repo}/pulls/{number}/files", None,
                         per_page, page)

    def list_pull_commits(self, owner: str, repo: str, number: Any,
                          per_page: Optional[int] = None,
                          page: Optional[int] = None) -> Any:
        """GET /repos/{owner}/{repo}/pulls/{number}/commits — the PR's commits.

        ⚠️ Capped at 250 commits; beyond that, go through the repository's commits API.
        """
        return self._get(f"/repos/{owner}/{repo}/pulls/{number}/commits", None,
                         per_page, page)

    def check_pull_merged(self, owner: str, repo: str, number: Any) -> bool:
        """GET /repos/{owner}/{repo}/pulls/{number}/merge — has the PR been merged?

        ⚠️ Bodyless endpoint: GitHub answers **204 if merged, 404 otherwise**.
        The 404 here is an ANSWER, not an error — hence this boolean, rather than
        letting an `UpstreamHTTPError` bubble up to say "no".
        """
        resp = self._request("GET", f"/repos/{owner}/{repo}/pulls/{number}/merge",
                             raw=True)
        if resp.status_code == 204:
            return True
        if resp.status_code == 404:
            return False
        raise_for_upstream(resp, service="github")
        return False

    def merge_pull(self, owner: str, repo: str, number: Any,
                   commit_title: Optional[str] = None,
                   commit_message: Optional[str] = None,
                   sha: Optional[str] = None,
                   merge_method: Optional[str] = None) -> Any:
        """PUT /repos/{owner}/{repo}/pulls/{number}/merge — **MERGES the PR**.

        ⚠️ Write to the target branch, cannot be undone with one click. The three
        methods do not do the same thing to history:
        `merge` adds a merge commit, `squash` squashes the branch into a single
        commit, `rebase` rewrites the commits onto the target.

        `sha` is a **race protection**: if the PR's head has moved
        since it was read, GitHub refuses (409) instead of merging something other
        than what was reviewed. Pass it when the merge decision
        rests on an already-read diff.

        Common refusals: **405** (not mergeable — conflicts, failing
        checks), **409** (the head moved, or stale `sha`).
        """
        self._check_choice("merge_method", merge_method, MERGE_METHODS)
        body: Dict[str, Any] = {}
        for key, value in (("commit_title", commit_title),
                           ("commit_message", commit_message),
                           ("sha", sha), ("merge_method", merge_method)):
            if value is not None:
                body[key] = value
        return self._request("PUT",
                             f"/repos/{owner}/{repo}/pulls/{number}/merge",
                             json=body or None)

    def update_pull_branch(self, owner: str, repo: str, number: Any,
                           expected_head_sha: Optional[str] = None) -> Any:
        """PUT /repos/{owner}/{repo}/pulls/{number}/update-branch — rebase the target into it.

        Updates the PR's branch with the latest commits of its base. Returns
        **202** (accepted, processed in the background): the work is not finished
        when the response arrives.
        """
        body = ({"expected_head_sha": expected_head_sha}
                if expected_head_sha else None)
        return self._request(
            "PUT", f"/repos/{owner}/{repo}/pulls/{number}/update-branch",
            json=body)

    # --- reviews --------------------------------------------------------------

    def list_reviews(self, owner: str, repo: str, number: Any,
                     per_page: Optional[int] = None,
                     page: Optional[int] = None) -> Any:
        """GET /repos/{owner}/{repo}/pulls/{number}/reviews — submitted reviews."""
        return self._get(f"/repos/{owner}/{repo}/pulls/{number}/reviews", None,
                         per_page, page)

    def create_review(self, owner: str, repo: str, number: Any,
                      payload: Dict[str, Any]) -> Any:
        """POST /repos/{owner}/{repo}/pulls/{number}/reviews — submit a review.

        Fields: `body`, `event` (`APPROVE` | `REQUEST_CHANGES` | `COMMENT`),
        `commit_id`, `comments` (line-by-line comments).

        ⚠️ **A missing `event` leaves the review PENDING** (`PENDING`): nothing is
        published, nobody is notified, and it stays visible to its author alone.
        This is useful for preparing, and a trap when you thought you were approving.
        ⚠️ `APPROVE` can unblock a protected merge: it is an act of
        governance, not a comment.
        """
        self._check_choice("event", payload.get("event"), REVIEW_EVENTS)
        return self._request(
            "POST", f"/repos/{owner}/{repo}/pulls/{number}/reviews",
            json=dict(payload))

    def submit_review(self, owner: str, repo: str, number: Any,
                      review_id: Any, event: str,
                      body: Optional[str] = None) -> Any:
        """POST /…/pulls/{number}/reviews/{id}/events — publish a pending review.

        This is the action that brings a `PENDING` review out of the shadows. `event` is required.
        """
        self._check_choice("event", event, REVIEW_EVENTS)
        payload: Dict[str, Any] = {"event": event}
        if body is not None:
            payload["body"] = body
        return self._request(
            "POST",
            f"/repos/{owner}/{repo}/pulls/{number}/reviews/{review_id}/events",
            json=payload)

    def list_review_comments(self, owner: str, repo: str, number: Any,
                             since: Optional[str] = None,
                             per_page: Optional[int] = None,
                             page: Optional[int] = None) -> Any:
        """GET /repos/{owner}/{repo}/pulls/{number}/comments — LINE-BY-LINE comments.

        Distinct from thread comments, which are the issue's
        (`list_issue_comments`).
        """
        return self._get(f"/repos/{owner}/{repo}/pulls/{number}/comments",
                         {"since": since}, per_page, page)

    def create_review_comment(self, owner: str, repo: str, number: Any,
                              payload: Dict[str, Any]) -> Any:
        """POST /repos/{owner}/{repo}/pulls/{number}/comments — comment on a LINE.

        Required: `body`, `commit_id`, `path`, and the position — `line` (+ `side`),
        or `start_line`/`line` for a range. `in_reply_to` replies to an existing
        thread, in which case the position is unnecessary.

        ⚠️ `commit_id` must be a commit **of the PR**: a foreign SHA returns
        a 422.
        """
        if not payload.get("body"):
            raise ValueError("`body` required.")
        if not payload.get("in_reply_to"):
            for champ in ("commit_id", "path"):
                if not payload.get(champ):
                    raise ValueError(
                        f"`{champ}` required for a new review comment "
                        "(except as a reply, with `in_reply_to`).")
        return self._request(
            "POST", f"/repos/{owner}/{repo}/pulls/{number}/comments",
            json=dict(payload))

    # --- reviewers --------------------------------------------------------------

    def list_requested_reviewers(self, owner: str, repo: str,
                                 number: Any) -> Any:
        """GET /repos/{owner}/{repo}/pulls/{number}/requested_reviewers — pending requests.

        ⚠️ Returns an OBJECT `{users: [...], teams: [...]}`, not a list.
        """
        return self._request(
            "GET", f"/repos/{owner}/{repo}/pulls/{number}/requested_reviewers")

    def request_reviewers(self, owner: str, repo: str, number: Any,
                          reviewers: Optional[List[str]] = None,
                          team_reviewers: Optional[List[str]] = None) -> Any:
        """POST /…/pulls/{number}/requested_reviewers — request a review.

        ⚠️ **Notifies** the designated people and teams. `reviewers` are
        account handles, `team_reviewers` team *slugs*.
        ⚠️ Requesting a review from the PR's AUTHOR returns a 422.
        """
        if not reviewers and not team_reviewers:
            raise ValueError(
                "pass `reviewers` (accounts) and/or `team_reviewers` (team "
                "slugs).")
        body: Dict[str, Any] = {}
        if reviewers:
            body["reviewers"] = list(reviewers)
        if team_reviewers:
            body["team_reviewers"] = list(team_reviewers)
        return self._request(
            "POST", f"/repos/{owner}/{repo}/pulls/{number}/requested_reviewers",
            json=body)

    def remove_requested_reviewers(self, owner: str, repo: str, number: Any,
                                   reviewers: Optional[List[str]] = None,
                                   team_reviewers: Optional[List[str]] = None) -> Any:
        """DELETE /…/pulls/{number}/requested_reviewers — remove a review request."""
        body: Dict[str, Any] = {}
        if reviewers:
            body["reviewers"] = list(reviewers)
        if team_reviewers:
            body["team_reviewers"] = list(team_reviewers)
        if not body:
            raise ValueError("pass `reviewers` and/or `team_reviewers`.")
        return self._request(
            "DELETE",
            f"/repos/{owner}/{repo}/pulls/{number}/requested_reviewers",
            json=body)
