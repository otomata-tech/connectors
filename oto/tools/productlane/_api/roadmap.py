"""Productlane roadmap — projects and issues, both **backed by Linear**.

This mixin is never instantiated on its own: it is composed into `ProductlaneClient`,
which provides the transport (`_request`, `_list`, `_check_choice`).

⚠️ **This is not a standalone roadmap: Linear must be connected.** Three
consequences worth knowing before reading a call's result:

- **creation starts from Linear** — an issue is filed THERE first, then
  mirrored here; without Linear connected, creation fails;
- **update does not** — a local write succeeds even if the Linear sync
  fails: the failure is logged on the vendor side and **does not surface in the
  response**. A `200` therefore does not prove that Linear followed;
- **deletion archives** in Linear and soft-deletes here, with the same
  asymmetry.

`team_id`, `state_id`, `assignee_id`, `label_ids`, `linear_status_id` are
**Linear** identifiers, not Productlane ones: read them via `list_workflow_states`,
`list_project_statuses` and the Linear connector.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..const import PROJECT_STATES, ROADMAP_SORTS


class _RoadmapMixin:
    """Roadmap projects and issues."""

    # --- projects -----------------------------------------------------------

    def list_projects(self, limit: Optional[int] = None,
                      cursor: Optional[str] = None,
                      state: Optional[str] = None,
                      name_contains: Optional[str] = None,
                      linear_team_id: Optional[str] = None,
                      sort: Optional[str] = None,
                      created_after: Optional[str] = None,
                      created_before: Optional[str] = None,
                      updated_after: Optional[str] = None,
                      updated_before: Optional[str] = None) -> Any:
        """GET /projects — roadmap projects. Scope `projects:read`.

        `sort="total_score"` ranks by the weight of attached customer feedback
        (customer needs), whereas `created_at` is the default order of v2 lists.
        """
        self._check_choice("state", state, PROJECT_STATES)
        self._check_choice("sort", sort, ROADMAP_SORTS)
        return self._list("/projects", limit, cursor, {
            "state": state, "name_contains": name_contains,
            "linear_team_id": linear_team_id, "sort": sort,
            "created_after": created_after, "created_before": created_before,
            "updated_after": updated_after, "updated_before": updated_before,
        })

    def get_project(self, project_id: str) -> Any:
        """GET /projects/{id} — one project. Scope `projects:read`."""
        return self._request("GET", f"/projects/{project_id}")

    def list_project_statuses(self) -> Any:
        """GET /projects/statuses — Linear project statuses, at organization level.

        Scope `projects:read`, **Linear connected required**. Used to fill
        `linear_status_id`.
        """
        return self._request("GET", "/projects/statuses")

    def create_project(self, payload: Dict[str, Any]) -> Any:
        """POST /projects — create a project, **synced to Linear**.

        Scope `projects:write`, Linear connected required. Required: `name`,
        `team_id` (LINEAR team identifier). Optional: `description`,
        `icon`, `color`, `state`, `linear_status_id`, `is_visible`.

        `is_visible` decides whether it appears on the **public roadmap**.
        """
        self._check_choice("state", payload.get("state"), PROJECT_STATES)
        return self._request("POST", "/projects", json=dict(payload))

    def update_project(self, project_id: str, payload: Dict[str, Any]) -> Any:
        """PATCH /projects/{id} — update a project. Scope `projects:write`.

        Fields: `name`, `description`, `icon`, `color`, `state`,
        `linear_status_id`, `is_visible`.

        ⚠️ A Linear sync failure is logged on the vendor side and **does not block**
        the local update: the response can be a success while
        Linear did not follow.
        """
        self._check_choice("state", payload.get("state"), PROJECT_STATES)
        return self._request("PATCH", f"/projects/{project_id}",
                             json=dict(payload))

    def delete_project(self, project_id: str) -> Any:
        """DELETE /projects/{id} — archive in Linear, soft-delete here.

        Scope `projects:write`. A failure on the Linear side does not block the
        local soft-delete.
        """
        return self._request("DELETE", f"/projects/{project_id}")

    # --- issues -------------------------------------------------------------

    def list_issues(self, limit: Optional[int] = None,
                    cursor: Optional[str] = None,
                    project_id: Optional[str] = None,
                    status: Optional[str] = None,
                    name_contains: Optional[str] = None,
                    linear_team_id: Optional[str] = None,
                    sort: Optional[str] = None,
                    created_after: Optional[str] = None,
                    created_before: Optional[str] = None,
                    updated_after: Optional[str] = None,
                    updated_before: Optional[str] = None) -> Any:
        """GET /issues — roadmap issues. Scope `issues:read`.

        ⚠️ `status` is NOT a closed enum here: issue states are
        the **workflow states of the Linear team**, specific to each workspace.
        Read them via `list_workflow_states(team_id)` — hardcoding a value
        would work for one customer and not the next.
        """
        self._check_choice("sort", sort, ROADMAP_SORTS)
        return self._list("/issues", limit, cursor, {
            "project_id": project_id, "status": status,
            "name_contains": name_contains, "linear_team_id": linear_team_id,
            "sort": sort,
            "created_after": created_after, "created_before": created_before,
            "updated_after": updated_after, "updated_before": updated_before,
        })

    def get_issue(self, issue_id: str) -> Any:
        """GET /issues/{id} — one issue. Scope `issues:read`."""
        return self._request("GET", f"/issues/{issue_id}")

    def list_workflow_states(self, team_id: str) -> Any:
        """GET /issues/workflow-states — Linear states of a team. Scope `issues:read`.

        `team_id` is **required** by upstream, and Linear must be connected. This is
        the source of the `state_id` values to pass to `create_issue` / `update_issue`.
        """
        if not team_id:
            raise ValueError(
                "`team_id` is required: workflow states are specific to a "
                "Linear team.")
        return self._request("GET", "/issues/workflow-states",
                             params={"team_id": team_id})

    def create_issue(self, payload: Dict[str, Any]) -> Any:
        """POST /issues — create an issue, **filed in Linear first**.

        Scope `issues:write`, Linear connected required. Required: `title`,
        `team_id`, `state_id`, `priority`. Optional: `description`,
        `project_id`, `assignee_id`, `label_ids`, `is_visible`.

        ⚠️ `priority` follows **Linear** numbering: `0` = no priority,
        `1` = urgent, then 2, 3, 4 in decreasing order of urgency. It is not
        an ascending scale, and `0` does not mean "the lowest".
        """
        return self._request("POST", "/issues", json=dict(payload))

    def update_issue(self, issue_id: str, payload: Dict[str, Any]) -> Any:
        """PATCH /issues/{id} — update an issue. Scope `issues:write`.

        Fields: `title`, `description`, `state_id`, `priority`, `project_id`,
        `assignee_id`, `is_visible`. Same asymmetry as projects: a Linear
        sync failure **does not block** the local write.
        """
        return self._request("PATCH", f"/issues/{issue_id}", json=dict(payload))

    def delete_issue(self, issue_id: str) -> Any:
        """DELETE /issues/{id} — archive in Linear, soft-delete here.

        Scope `issues:write`.
        """
        return self._request("DELETE", f"/issues/{issue_id}")
