"""Workspaces and forms: reading, creating, replacing, patching, deleting."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from ..params import _segment

#: Paths a PATCH may replace, as the Create API reference lists them.
PATCH_PATHS = frozenset({
    "/settings/enrichment_in_renderer",
    "/settings/email_consent_notifications_config",
    "/settings/facebook_pixel",
    "/settings/google_analytics",
    "/settings/google_tag_manager",
    "/settings/is_public",
    "/settings/meta",
    "/theme",
    "/title",
    "/workspace",
})


def _form_body(title: str, form_type: Optional[str], settings: Optional[Dict[str, Any]],
               theme: Optional[Dict[str, Any]], workspace: Optional[Dict[str, Any]],
               hidden: Optional[List[str]], variables: Optional[Dict[str, Any]],
               welcome_screens: Optional[List[Dict[str, Any]]],
               thankyou_screens: Optional[List[Dict[str, Any]]],
               fields: Optional[List[Dict[str, Any]]],
               logic: Optional[List[Dict[str, Any]]]) -> Dict[str, Any]:
    if not isinstance(title, str) or not title.strip():
        raise ValueError("`title` must be a non-empty string.")
    body = {"title": title, "type": form_type, "settings": settings, "theme": theme,
            "workspace": workspace, "hidden": hidden, "variables": variables,
            "welcome_screens": welcome_screens, "thankyou_screens": thankyou_screens,
            "fields": fields, "logic": logic}
    return {k: v for k, v in body.items() if v is not None}


def _patch_operations(operations: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Checks the JSON Patch operations before anything is sent: `replace` on a
    documented path, with a value."""
    if not isinstance(operations, list) or not operations:
        raise ValueError("`operations` must be a non-empty list of {op, path, value}.")
    checked = []
    for i, operation in enumerate(operations):
        if not isinstance(operation, dict) or set(operation) != {"op", "path", "value"}:
            raise ValueError(f"`operations[{i}]` must be exactly {{op, path, value}}.")
        if operation["op"] != "replace":
            raise ValueError(f"`operations[{i}].op` must be 'replace'.")
        if operation["path"] not in PATCH_PATHS:
            raise ValueError(f"`operations[{i}].path` {operation['path']!r} cannot be "
                             f"patched; allowed: {', '.join(sorted(PATCH_PATHS))}.")
        checked.append(dict(operation))
    return checked


