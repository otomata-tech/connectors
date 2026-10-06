"""Leexi call families, composed into `LeexiClient`.

One module per API domain. Contract details: `../client.py`.
"""

from .calls import _CallsMixin
from .meetings import _MeetingsMixin
from .notes import _NotesMixin
from .teams import _TeamsMixin
from .users import _UsersMixin

__all__ = [
    "_CallsMixin",
    "_MeetingsMixin",
    "_NotesMixin",
    "_TeamsMixin",
    "_UsersMixin",
]
