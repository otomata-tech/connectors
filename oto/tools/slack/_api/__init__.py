"""Slack call families, composed into `SlackClient`.

One module per API domain. Transport and token routing: `../client.py`.
"""

from .usergroups import _UsergroupsMixin

__all__ = ["_UsergroupsMixin"]
