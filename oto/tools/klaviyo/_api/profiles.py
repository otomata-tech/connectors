"""Account, profiles and marketing consent."""

from __future__ import annotations

from typing import List, Optional

from .shapes import job_profiles, path_id, resource

MAX_SUBSCRIBE = 1000
MAX_UNSUBSCRIBE = 100

_PROFILE_NOT_FOUND = (
    "profile_not_found",
    "No profile with this id in this Klaviyo account: read the ids from the "
    "list of profiles, filtered by email.")
_PROFILE_CONFLICT = (
    "profile_conflict",
    "Another profile already holds one of these identifiers (email, "
    "phone_number or external_id): read that profile from the list of "
    "profiles before writing.")


class ProfilesMixin:
    """Needs `_call` and `_list` (see `KlaviyoClient`)."""

    def get_account(self) -> dict:
        """The account of the key: `{data: [{type: "account", id, attributes:
        {contact_information, industry, timezone, preferred_currency,
        public_api_key, locale}}]}`. Scope accounts:read; 1/s, 15/min."""
        return self._call("GET", "/accounts", "get_account")

    def list_profiles(self, filter: Optional[str] = None,
                      sort: Optional[str] = None,
                      page_size: Optional[int] = None,
                      page_cursor: Optional[str] = None,
                      additional_fields: Optional[List[str]] = None, *,
                      all_pages: bool = False, max_pages: int = 10) -> dict:
        """List profiles (page_size <= 100), e.g.
        `filter='equals(email,"jane@example.com")'`. `additional_fields`:
        subscriptions, predictive_analytics. Scope profiles:read."""
        return self._list("/profiles", "list_profiles", {
            "filter": filter, "sort": sort, "page[size]": page_size,
            "page[cursor]": page_cursor,
            "additional-fields[profile]": additional_fields,
        }, all_pages=all_pages, max_pages=max_pages)

    def get_profile(self, profile_id: str,
                    additional_fields: Optional[List[str]] = None,
                    include: Optional[List[str]] = None) -> dict:
        """One profile by id. `include`: lists, segments (returned in
        `included`). Scope profiles:read."""
        return self._call(
            "GET", f"/profiles/{path_id('profile_id', profile_id)}",
            "get_profile",
            params={"additional-fields[profile]": additional_fields,
                    "include": include},
            refusals={404: _PROFILE_NOT_FOUND})

    def create_or_update_profile(self, data: dict) -> dict:
        """Create a profile, or update the one matching `data.id`, else the
        email, phone_number or external_id of `data.attributes`. A field set
        to None is cleared. Consent is unchanged. Scope profiles:write."""
        return self._call(
            "POST", "/profile-import", "create_or_update_profile",
            body=resource(data, "profile", "create_or_update_profile"),
            refusals={409: _PROFILE_CONFLICT})

    def subscribe_profiles(self, data: dict) -> dict:
        """Give email or SMS marketing consent to 1 to 1000 profiles (a
        `profile-subscription-bulk-create-job`). A double opt-in list sends
        them a confirmation message. Asynchronous: returns `{}` on 202.
        Scopes subscriptions:write, profiles:write, lists:write."""
        return self._call(
            "POST", "/profile-subscription-bulk-create-jobs",
            "subscribe_profiles",
            body=job_profiles(data, "profile-subscription-bulk-create-job",
                              MAX_SUBSCRIBE, "subscribe_profiles"))

    def unsubscribe_profiles(self, data: dict) -> dict:
        """Withdraw email or SMS marketing consent from 1 to 100 profiles (a
        `profile-subscription-bulk-delete-job`). Profiles outside the given
        list, or all without a list, are unsubscribed globally.
        Asynchronous: returns `{}` on 202. Scopes subscriptions:write,
        profiles:write, lists:write."""
        return self._call(
            "POST", "/profile-subscription-bulk-delete-jobs",
            "unsubscribe_profiles",
            body=job_profiles(data, "profile-subscription-bulk-delete-job",
                              MAX_UNSUBSCRIBE, "unsubscribe_profiles"))
