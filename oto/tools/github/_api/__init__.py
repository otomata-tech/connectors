"""GitHub call families, composed into `GitHubClient`.

One module per API domain. Contract details: `../client.py`.
"""

from .actions import _ActionsMixin
from .issues import _IssuesMixin
from .orgs import _OrgsMixin
from .pulls import _PullsMixin
from .repos import _ReposMixin
from .search import _SearchMixin

__all__ = [
    "_ActionsMixin",
    "_IssuesMixin",
    "_OrgsMixin",
    "_PullsMixin",
    "_ReposMixin",
    "_SearchMixin",
]
