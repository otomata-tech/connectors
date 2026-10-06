"""GitHub repositories — metadata, branches, commits, file contents, releases.

This mixin is never instantiated on its own: it is composed into `GitHubClient`, which
provides the transport (`_request`, `_get`, `_check_choice`, `_check_per_page`).

⚠️ **A file's content arrives in base64**, not in clear: `get_content`
returns `{type, encoding: "base64", content, sha, …}`. `read_text_file` does the
decoding, and explicitly refuses the cases where there is nothing to decode (a
directory, a file too big served without content, a binary).

⚠️ **Writing an existing file REQUIRES its `sha`** (the blob's, returned by
`get_content`). Without it, GitHub answers 409 — it is its concurrency control:
it guarantees we overwrite the version we read, and not a change
that arrived in the meantime.

**Deliberately absent**: `DELETE /repos/{owner}/{repo}`. Deleting a repository is
irreversible and outside this connector's scope.
"""
from __future__ import annotations

import base64
from typing import Any, Dict, Optional

from ..const import (DEFAULT_ACCEPT, ORG_REPO_TYPES, REPO_SORTS, REPO_TYPES,
                     SORT_DIRECTIONS)


class _ReposMixin:
    """Repositories, branches, commits, contents, releases."""

    # --- repositories -------------------------------------------------------

    def list_my_repos(self, visibility: Optional[str] = None,
                      affiliation: Optional[str] = None,
                      type: Optional[str] = None, sort: Optional[str] = None,
                      direction: Optional[str] = None,
                      per_page: Optional[int] = None,
                      page: Optional[int] = None) -> Any:
        """GET /user/repos — repositories of the token holder.

        ⚠️ `type` and `visibility`/`affiliation` are **mutually exclusive** on GitHub's side:
        passing both returns a 422.
        """
        self._check_choice("type", type, REPO_TYPES)
        self._check_choice("sort", sort, REPO_SORTS)
        self._check_choice("direction", direction, SORT_DIRECTIONS)
        if type and (visibility or affiliation):
            raise ValueError(
                "`type` is mutually exclusive with `visibility`/`affiliation` (422 on "
                "GitHub's side) — choose one or the other.")
        return self._get("/user/repos", {
            "visibility": visibility, "affiliation": affiliation, "type": type,
            "sort": sort, "direction": direction}, per_page, page)

    def list_org_repos(self, org: str, type: Optional[str] = None,
                       sort: Optional[str] = None,
                       direction: Optional[str] = None,
                       per_page: Optional[int] = None,
                       page: Optional[int] = None) -> Any:
        """GET /orgs/{org}/repos — repositories of an organization.

        ⚠️ A token without access to private repositories will simply not see them
        listed: the list is silently shorter, it does not fail.
        """
        self._check_choice("type", type, ORG_REPO_TYPES)
        self._check_choice("sort", sort, REPO_SORTS)
        self._check_choice("direction", direction, SORT_DIRECTIONS)
        return self._get(f"/orgs/{org}/repos", {
            "type": type, "sort": sort, "direction": direction},
            per_page, page)

    def list_user_repos(self, username: str, type: Optional[str] = None,
                        sort: Optional[str] = None,
                        direction: Optional[str] = None,
                        per_page: Optional[int] = None,
                        page: Optional[int] = None) -> Any:
        """GET /users/{username}/repos — PUBLIC repositories of an account."""
        self._check_choice("sort", sort, REPO_SORTS)
        self._check_choice("direction", direction, SORT_DIRECTIONS)
        return self._get(f"/users/{username}/repos", {
            "type": type, "sort": sort, "direction": direction},
            per_page, page)

    def get_repo(self, owner: str, repo: str) -> Any:
        """GET /repos/{owner}/{repo} — a repository's profile.

        ⚠️ **404 on a private repository = token without the right**, most often, and
        not "does not exist": GitHub hides existence on purpose.
        """
        return self._request("GET", f"/repos/{owner}/{repo}")

    def list_languages(self, owner: str, repo: str) -> Any:
        """GET /repos/{owner}/{repo}/languages — bytes per language.

        ⚠️ Returns an OBJECT `{language: bytes}`, not a list: do not pass it to
        `iterate`.
        """
        return self._request("GET", f"/repos/{owner}/{repo}/languages")

    def list_contributors(self, owner: str, repo: str,
                          per_page: Optional[int] = None,
                          page: Optional[int] = None) -> Any:
        """GET /repos/{owner}/{repo}/contributors — contributors and their volume."""
        return self._get(f"/repos/{owner}/{repo}/contributors", None,
                         per_page, page)

    def list_topics(self, owner: str, repo: str) -> Any:
        """GET /repos/{owner}/{repo}/topics — the repository's "topics"."""
        return self._request("GET", f"/repos/{owner}/{repo}/topics")

    # --- branches and commits -------------------------------------------------

    def list_branches(self, owner: str, repo: str,
                      protected: Optional[bool] = None,
                      per_page: Optional[int] = None,
                      page: Optional[int] = None) -> Any:
        """GET /repos/{owner}/{repo}/branches — the repository's branches."""
        return self._get(f"/repos/{owner}/{repo}/branches",
                         {"protected": protected}, per_page, page)

    def get_branch(self, owner: str, repo: str, branch: str) -> Any:
        """GET /repos/{owner}/{repo}/branches/{branch} — a branch and its HEAD."""
        return self._request("GET", f"/repos/{owner}/{repo}/branches/{branch}")

    def list_commits(self, owner: str, repo: str, sha: Optional[str] = None,
                     path: Optional[str] = None, author: Optional[str] = None,
                     since: Optional[str] = None, until: Optional[str] = None,
                     per_page: Optional[int] = None,
                     page: Optional[int] = None) -> Any:
        """GET /repos/{owner}/{repo}/commits — history.

        `sha` is the starting point (branch, tag or SHA), `path` restricts to
        commits that touch a path. `since`/`until` are ISO 8601 dates.
        """
        return self._get(f"/repos/{owner}/{repo}/commits", {
            "sha": sha, "path": path, "author": author,
            "since": since, "until": until}, per_page, page)

    def get_commit(self, owner: str, repo: str, ref: str) -> Any:
        """GET /repos/{owner}/{repo}/commits/{ref} — a commit AND its diff.

        ⚠️ The response embeds `files[]`: on a big commit, that is heavy. GitHub
        also caps at 300 files, and truncates beyond that **without saying so
        in the files themselves** — compare `files` to `stats` to see it.
        """
        return self._request("GET", f"/repos/{owner}/{repo}/commits/{ref}")

    def compare_commits(self, owner: str, repo: str, base: str, head: str,
                        per_page: Optional[int] = None,
                        page: Optional[int] = None) -> Any:
        """GET /repos/{owner}/{repo}/compare/{base}...{head} — the gap between two refs.

        `base` and `head` accept branches, tags and SHAs. To compare across
        two forks, prefix with an owner (`other:branch`).
        """
        return self._get(f"/repos/{owner}/{repo}/compare/{base}...{head}",
                         None, per_page, page)

    def list_tags(self, owner: str, repo: str, per_page: Optional[int] = None,
                  page: Optional[int] = None) -> Any:
        """GET /repos/{owner}/{repo}/tags — the repository's tags."""
        return self._get(f"/repos/{owner}/{repo}/tags", None, per_page, page)

    # --- contents ------------------------------------------------------------

    def get_content(self, owner: str, repo: str, path: str,
                    ref: Optional[str] = None) -> Any:
        """GET /repos/{owner}/{repo}/contents/{path} — file OR directory.

        Returns an OBJECT for a file (with `content` in **base64** and the blob's
        `sha`), a LIST for a directory. `ref` chooses branch, tag or SHA.

        ⚠️ Beyond 1 MB, GitHub returns the metadata **without `content`**; beyond
        100 MB, it refuses. `read_text_file` names these cases instead of returning
        an empty string.
        """
        return self._request("GET", f"/repos/{owner}/{repo}/contents/{path}",
                             params={"ref": ref})

    def read_text_file(self, owner: str, repo: str, path: str,
                       ref: Optional[str] = None,
                       encoding: str = "utf-8") -> str:
        """A file's decoded TEXT CONTENT — convenience on top of `get_content`.

        Written here so that base64 decoding exists only once, and above all
        so that the three cases where there is nothing to decode are **named**:
        a directory, a file too big served without content, a binary not
        decodable in the requested encoding. Each would otherwise return an empty string
        or an opaque exception far from the call site.
        """
        payload = self.get_content(owner, repo, path, ref)
        if isinstance(payload, list):
            raise ValueError(
                f"`{path}` is a DIRECTORY ({len(payload)} entries), not a "
                "file — use `get_content` to list it.")
        if not isinstance(payload, dict):
            raise ValueError(
                f"unexpected response for `{path}`: {type(payload).__name__}")
        content = payload.get("content")
        if not content:
            taille = payload.get("size")
            raise ValueError(
                f"`{path}` is served WITHOUT content (size {taille} bytes). "
                "GitHub omits `content` beyond 1 MB — go through the Git blobs "
                "API, or read the file from a repository archive.")
        if payload.get("encoding") != "base64":
            raise ValueError(
                f"unexpected encoding for `{path}`: "
                f"{payload.get('encoding')!r} (expected base64).")
        brut = base64.b64decode(content)
        try:
            return brut.decode(encoding)
        except UnicodeDecodeError as exc:
            raise ValueError(
                f"`{path}` is not {encoding} text ({exc}) — it is "
                "probably a binary.") from exc

    def create_or_update_file(self, owner: str, repo: str, path: str,
                              message: str, content: Any,
                              sha: Optional[str] = None,
                              branch: Optional[str] = None,
                              committer: Optional[Dict[str, Any]] = None,
                              author: Optional[Dict[str, Any]] = None) -> Any:
        """PUT /repos/{owner}/{repo}/contents/{path} — write a file (commit).

        `content` accepts text, bytes, or already-encoded base64; it is
        encoded here if needed. `message` is the commit message.

        ⚠️ **`sha` is REQUIRED to overwrite an existing file** — it is the
        blob's, returned by `get_content`. Without it, GitHub answers **409**: it is its
        concurrency control, which guarantees we replace the version read
        and not a change that arrived in the meantime. Omitting it on a creation
        is normal.
        """
        if isinstance(content, bytes):
            encoded = base64.b64encode(content).decode("ascii")
        else:
            encoded = base64.b64encode(str(content).encode("utf-8")).decode("ascii")
        body: Dict[str, Any] = {"message": message, "content": encoded}
        for key, value in (("sha", sha), ("branch", branch),
                           ("committer", committer), ("author", author)):
            if value is not None:
                body[key] = value
        return self._request("PUT", f"/repos/{owner}/{repo}/contents/{path}",
                             json=body)

    def delete_file(self, owner: str, repo: str, path: str, message: str,
                    sha: str, branch: Optional[str] = None) -> Any:
        """DELETE /repos/{owner}/{repo}/contents/{path} — delete a file (commit).

        `sha` (the blob's) is **mandatory** here, without exception: there is
        no "blind" deletion.
        """
        if not sha:
            raise ValueError(
                "blob `sha` required to delete a file (read it with "
                "`get_content`) — GitHub refuses a deletion without it.")
        body: Dict[str, Any] = {"message": message, "sha": sha}
        if branch is not None:
            body["branch"] = branch
        return self._request("DELETE", f"/repos/{owner}/{repo}/contents/{path}",
                             json=body)

    def get_readme(self, owner: str, repo: str,
                   ref: Optional[str] = None) -> Any:
        """GET /repos/{owner}/{repo}/readme — the README, whatever its name.

        Same shape as `get_content` (content in base64).
        """
        return self._request("GET", f"/repos/{owner}/{repo}/readme",
                             params={"ref": ref})

    # --- releases ------------------------------------------------------------

    def list_releases(self, owner: str, repo: str,
                      per_page: Optional[int] = None,
                      page: Optional[int] = None) -> Any:
        """GET /repos/{owner}/{repo}/releases — the repository's releases."""
        return self._get(f"/repos/{owner}/{repo}/releases", None, per_page, page)

    def get_release(self, owner: str, repo: str, release_id: Any) -> Any:
        """GET /repos/{owner}/{repo}/releases/{id} — one release."""
        return self._request("GET",
                             f"/repos/{owner}/{repo}/releases/{release_id}")

    def get_latest_release(self, owner: str, repo: str) -> Any:
        """GET /repos/{owner}/{repo}/releases/latest — the latest published release.

        ⚠️ Ignores drafts AND prereleases: "latest" is not the
        last tag created.
        """
        return self._request("GET", f"/repos/{owner}/{repo}/releases/latest")

    def create_release(self, owner: str, repo: str,
                       payload: Dict[str, Any]) -> Any:
        """POST /repos/{owner}/{repo}/releases — create a release.

        Required: `tag_name`. Optional: `name`, `body`, `draft`, `prerelease`,
        `target_commitish`, `generate_release_notes`.

        ⚠️ A **published** release (`draft` absent or false) is visible
        immediately, and notifies the people subscribed to the repository. `draft=True`
        to prepare without publishing.
        """
        if not payload.get("tag_name"):
            raise ValueError("`tag_name` required to create a release.")
        return self._request("POST", f"/repos/{owner}/{repo}/releases",
                             json=dict(payload))

    def update_release(self, owner: str, repo: str, release_id: Any,
                       payload: Dict[str, Any]) -> Any:
        """PATCH /repos/{owner}/{repo}/releases/{id} — update a release."""
        return self._request("PATCH",
                             f"/repos/{owner}/{repo}/releases/{release_id}",
                             json=dict(payload))

    def download_tarball(self, owner: str, repo: str, ref: str) -> Any:
        """GET /repos/{owner}/{repo}/tarball/{ref} — repository archive at a ref.

        Returns the **raw response** (followed redirect not included): the body is
        a binary, not JSON. Useful to read files too big for
        `get_content`.
        """
        return self._request("GET", f"/repos/{owner}/{repo}/tarball/{ref}",
                             accept=DEFAULT_ACCEPT, raw=True)
