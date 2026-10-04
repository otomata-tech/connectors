"""Notion API families, composed into `NotionClient`.

One module per API area. Transport (`_request`) and id resolution
(`resolve_data_source`) live in `../notion_client.py`.
"""

from .collab import _CollabMixin
from .content import _ContentMixin
from .pages import _PagesMixin
from .structure import _StructureMixin

__all__ = ["_CollabMixin", "_ContentMixin", "_PagesMixin", "_StructureMixin"]
