"""GitHub organizations — members, teams, repository collaborators.

This mixin is never instantiated on its own: it is composed into `GitHubClient`, which
provides the transport (`_request`, `_get`, `_check_choice`).

⚠️ **Three notions of membership look alike and are not the same thing:**

- **member of an ORGANIZATION** (`/orgs/{org}/members`) — global membership,
  which can be public or private;
- **member of a TEAM** (`/orgs/{org}/teams/{slug}/members`) — a subset,
  which carries rights on the team's repositories;
- **collaborator on a REPOSITORY** (`/repos/{owner}/{repo}/collaborators`) — access to
  a specific repository, without membership in the organization.

Removing someone from one does not remove them from the others, and this is the most
frequent source of error here — hence three families of methods named after
their scope, never a generic `remove_member`.

⚠️ **`list_members` only shows by default what the token is allowed to
see.** A token without the organization scope will only see *public* members
— a shorter list, with no error. It is therefore not a census.

**Deliberately absent**: deleting an organization, and managing installed
GitHub Apps.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ...common import raise_for_upstream
from ..const import (COLLABORATOR_PERMISSIONS, MEMBER_FILTERS,
                     MEMBERSHIP_ROLES, TEAM_ROLES)


class _OrgsMixin:
    """Organizations, teams, members, collaborators."""

    # --- identity --------------------------------------------------------------

    def me(self) -> Any:
        """GET /user — the account holding the token.

        **No particular scope required**: it is the connector's authentication
        probe. A 401 says the token is bad or revoked; a response
        says who it is, proving nothing about its rights.
        """
        return self._request("GET", "/user")

    def rate_limit(self) -> Any:
        """GET /rate_limit — the quota state, **without consuming them**.

        The only endpoint that does not count against the primary limit: useful to
        explain a 403 without making the situation worse.
        """
        return self._request("GET", "/rate_limit")

    def list_my_orgs(self, per_page: Optional[int] = None,
                     page: Optional[int] = None) -> Any:
        """GET /user/orgs — organizations of the token holder.

        ⚠️ A classic token without the `read:org` scope returns an **empty** list
        rather than an error: absence there does not prove non-membership.
        """
        return self._get("/user/orgs", None, per_page, page)

    # --- organizations -----------------------------------------------------------

    def get_org(self, org: str) -> Any:
        """GET /orgs/{org} — an organization's profile."""
        return self._request("GET", f"/orgs/{org}")

    def list_org_members(self, org: str, filter: Optional[str] = None,
                         role: Optional[str] = None,
                         per_page: Optional[int] = None,
                         page: Optional[int] = None) -> Any:
        """GET /orgs/{org}/members — members of the organization.

        `role` restricts to `admin` (owners) or `member`.
        `filter="2fa_disabled"` lists those without two-factor authentication —
        reserved for owners.

        ⚠️ Without sufficient rights, only **public** members are returned.
        """
        self._check_choice("filter", filter, MEMBER_FILTERS)
        self._check_choice("role", role, MEMBERSHIP_ROLES)
        return self._get(f"/orgs/{org}/members", {"filter": filter,
                                                  "role": role},
                         per_page, page)

    def check_org_membership(self, org: str, username: str) -> bool:
        """GET /orgs/{org}/members/{username} — is this person a member?

        ⚠️ Bodyless endpoint: **204 if a member, 404 otherwise** (and 302 if the token
        is not allowed to know). The 404 is an ANSWER, not an error —
        hence this boolean.
        """
        resp = self._request("GET", f"/orgs/{org}/members/{username}", raw=True)
        if resp.status_code == 204:
            return True
        if resp.status_code in (404, 302):
            return False
        raise_for_upstream(resp, service="github")
        return False

    def get_org_membership(self, org: str, username: str) -> Any:
        """GET /orgs/{org}/memberships/{username} — the detailed membership.

        Unlike `check_org_membership`, returns the role and the state
        (`active` / `pending` — an invitation not yet accepted).
        """
        return self._request("GET", f"/orgs/{org}/memberships/{username}")

    def set_org_membership(self, org: str, username: str,
                           role: Optional[str] = None) -> Any:
        """PUT /orgs/{org}/memberships/{username} — invite or change a role.

        ⚠️ **Sends an email invitation** if the person is not already a
        member, and its state stays `pending` until they accept. On an existing
        member, changes their role (`admin` = organization
        owner, a very broad right).
        """
        self._check_choice("role", role, MEMBERSHIP_ROLES)
        body = {"role": role} if role else None
        return self._request("PUT", f"/orgs/{org}/memberships/{username}",
                             json=body)

    def remove_org_member(self, org: str, username: str) -> Any:
        """DELETE /orgs/{org}/members/{username} — **remove from the organization**.

        ⚠️ Removes the person from the organization AND from all its teams, and makes them
        lose access to private repositories. Does not delete their contributions.
        This does NOT remove them from repositories where they are an individual
        collaborator: see `remove_collaborator`.
        """
        return self._request("DELETE", f"/orgs/{org}/members/{username}")

    # --- teams --------------------------------------------------------------------

    def list_teams(self, org: str, per_page: Optional[int] = None,
                   page: Optional[int] = None) -> Any:
        """GET /orgs/{org}/teams — visible teams of the organization."""
        return self._get(f"/orgs/{org}/teams", None, per_page, page)

    def get_team(self, org: str, team_slug: str) -> Any:
        """GET /orgs/{org}/teams/{team_slug} — one team.

        ⚠️ The key is the **slug** (in the URL), not the display name.
        """
        return self._request("GET", f"/orgs/{org}/teams/{team_slug}")

    def list_team_members(self, org: str, team_slug: str,
                          role: Optional[str] = None,
                          per_page: Optional[int] = None,
                          page: Optional[int] = None) -> Any:
        """GET /orgs/{org}/teams/{team_slug}/members — members of a team.

        `role`: `member` or `maintainer`.
        """
        self._check_choice("role", role, TEAM_ROLES)
        return self._get(f"/orgs/{org}/teams/{team_slug}/members",
                         {"role": role}, per_page, page)

    def list_team_repos(self, org: str, team_slug: str,
                        per_page: Optional[int] = None,
                        page: Optional[int] = None) -> Any:
        """GET /orgs/{org}/teams/{team_slug}/repos — repositories managed by the team."""
        return self._get(f"/orgs/{org}/teams/{team_slug}/repos", None,
                         per_page, page)

    def add_team_member(self, org: str, team_slug: str, username: str,
                        role: Optional[str] = None) -> Any:
        """PUT /orgs/{org}/teams/{team_slug}/memberships/{username} — add to the team.

        ⚠️ The person must **already be a member of the organization**; otherwise, this
        call sends them an invitation to join, and the membership stays
        `pending`.
        """
        self._check_choice("role", role, TEAM_ROLES)
        body = {"role": role} if role else None
        return self._request(
            "PUT", f"/orgs/{org}/teams/{team_slug}/memberships/{username}",
            json=body)

    def remove_team_member(self, org: str, team_slug: str,
                           username: str) -> Any:
        """DELETE /orgs/{org}/teams/{team_slug}/memberships/{username} — remove from the TEAM.

        ⚠️ Does NOT remove from the organization: the person keeps their global
        membership and the access that follows from it.
        """
        return self._request(
            "DELETE", f"/orgs/{org}/teams/{team_slug}/memberships/{username}")

    # --- repository collaborators ---------------------------------------------------

    def list_collaborators(self, owner: str, repo: str,
                           affiliation: Optional[str] = None,
                           permission: Optional[str] = None,
                           per_page: Optional[int] = None,
                           page: Optional[int] = None) -> Any:
        """GET /repos/{owner}/{repo}/collaborators — who has access to this repository.

        `affiliation`: `outside`, `direct` or `all` (default) — "direct" excludes
        access inherited from a team, which is often the real question.
        """
        self._check_choice("permission", permission, COLLABORATOR_PERMISSIONS)
        return self._get(f"/repos/{owner}/{repo}/collaborators",
                         {"affiliation": affiliation, "permission": permission},
                         per_page, page)

    def check_collaborator(self, owner: str, repo: str, username: str) -> bool:
        """GET /repos/{owner}/{repo}/collaborators/{username} — do they have access?

        ⚠️ Bodyless endpoint: **204 if yes, 404 if no**. The 404 is an
        answer, not an error.
        """
        resp = self._request(
            "GET", f"/repos/{owner}/{repo}/collaborators/{username}", raw=True)
        if resp.status_code == 204:
            return True
        if resp.status_code == 404:
            return False
        raise_for_upstream(resp, service="github")
        return False

    def get_collaborator_permission(self, owner: str, repo: str,
                                    username: str) -> Any:
        """GET /repos/{owner}/{repo}/collaborators/{username}/permission — their level.

        Returns the EFFECTIVE level, team inheritance included — which
        `list_collaborators(affiliation="direct")` would not say.
        """
        return self._request(
            "GET",
            f"/repos/{owner}/{repo}/collaborators/{username}/permission")

    def add_collaborator(self, owner: str, repo: str, username: str,
                         permission: Optional[str] = None) -> Any:
        """PUT /repos/{owner}/{repo}/collaborators/{username} — invite to the repository.

        ⚠️ **Sends an invitation**: access is only effective once
        accepted (the response then carries the invitation, not an active access).
        `permission`: `pull`, `triage`, `push`, `maintain`, `admin`.
        """
        self._check_choice("permission", permission, COLLABORATOR_PERMISSIONS)
        body = {"permission": permission} if permission else None
        return self._request(
            "PUT", f"/repos/{owner}/{repo}/collaborators/{username}", json=body)

    def remove_collaborator(self, owner: str, repo: str,
                            username: str) -> Any:
        """DELETE /repos/{owner}/{repo}/collaborators/{username} — remove from the REPOSITORY.

        ⚠️ Does not remove from the organization, and **does not remove access inherited
        from a team**: if the person has the repository through their team, they keep it.
        Check with `get_collaborator_permission` afterwards.
        """
        return self._request(
            "DELETE", f"/repos/{owner}/{repo}/collaborators/{username}")
