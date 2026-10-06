"""GitHub connector constants — headers, bounds, enumerations.

Single home: `client.py` re-exports them via its `__all__`, and the mixins in
`_api/` import them from here. The backend pins oto-core by tag and only imports
`oto.tools.github.client`.
"""
from __future__ import annotations

# (connect, read) — no unbounded wait. The read timeout is generous: a
# `compare_commits` on a big repository, or a job log download,
# takes its time.
HTTP_TIMEOUT = (10, 90)

DEFAULT_BASE_URL = "https://api.github.com"

#: Media type expected by almost all REST endpoints.
DEFAULT_ACCEPT = "application/vnd.github+json"

#: API version sent on EVERY request. Pinned here, never copied to a
#: call site: GitHub dates its versions and will retire old ones, and there must
#: be ONE place to change.
DEFAULT_API_VERSION = "2022-11-28"

# ⚠️ `per_page` CAPS AT 100, and GitHub **silently trims** anything above: no
# error, just fewer rows than requested. This is trap no. 1 of this API —
# a caller asking for 500 believes it has everything and only has the first 100. Hence
# a LOCAL refusal, which names the limit instead of suffering it.
MIN_PER_PAGE, MAX_PER_PAGE = 1, 100
DEFAULT_PER_PAGE = 30

#: Search has its own, lower cap, and a total bounded to 1,000
#: results regardless of pagination.
SEARCH_MAX_RESULTS = 1000

# Retried statuses. GitHub has TWO limits: the primary one (`x-ratelimit-*`
# headers, 403 or 429) and an anti-abuse "secondary" limit, which
# also answers 403/429 and often carries `Retry-After`. Both are retried.
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 3

# --- enumerations -----------------------------------------------------------

ISSUE_STATES = ("open", "closed", "all")
ISSUE_STATE_WRITES = ("open", "closed")
SORT_DIRECTIONS = ("asc", "desc")

ISSUE_SORTS = ("created", "updated", "comments")
PULL_STATES = ("open", "closed", "all")
PULL_SORTS = ("created", "updated", "popularity", "long-running")

#: ⚠️ `merge` creates a merge commit, `squash` squashes the branch's history
#: into a single commit, `rebase` rewrites the commits. All three change
#: the target branch differently, and none can be undone with one click.
MERGE_METHODS = ("merge", "squash", "rebase")

REVIEW_EVENTS = ("APPROVE", "REQUEST_CHANGES", "COMMENT")

REPO_TYPES = ("all", "owner", "public", "private", "member")
REPO_SORTS = ("created", "updated", "pushed", "full_name")
ORG_REPO_TYPES = ("all", "public", "private", "forks", "sources", "member")

RUN_STATUSES = (
    "completed", "action_required", "cancelled", "failure", "neutral",
    "skipped", "stale", "success", "timed_out", "in_progress", "queued",
    "requested", "waiting", "pending",
)

MEMBERSHIP_ROLES = ("admin", "member")
MEMBER_FILTERS = ("2fa_disabled", "all")
TEAM_ROLES = ("member", "maintainer")

#: Permissions that can be set on a repository collaborator.
COLLABORATOR_PERMISSIONS = ("pull", "triage", "push", "maintain", "admin")

SEARCH_CODE_SORTS = ("indexed",)
SEARCH_REPO_SORTS = ("stars", "forks", "help-wanted-issues", "updated")
SEARCH_ISSUE_SORTS = (
    "comments", "reactions", "author-date", "committer-date", "updated",
    "created",
)

__all__ = [
    "HTTP_TIMEOUT", "DEFAULT_BASE_URL", "DEFAULT_ACCEPT", "DEFAULT_API_VERSION",
    "MIN_PER_PAGE", "MAX_PER_PAGE", "DEFAULT_PER_PAGE", "SEARCH_MAX_RESULTS",
    "RETRY_STATUSES", "MAX_ATTEMPTS",
    "ISSUE_STATES", "ISSUE_STATE_WRITES", "SORT_DIRECTIONS", "ISSUE_SORTS",
    "PULL_STATES", "PULL_SORTS", "MERGE_METHODS", "REVIEW_EVENTS",
    "REPO_TYPES", "REPO_SORTS", "ORG_REPO_TYPES", "RUN_STATUSES",
    "MEMBERSHIP_ROLES", "MEMBER_FILTERS", "TEAM_ROLES",
    "COLLABORATOR_PERMISSIONS",
    "SEARCH_CODE_SORTS", "SEARCH_REPO_SORTS", "SEARCH_ISSUE_SORTS",
]