class _FormsMixin:
    """Workspaces and forms. Transport (`_get`, `_send`) comes from the client."""

    # ------------------------------------------------------------------
    # Workspaces
    # ------------------------------------------------------------------

    def list_workspaces(self, *, search: Optional[str] = None,
                        page: Optional[int] = None,
                        page_size: Optional[int] = None) -> Any:
        """GET /workspaces — every workspace the token can access, across
        organizations. `{total_items, page_count, items: [{id, name,
        account_id, shared, forms: {count, href}, self: {href}}]}`.

        Args:
            search: only workspaces containing this string.
            page: 1-based page number (default 1).
            page_size: default 10, maximum 200.
        """
        return self._get("/workspaces", search=search, page=page, page_size=page_size)

    # ------------------------------------------------------------------
    # Forms — reading
    # ------------------------------------------------------------------

    def list_forms(self, *, search: Optional[str] = None,
                   page: Optional[int] = None,
                   page_size: Optional[int] = None,
                   workspace_id: Optional[str] = None,
                   sort_by: Optional[str] = None,
                   order_by: Optional[str] = None,
                   is_public: Optional[bool] = None) -> Any:
        """GET /forms — forms of the account, public and private.
        `{total_items, page_count, items: [{id, title, created_at,
        last_updated_at, settings: {is_public}, self, theme, _links: {display,
        responses}}]}`.

        Args:
            search: only forms containing this string.
            page: 1-based page number (default 1).
            page_size: default 10, maximum 200.
            workspace_id: only the forms of this workspace.
            sort_by: `created_at` | `last_updated_at`.
            order_by: `asc` | `desc`.
            is_public: filter on `settings.is_public`.
        """
        return self._get("/forms", search=search, page=page, page_size=page_size,
                         workspace_id=workspace_id, sort_by=sort_by,
                         order_by=order_by,
                         is_public=None if is_public is None else str(is_public).lower())

    def get_form(self, form_id: str) -> Any:
        """GET /forms/{form_id} — the full form definition: `title`, `fields`
        (each `{id, ref, title, type, properties: {description, choices:
        [{id, ref, label}], fields: […] for group/matrix, …}, validations}`),
        `hidden`, `variables`, `logic`, screens, `settings`, `_links: {display,
        responses}`. A response's `answers[].field.id`/`ref` point to these
        fields.

        Args:
            form_id: the form id (the last path segment of the form's public
                URL, e.g. `u6nXL7` in `…typeform.com/to/u6nXL7`).
        """
        return self._get(f"/forms/{_segment('form_id', form_id)}")

    # ------------------------------------------------------------------
    # Forms — writing (scope forms:write)
    # ------------------------------------------------------------------

    def create_form(self, *, title: str, type: Optional[str] = None,
                    settings: Optional[Dict[str, Any]] = None,
                    theme: Optional[Dict[str, Any]] = None,
                    workspace: Optional[Dict[str, Any]] = None,
                    hidden: Optional[List[str]] = None,
                    variables: Optional[Dict[str, Any]] = None,
                    welcome_screens: Optional[List[Dict[str, Any]]] = None,
                    thankyou_screens: Optional[List[Dict[str, Any]]] = None,
                    fields: Optional[List[Dict[str, Any]]] = None,
                    logic: Optional[List[Dict[str, Any]]] = None) -> Any:
        """POST /forms — creates a form and returns it (201), with its `id`
        and `_links.display`.

        ⚠️ `settings.is_public` defaults to true upstream: the form is live at
        once unless `settings={"is_public": False}`. Images must already exist
        in the account. `workspace` is `{href: <workspace URL>}`.

        Args:
            title: form title.
            type: `quiz` (default upstream), `classification`, `score`,
                `branching`, `classification_branching`, `score_branching`.
            settings, theme, workspace, hidden, variables, welcome_screens,
                thankyou_screens, fields, logic: as in the Create API
                reference; each field is at least `{title, type}`.
        """
        body = _form_body(title, type, settings, theme, workspace, hidden, variables,
                          welcome_screens, thankyou_screens, fields, logic)
        return self._send("POST", "/forms", json=body)

    def replace_form(self, form_id: str, *, title: str, type: Optional[str] = None,
                     settings: Optional[Dict[str, Any]] = None,
                     theme: Optional[Dict[str, Any]] = None,
                     workspace: Optional[Dict[str, Any]] = None,
                     hidden: Optional[List[str]] = None,
                     variables: Optional[Dict[str, Any]] = None,
                     welcome_screens: Optional[List[Dict[str, Any]]] = None,
                     thankyou_screens: Optional[List[Dict[str, Any]]] = None,
                     fields: Optional[List[Dict[str, Any]]] = None,
                     logic: Optional[List[Dict[str, Any]]] = None) -> Any:
        """PUT /forms/{form_id} — overwrites the WHOLE definition and returns
        the form.

        ⚠️ Destructive: a field left out is deleted together with its answers
        in every response, and an existing field must keep its `id`. Start
        from `get_form`, change it, send it back whole. Without `theme`,
        Typeform applies a new copy of the default theme. To change only the
        title, visibility, theme or workspace, use `update_form`.
        """
        body = _form_body(title, type, settings, theme, workspace, hidden, variables,
                          welcome_screens, thankyou_screens, fields, logic)
        return self._send("PUT", f"/forms/{_segment('form_id', form_id)}", json=body)

    def update_form(self, form_id: str, operations: List[Dict[str, Any]]) -> None:
        """PATCH /forms/{form_id} — JSON Patch: replaces some top-level parts of
        a form without touching its fields. 204, nothing returned.

        Each operation is `{"op": "replace", "path": <path>, "value": …}`, the
        path one of `PATCH_PATHS`: `/title`, `/theme` (`{href}`), `/workspace`
        (`{href}`), `/settings/is_public` (publish with true, unpublish with
        false), `/settings/meta`, and the tracking and enrichment settings.
        Anything else is refused here, before any call.
        """
        self._send("PATCH", f"/forms/{_segment('form_id', form_id)}",
                   json=_patch_operations(operations))

    def delete_form(self, form_id: str) -> None:
        """DELETE /forms/{form_id} — ⚠️ deletes the form AND all its responses,
        irreversibly. 204, nothing returned."""
        self._send("DELETE", f"/forms/{_segment('form_id', form_id)}")
