"""Lists, segments and list membership."""

from __future__ import annotations

from typing import List, Optional

from .shapes import path_id, profile_refs

_LIST_NOT_FOUND = (
    "list_not_found",
    "No list with this id in this Klaviyo account: read the ids from the "
    "list of lists.")
_SEGMENT_NOT_FOUND = (
    "segment_not_found",
    "No segment with this id in this Klaviyo account: read the ids from the "
    "list of segments.")


class ListsMixin:
    """Needs `_call` and `_list` (see `KlaviyoClient`)."""

    # --- Lists ------------------------------------------------------------

    def list_lists(self, filter: Optional[str] = None,
                   sort: Optional[str] = None,
                   page_size: Optional[int] = None,
                   page_cursor: Optional[str] = None, *,
                   all_pages: bool = False, max_pages: int = 10) -> dict:
        """List the lists (page_size <= 10). Scope lists:read."""
        return self._list("/lists", "list_lists", {
            "filter": filter, "sort": sort, "page[size]": page_size,
            "page[cursor]": page_cursor,
        }, all_pages=all_pages, max_pages=max_pages)

    def get_list(self, list_id: str,
                 additional_fields: Optional[List[str]] = None) -> dict:
        """One list by id; `additional_fields=["profile_count"]` adds the
        member count (1/s, 15/min). Scope lists:read."""
        extra = ["list_profile_count"] if additional_fields else []
        return self._call(
            "GET", f"/lists/{path_id('list_id', list_id)}", "get_list",
            params={"additional-fields[list]": additional_fields},
            refusals={404: _LIST_NOT_FOUND}, extra_buckets=extra)

    def list_profiles_in_list(self, list_id: str,
                              filter: Optional[str] = None,
                              sort: Optional[str] = None,
                              page_size: Optional[int] = None,
                              page_cursor: Optional[str] = None, *,
                              all_pages: bool = False,
                              max_pages: int = 10) -> dict:
        """The profiles of one list (page_size <= 100). Scopes lists:read,
        profiles:read."""
        return self._list(
            f"/lists/{path_id('list_id', list_id)}/profiles",
            "list_profiles_in_list", {
                "filter": filter, "sort": sort, "page[size]": page_size,
                "page[cursor]": page_cursor,
            }, all_pages=all_pages, max_pages=max_pages,
            refusals={404: _LIST_NOT_FOUND})

    def add_profiles_to_list(self, list_id: str, data: List[dict]) -> dict:
        """Add 1 to 1000 profiles (`[{type: "profile", id}]`) to a list.
        Consent is unchanged, but flows triggered by the list start for them.
        Returns `{}` (204). Scopes lists:write, profiles:write."""
        return self._call(
            "POST",
            f"/lists/{path_id('list_id', list_id)}/relationships/profiles",
            "add_profiles_to_list",
            body=profile_refs(data, "add_profiles_to_list"),
            refusals={404: _LIST_NOT_FOUND})

    def remove_profiles_from_list(self, list_id: str,
                                  data: List[dict]) -> dict:
        """Remove 1 to 1000 profiles (`[{type: "profile", id}]`) from a list.
        Consent is unchanged. Returns `{}` (204). Scopes lists:write,
        profiles:write."""
        return self._call(
            "DELETE",
            f"/lists/{path_id('list_id', list_id)}/relationships/profiles",
            "remove_profiles_from_list",
            body=profile_refs(data, "remove_profiles_from_list"),
            refusals={404: _LIST_NOT_FOUND})

    # --- Segments ---------------------------------------------------------

    def list_segments(self, filter: Optional[str] = None,
                      sort: Optional[str] = None,
                      page_size: Optional[int] = None,
                      page_cursor: Optional[str] = None, *,
                      all_pages: bool = False, max_pages: int = 10) -> dict:
        """List the segments (page_size <= 10). Scope segments:read."""
        return self._list("/segments", "list_segments", {
            "filter": filter, "sort": sort, "page[size]": page_size,
            "page[cursor]": page_cursor,
        }, all_pages=all_pages, max_pages=max_pages)

    def get_segment(self, segment_id: str,
                    additional_fields: Optional[List[str]] = None) -> dict:
        """One segment by id; `additional_fields=["profile_count"]` adds the
        member count (1/s, 15/min). Scope segments:read."""
        extra = ["segment_profile_count"] if additional_fields else []
        return self._call(
            "GET", f"/segments/{path_id('segment_id', segment_id)}",
            "get_segment",
            params={"additional-fields[segment]": additional_fields},
            refusals={404: _SEGMENT_NOT_FOUND}, extra_buckets=extra)

    def list_profiles_in_segment(self, segment_id: str,
                                 filter: Optional[str] = None,
                                 sort: Optional[str] = None,
                                 page_size: Optional[int] = None,
                                 page_cursor: Optional[str] = None, *,
                                 all_pages: bool = False,
                                 max_pages: int = 10) -> dict:
        """The profiles of one segment (page_size <= 100). Scopes
        segments:read, profiles:read."""
        return self._list(
            f"/segments/{path_id('segment_id', segment_id)}/profiles",
            "list_profiles_in_segment", {
                "filter": filter, "sort": sort, "page[size]": page_size,
                "page[cursor]": page_cursor,
            }, all_pages=all_pages, max_pages=max_pages,
            refusals={404: _SEGMENT_NOT_FOUND})
