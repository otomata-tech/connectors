"""Microsoft Graph client — SharePoint sites, document libraries and OneDrive files,
on behalf of a signed-in person (delegated access token, see `auth`).

One method per Graph endpoint. Covers what an agent needs to find and read a
document and to drop one back: sites (search, by id, by path), the drives
(document libraries) of a site, a user's OneDrive, and the items of a drive (list
a folder, get, search, download, upload, create a folder). Mail, calendar and
Teams are other Graph surfaces, not covered here. The token is obtained and
refreshed by the consumer (`auth.exchange_code` / `auth.refresh`); this client only
spends it.

## Protocol facts that shape a caller

- **The person's own rights decide everything.** A delegated token sees exactly
  what its owner sees in Microsoft 365: their OneDrive, the sites and files shared
  with them. A 403 means "this person has no access", never "does not exist".
- **A document library is a drive.** Every item call is addressed by `drive_id`;
  a site's libraries come from `list_site_drives`, the person's own OneDrive from
  `get_my_drive`, a colleague's from `get_user_drive` (if shared with them).
- **An item is addressed by id OR by path** relative to the drive root
  (`"Contrats/2026/nda.docx"`). The two are exclusive.
- **`download` follows a redirect** to a short-lived pre-authenticated URL;
  `requests` drops the `Authorization` header on that cross-host hop, as it
  should. `format="pdf"` asks Graph to convert an Office document
  (docx, pptx, xlsx…) before sending it.
- **Simple upload** (`PUT …:/content`) is limited to 250 MB by Graph; larger
  files need an upload session, not covered here.
- Collections paginate with `@odata.nextLink`; `limit` bounds how many items
  are followed across pages.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import quote

from ..common.credentials import require
from ._transport import GraphBase

CONFLICT_BEHAVIORS = ("fail", "replace", "rename")


def _encode_path(path: str) -> str:
    """`Contrats/2026/nda v2.docx` → segments percent-encoded, slashes kept."""
    cleaned = (path or "").strip().strip("/")
    if not cleaned:
        raise ValueError("path is empty")
    return quote(cleaned, safe="/")


def _odata_string(value: str) -> str:
    """A literal for an OData function argument: single quotes doubled."""
    return value.replace("'", "''")


class FilesClient(GraphBase):
    """Microsoft Graph v1.0 on behalf of one person, scoped to files (SharePoint,
    OneDrive)."""

    @staticmethod
    def _item_path(drive_id: str, item_id: Optional[str], path: Optional[str]) -> str:
        """`/drives/{d}/items/{id}`, `/drives/{d}/root:/{path}:` or `/drives/{d}/root`."""
        if item_id and path:
            raise ValueError("item_id and path are mutually exclusive: use only one")
        base = f"/drives/{require(drive_id, 'drive_id')}"
        if item_id:
            return f"{base}/items/{item_id}"
        if path:
            return f"{base}/root:/{_encode_path(path)}:"
        return f"{base}/root"

    # ================================================================
    # Sites
    # ================================================================

    def search_sites(self, query: str, *, limit: int = 50) -> List[Dict[str, Any]]:
        """GET /sites?search= — sites whose name or description match `query`,
        among those the signed-in person can open."""
        return self._paged("/sites", {"search": query}, limit=limit)

    def get_site(self, site_id: str) -> Dict[str, Any]:
        """GET /sites/{id} — `site_id` is the composite id
        `{hostname},{site-collection-guid},{web-guid}` returned by search."""
        return self._json("GET", f"/sites/{site_id}")

    def get_site_by_path(self, hostname: str, site_path: str) -> Dict[str, Any]:
        """GET /sites/{hostname}:/{path} — e.g. `contoso.sharepoint.com` +
        `sites/Marketing`, as read in the site's URL."""
        return self._json("GET", f"/sites/{hostname}:/{_encode_path(site_path)}")

    def list_site_drives(self, site_id: str, *, limit: int = 100) -> List[Dict[str, Any]]:
        """GET /sites/{id}/drives — the site's document libraries."""
        return self._paged(f"/sites/{site_id}/drives", limit=limit)

    # ================================================================
    # Drives
    # ================================================================

    def get_my_drive(self) -> Dict[str, Any]:
        """GET /me/drive — the signed-in person's OneDrive."""
        return self._json("GET", "/me/drive")

    def get_user_drive(self, user: str) -> Dict[str, Any]:
        """GET /users/{id or userPrincipalName}/drive — that colleague's OneDrive,
        if they shared it with the signed-in person."""
        return self._json("GET", f"/users/{quote(user, safe='@')}/drive")

    def get_drive(self, drive_id: str) -> Dict[str, Any]:
        """GET /drives/{id}."""
        return self._json("GET", f"/drives/{drive_id}")

    # ================================================================
    # Items
    # ================================================================

    def get_item(self, drive_id: str, *, item_id: Optional[str] = None,
                 path: Optional[str] = None) -> Dict[str, Any]:
        """GET a driveItem by id or by path (the drive root when neither)."""
        return self._json("GET", self._item_path(drive_id, item_id, path))

    def list_children(self, drive_id: str, *, item_id: Optional[str] = None,
                      path: Optional[str] = None, limit: int = 200) -> List[Dict[str, Any]]:
        """GET …/children — the content of a folder (the drive root when neither
        `item_id` nor `path`)."""
        return self._paged(f"{self._item_path(drive_id, item_id, path)}/children",
                           limit=limit)

    def search_items(self, drive_id: str, query: str, *, limit: int = 50
                     ) -> List[Dict[str, Any]]:
        """GET /drives/{d}/root/search(q='…') — name, metadata and content of the
        files of the whole drive (SharePoint's index, not a live scan: a file
        uploaded seconds ago may not be found yet)."""
        return self._paged(
            f"/drives/{drive_id}/root/search(q='{quote(_odata_string(query), safe='')}')",
            limit=limit)

    def download(self, drive_id: str, *, item_id: Optional[str] = None,
                 path: Optional[str] = None, format: Optional[str] = None) -> bytes:
        """GET …/content — the file's bytes. `format="pdf"` converts an Office
        document server-side (refused by Graph for a type it cannot convert)."""
        params = {"format": format} if format else None
        resp = self._request("GET", f"{self._item_path(drive_id, item_id, path)}/content",
                             params=params, headers={"Accept": "*/*"})
        return resp.content

    def upload(self, drive_id: str, filename: str, content: bytes, *,
               parent_id: Optional[str] = None, parent_path: Optional[str] = None,
               conflict: str = "fail", content_type: str = "application/octet-stream"
               ) -> Dict[str, Any]:
        """PUT …/{parent}:/{filename}:/content — simple upload (≤ 250 MB).

        Args:
            parent_id / parent_path: the destination folder (the drive root when
                neither).
            conflict: `fail` (default — never overwrite silently), `replace`
                or `rename`.
        """
        if conflict not in CONFLICT_BEHAVIORS:
            raise ValueError(f"conflict must be one of {', '.join(CONFLICT_BEHAVIORS)}")
        name = (filename or "").strip()
        if not name or "/" in name:
            raise ValueError("filename is a file name, without '/'")
        parent = self._item_path(drive_id, parent_id, parent_path)
        # `root:/a/b:` → `root:/a/b/name:` ; `root` ou `items/{id}` → `…:/name:`
        target = (f"{parent[:-1]}/{quote(name)}:" if parent.endswith(":")
                  else f"{parent}:/{quote(name)}:")
        return self._json("PUT", f"{target}/content", data=content,
                          params={"@microsoft.graph.conflictBehavior": conflict},
                          headers={"Content-Type": content_type})

    def create_folder(self, drive_id: str, name: str, *, parent_id: Optional[str] = None,
                      parent_path: Optional[str] = None, conflict: str = "fail"
                      ) -> Dict[str, Any]:
        """POST …/children — a new folder `name` in the parent (root when neither)."""
        if conflict not in CONFLICT_BEHAVIORS:
            raise ValueError(f"conflict must be one of {', '.join(CONFLICT_BEHAVIORS)}")
        parent = self._item_path(drive_id, parent_id, parent_path)
        return self._json("POST", f"{parent}/children", json={
            "name": require(name, "name"), "folder": {},
            "@microsoft.graph.conflictBehavior": conflict})
