"""GitHub issues — tickets, comments, labels, milestones, assignments.

This mixin is never instantiated on its own: it is composed into `GitHubClient`, which
provides the transport (`_request`, `_get`, `_check_choice`).

⚠️ **At GitHub, a pull request IS an issue.** `GET /repos/…/issues` therefore
ALSO returns PRs, each carrying a `pull_request` key. This is the most
common trap of this API: counting a repository's issues without filtering gives a wrong
number, often by a lot. `list_issues(include_pull_requests=False)` — the
default — drops PRs client-side, since upstream offers no filter for that.

Symmetric consequence, and a useful one: the ISSUE comment, label and
assignment endpoints work as-is on a PR, by passing its number.
This is intended on GitHub's side, and is why `pulls.py` does not redeclare them.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..const import ISSUE_SORTS, ISSUE_STATE_WRITES, ISSUE_STATES, SORT_DIRECTIONS


class _IssuesMixin:
    """Issues, comments, labels, milestones."""

    # --- issues -------------------------------------------------------------

    def list_issues(self, owner: str, repo: str, state: Optional[str] = None,
                    labels: Optional[Any] = None,
                    assignee: Optional[str] = None,
                    creator: Optional[str] = None,
                    mentioned: Optional[str] = None,
                    milestone: Optional[Any] = None,
                    since: Optional[str] = None, sort: Optional[str] = None,
                    direction: Optional[str] = None,
                    include_pull_requests: bool = False,
                    per_page: Optional[int] = None,
                    page: Optional[int] = None) -> Any:
        """GET /repos/{owner}/{repo}/issues — the repository's issues.

        ⚠️ **Upstream ALSO returns pull requests** (at GitHub, a PR is an
        issue). `include_pull_requests=False` (the default) drops them HERE, for lack
        of a server-side filter. Setting it to `True` returns the API's raw
        response — useful to count "tickets + PRs" as the UI does.

        ⚠️ This filtering is done **after pagination**: a 30-row page of which
        12 are PRs returns 18. This is unavoidable without an upstream filter, and is
        one more reason to loop with `iterate` rather than read a single page.

        `labels` accepts a list (joined by commas). `state` is
        `open` (GitHub default), `closed` or `all`.
        """
        self._check_choice("state", state, ISSUE_STATES)
        self._check_choice("sort", sort, ISSUE_SORTS)
        self._check_choice("direction", direction, SORT_DIRECTIONS)
        payload = self._get(f"/repos/{owner}/{repo}/issues", {
            "state": state, "labels": labels, "assignee": assignee,
            "creator": creator, "mentioned": mentioned, "milestone": milestone,
            "since": since, "sort": sort, "direction": direction},
            per_page, page)
        if include_pull_requests or not isinstance(payload, list):
            return payload
        return [row for row in payload
                if not (isinstance(row, dict) and row.get("pull_request"))]

    def get_issue(self, owner: str, repo: str, number: Any) -> Any:
        """GET /repos/{owner}/{repo}/issues/{number} — one issue.

        Also returns a **pull request** if the number designates one: both
        share the same numbering within a repository.
        """
        return self._request("GET", f"/repos/{owner}/{repo}/issues/{number}")

    def create_issue(self, owner: str, repo: str,
                     payload: Dict[str, Any]) -> Any:
        """POST /repos/{owner}/{repo}/issues — create an issue.

        Required: `title`. Optional: `body`, `assignees`, `labels`,
        `milestone`.

        ⚠️ **Notifies**: assignees, repository watchers and anyone
        mentioned in `body` receive a notification. This is not
        a draft — GitHub has none for issues.
        """
        if not payload.get("title"):
            raise ValueError("`title` required to create an issue.")
        return self._request("POST", f"/repos/{owner}/{repo}/issues",
                             json=dict(payload))

    def update_issue(self, owner: str, repo: str, number: Any,
                     payload: Dict[str, Any]) -> Any:
        """PATCH /repos/{owner}/{repo}/issues/{number} — update an issue.

        Fields: `title`, `body`, `state` (`open`/`closed`), `state_reason`
        (`completed`/`not_planned`/`reopened`), `assignees`, `labels`,
        `milestone`.

        ⚠️ `labels` and `assignees` **REPLACE** the existing lists, they do not
        add to them. To add without overwriting: `add_labels` /
        `add_assignees`.
        """
        self._check_choice("state", payload.get("state"), ISSUE_STATE_WRITES)
        return self._request("PATCH",
                             f"/repos/{owner}/{repo}/issues/{number}",
                             json=dict(payload))

    def lock_issue(self, owner: str, repo: str, number: Any,
                   lock_reason: Optional[str] = None) -> Any:
        """PUT /repos/{owner}/{repo}/issues/{number}/lock — lock the conversation.

        `lock_reason`: `off-topic`, `too heated`, `resolved`, `spam`.
        """
        body = {"lock_reason": lock_reason} if lock_reason else None
        return self._request("PUT",
                             f"/repos/{owner}/{repo}/issues/{number}/lock",
                             json=body)

    def unlock_issue(self, owner: str, repo: str, number: Any) -> Any:
        """DELETE /repos/{owner}/{repo}/issues/{number}/lock — unlock."""
        return self._request("DELETE",
                             f"/repos/{owner}/{repo}/issues/{number}/lock")

    # --- comments ------------------------------------------------------------

    def list_issue_comments(self, owner: str, repo: str, number: Any,
                            since: Optional[str] = None,
                            per_page: Optional[int] = None,
                            page: Optional[int] = None) -> Any:
        """GET /repos/{owner}/{repo}/issues/{number}/comments — comments.

        Also works on a pull request (same numbering): these are the
        THREAD comments, distinct from line-by-line review comments
        (`list_review_comments`).
        """
        return self._get(f"/repos/{owner}/{repo}/issues/{number}/comments",
                         {"since": since}, per_page, page)

    def create_issue_comment(self, owner: str, repo: str, number: Any,
                             body: str) -> Any:
        """POST /repos/{owner}/{repo}/issues/{number}/comments — comment.

        ⚠️ **Notifies** the people subscribed to the thread. Also works on a PR.
        """
        if not body:
            raise ValueError("`body` required: an empty comment is refused.")
        return self._request(
            "POST", f"/repos/{owner}/{repo}/issues/{number}/comments",
            json={"body": body})

    def update_issue_comment(self, owner: str, repo: str, comment_id: Any,
                             body: str) -> Any:
        """PATCH /repos/{owner}/{repo}/issues/comments/{id} — edit a comment.

        ⚠️ The path carries the COMMENT id, not the issue number.
        """
        return self._request(
            "PATCH", f"/repos/{owner}/{repo}/issues/comments/{comment_id}",
            json={"body": body})

    def delete_issue_comment(self, owner: str, repo: str,
                             comment_id: Any) -> Any:
        """DELETE /repos/{owner}/{repo}/issues/comments/{id} — delete a comment.

        ⚠️ Permanent, no trash.
        """
        return self._request(
            "DELETE", f"/repos/{owner}/{repo}/issues/comments/{comment_id}")

    # --- labels ---------------------------------------------------------------

    def list_labels(self, owner: str, repo: str,
                    per_page: Optional[int] = None,
                    page: Optional[int] = None) -> Any:
        """GET /repos/{owner}/{repo}/labels — labels defined in the repository."""
        return self._get(f"/repos/{owner}/{repo}/labels", None, per_page, page)

    def create_label(self, owner: str, repo: str, name: str, color: str,
                     description: Optional[str] = None) -> Any:
        """POST /repos/{owner}/{repo}/labels — create a label.

        `color` is a hexadecimal **without `#`** (e.g. `"d73a4a"`): GitHub refuses
        the hash sign.
        """
        if color.startswith("#"):
            raise ValueError(
                "`color` is written without `#` (e.g. 'd73a4a') — GitHub refuses "
                "the hash sign.")
        body: Dict[str, Any] = {"name": name, "color": color}
        if description is not None:
            body["description"] = description
        return self._request("POST", f"/repos/{owner}/{repo}/labels", json=body)

    def add_labels(self, owner: str, repo: str, number: Any,
                   labels: List[str]) -> Any:
        """POST /repos/{owner}/{repo}/issues/{number}/labels — ADD labels.

        Unlike `update_issue(labels=…)`, which replaces the list.
        """
        if not labels:
            raise ValueError("`labels` required: at least one label.")
        return self._request(
            "POST", f"/repos/{owner}/{repo}/issues/{number}/labels",
            json={"labels": list(labels)})

    def set_labels(self, owner: str, repo: str, number: Any,
                   labels: List[str]) -> Any:
        """PUT /repos/{owner}/{repo}/issues/{number}/labels — REPLACE the labels.

        An empty list removes them all.
        """
        return self._request(
            "PUT", f"/repos/{owner}/{repo}/issues/{number}/labels",
            json={"labels": list(labels)})

    def remove_label(self, owner: str, repo: str, number: Any,
                     label: str) -> Any:
        """DELETE /repos/{owner}/{repo}/issues/{number}/labels/{label} — remove one."""
        return self._request(
            "DELETE", f"/repos/{owner}/{repo}/issues/{number}/labels/{label}")

    # --- assignment -------------------------------------------------------------

    def add_assignees(self, owner: str, repo: str, number: Any,
                      assignees: List[str]) -> Any:
        """POST /repos/{owner}/{repo}/issues/{number}/assignees — assign.

        ⚠️ GitHub **silently ignores** an account that does not have write access
        to the repository: the response comes back 201 without having assigned it. Compare the
        returned list with the requested one to see it.
        """
        return self._request(
            "POST", f"/repos/{owner}/{repo}/issues/{number}/assignees",
            json={"assignees": list(assignees)})

    def remove_assignees(self, owner: str, repo: str, number: Any,
                         assignees: List[str]) -> Any:
        """DELETE /repos/{owner}/{repo}/issues/{number}/assignees — unassign."""
        return self._request(
            "DELETE", f"/repos/{owner}/{repo}/issues/{number}/assignees",
            json={"assignees": list(assignees)})

    # --- milestones --------------------------------------------------------------

    def list_milestones(self, owner: str, repo: str,
                        state: Optional[str] = None,
                        per_page: Optional[int] = None,
                        page: Optional[int] = None) -> Any:
        """GET /repos/{owner}/{repo}/milestones — the repository's milestones."""
        self._check_choice("state", state, ISSUE_STATES)
        return self._get(f"/repos/{owner}/{repo}/milestones", {"state": state},
                         per_page, page)

    def create_milestone(self, owner: str, repo: str, title: str,
                         payload: Optional[Dict[str, Any]] = None) -> Any:
        """POST /repos/{owner}/{repo}/milestones — create a milestone.

        Optional: `state`, `description`, `due_on`.
        """
        body: Dict[str, Any] = {"title": title}
        body.update(payload or {})
        return self._request("POST", f"/repos/{owner}/{repo}/milestones",
                             json=body)
