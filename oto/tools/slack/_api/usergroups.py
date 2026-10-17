"""Slack user groups (`usergroups.*`) — the `@team` handles of a workspace.

This mixin is never instantiated on its own: it is composed into `SlackClient`,
which provides the transport (`_request`) and the token routing (`_prefer`).

Contract (Slack reference, `docs.slack.dev/reference/methods/usergroups.*`):

- scopes: `usergroups:read` for reads, `usergroups:write` for writes — the same
  names on a bot and on a user token;
- ⚠️ **a bot token can only WRITE when the workspace lets EVERYONE manage user
  groups.** Otherwise Slack answers `permission_denied`, and only the user token
  of a member allowed by that setting can do it;
- ⚠️ user groups require a paid plan: on a free workspace every call answers
  `plan_upgrade_required` (or `paid_teams_only` on create);
- ⚠️ **`usergroups.users.update` REPLACES the whole member list**, and refuses an
  empty one (`no_users_provided` / `invalid_users`): a group is emptied by
  disabling it. Adding or removing ONE member is therefore read-then-replace, on
  the caller's side;
- list arguments (`users`, `channels`) travel as comma-separated strings, sent
  form-encoded.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


def _csv(values: Optional[Iterable[str]]) -> Optional[str]:
    """Comma-join a list of IDs; a ready-made string passes through."""
    if values is None:
        return None
    if isinstance(values, str):
        return values
    return ",".join(v.strip() for v in values if v and v.strip())


class _UsergroupsMixin:
    """User groups: list, members, create, update, enable/disable."""

    # --- reads ---------------------------------------------------------------

    def list_usergroups(
        self,
        include_users: bool = False,
        include_disabled: bool = False,
        include_count: bool = False,
        as_user: Optional[bool] = None,
    ) -> List[Dict[str, Any]]:
        """`usergroups.list` — the workspace's user groups.

        A disabled group carries `date_delete != 0`; it is only returned with
        `include_disabled=True`.

        Args:
            include_users: Add each group's member IDs (`users`).
            include_disabled: Also return disabled groups.
            include_count: Add each group's `user_count`.
            as_user: Token override; None → bot (either token reads).

        Returns:
            The list of usergroup objects (`id`, `name`, `handle`, `description`,
            `date_delete`, `prefs.channels`, …).
        """
        if as_user is None:
            as_user = self._prefer(want_user=False)
        params: Dict[str, Any] = {}
        if include_users:
            params["include_users"] = "true"
        if include_disabled:
            params["include_disabled"] = "true"
        if include_count:
            params["include_count"] = "true"
        data = self._request("GET", "usergroups.list", as_user=as_user, params=params)
        return data.get("usergroups", [])

    def usergroup_users(
        self,
        usergroup: str,
        include_disabled: bool = False,
        as_user: Optional[bool] = None,
    ) -> List[str]:
        """`usergroups.users.list` — the member IDs of one group.

        Args:
            usergroup: User group ID (`S…`).
            include_disabled: Allow reading a disabled group.
            as_user: Token override; None → bot (either token reads).

        Returns:
            The list of user IDs (`U…`).
        """
        if as_user is None:
            as_user = self._prefer(want_user=False)
        params: Dict[str, Any] = {"usergroup": usergroup}
        if include_disabled:
            params["include_disabled"] = "true"
        data = self._request("GET", "usergroups.users.list", as_user=as_user, params=params)
        return data.get("users", [])

    # --- writes --------------------------------------------------------------

    def create_usergroup(
        self,
        name: str,
        handle: Optional[str] = None,
        description: Optional[str] = None,
        channels: Optional[Iterable[str]] = None,
        as_user: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """`usergroups.create` — a new, EMPTY group (members are set afterwards).

        `name` and `handle` must be unique in the workspace
        (`name_already_exists` / `handle_already_exists`).

        Args:
            name: Display name.
            handle: Mention handle, without `@`.
            description: Short description.
            channels: Default channel IDs of the group.
            as_user: Same conventions as `post_message`.

        Returns:
            Response data with the created `usergroup`.
        """
        data: Dict[str, Any] = {"name": name}
        if handle:
            data["handle"] = handle
        if description:
            data["description"] = description
        if channels is not None:
            data["channels"] = _csv(channels)
        return self._request("POST", "usergroups.create", as_user=as_user, data=data)

    def update_usergroup(
        self,
        usergroup: str,
        name: Optional[str] = None,
        handle: Optional[str] = None,
        description: Optional[str] = None,
        channels: Optional[Iterable[str]] = None,
        as_user: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """`usergroups.update` — rename, re-handle, re-describe a group, or set
        its default channels. Only the fields given are sent; the others keep
        their value.

        Args:
            usergroup: User group ID (`S…`).
            name: New display name.
            handle: New mention handle, without `@`.
            description: New description.
            channels: New default channel IDs (replaces the list).
            as_user: Same conventions as `post_message`.

        Returns:
            Response data with the updated `usergroup`.
        """
        data: Dict[str, Any] = {"usergroup": usergroup}
        if name is not None:
            data["name"] = name
        if handle is not None:
            data["handle"] = handle
        if description is not None:
            data["description"] = description
        if channels is not None:
            data["channels"] = _csv(channels)
        return self._request("POST", "usergroups.update", as_user=as_user, data=data)

    def set_usergroup_users(
        self,
        usergroup: str,
        users: Iterable[str],
        as_user: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """`usergroups.users.update` — REPLACE the group's whole member list.

        ⚠️ Not an add: whoever is missing from `users` leaves the group. An empty
        list is refused here, before the call — Slack refuses it too, and the
        way to empty a group is `disable_usergroup`.

        Args:
            usergroup: User group ID (`S…`).
            users: The FULL list of member IDs (`U…`).
            as_user: Same conventions as `post_message`.

        Returns:
            Response data with the updated `usergroup`.
        """
        csv = _csv(users)
        if not csv:
            raise ValueError(
                "a user group cannot be set to zero members — disable it instead")
        return self._request("POST", "usergroups.users.update", as_user=as_user,
                             data={"usergroup": usergroup, "users": csv})

    def disable_usergroup(self, usergroup: str,
                          as_user: Optional[bool] = None) -> Dict[str, Any]:
        """`usergroups.disable` — hide a group (its handle stops notifying).
        Reversible with `enable_usergroup`; members are kept."""
        return self._request("POST", "usergroups.disable", as_user=as_user,
                             data={"usergroup": usergroup})

    def enable_usergroup(self, usergroup: str,
                         as_user: Optional[bool] = None) -> Dict[str, Any]:
        """`usergroups.enable` — bring a disabled group back."""
        return self._request("POST", "usergroups.enable", as_user=as_user,
                             data={"usergroup": usergroup})
