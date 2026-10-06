"""GitHub Actions — workflows, runs, jobs, logs, artifacts.

This mixin is never instantiated on its own: it is composed into `GitHubClient`, which
provides the transport (`_request`, `_get`, `_check_choice`).

⚠️ **All Actions lists are OBJECTS, not arrays**:
`{total_count, workflow_runs: [...]}`, `{total_count, jobs: [...]}`,
`{total_count, artifacts: [...]}`. A loop written for a bare array would iterate
over the dict's KEYS without raising. `client.iterate()` knows these envelopes; do
not rewrite pagination by hand here.

⚠️ **Logs and artifacts are downloaded through a REDIRECT**: GitHub answers 302 to
a signed storage URL with a short lifetime (≈ 1 minute), and **this URL must NOT
receive the `Authorization` header** — the storage would reject it. The
download methods here therefore return the raw response and expose
the URL, rather than following the redirect with the session's headers.

⚠️ **Rerunning and cancelling are REAL actions on infrastructure**:
`rerun_workflow_run` re-executes a pipeline (thus consuming billed minutes
and possibly redeploying), `cancel_workflow_run` interrupts work in progress, and
`dispatch_workflow` triggers a workflow — potentially a deployment.

**Deliberately absent**: Actions secrets and variables. Setting them through a
connector would amount to moving credentials from one vault to another, which
oto's architecture refuses on principle.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ..const import RUN_STATUSES


class _ActionsMixin:
    """Workflows, runs, jobs, artifacts."""

    # --- workflows ------------------------------------------------------------

    def list_workflows(self, owner: str, repo: str,
                       per_page: Optional[int] = None,
                       page: Optional[int] = None) -> Any:
        """GET /repos/{owner}/{repo}/actions/workflows — defined workflows.

        ⚠️ Returns `{total_count, workflows: [...]}`, not an array.
        """
        return self._get(f"/repos/{owner}/{repo}/actions/workflows", None,
                         per_page, page)

    def get_workflow(self, owner: str, repo: str, workflow: Any) -> Any:
        """GET /repos/{owner}/{repo}/actions/workflows/{workflow} — one workflow.

        `workflow` accepts the **numeric id OR the file name**
        (`ci.yml`) — the file name is more stable over time.
        """
        return self._request(
            "GET", f"/repos/{owner}/{repo}/actions/workflows/{workflow}")

    def dispatch_workflow(self, owner: str, repo: str, workflow: Any,
                          ref: str, inputs: Optional[Dict[str, Any]] = None) -> Any:
        """POST /…/actions/workflows/{workflow}/dispatches — **TRIGGERS a workflow**.

        ⚠️ Real execution: may build, test, publish or **deploy**.
        `ref` is the branch or tag to run on.

        ⚠️ **The workflow must declare `workflow_dispatch`** in its
        triggers, otherwise GitHub answers 404 — a 404 here means "not
        triggerable", not "does not exist".

        ⚠️ **204 response with no body**: it does NOT return the created run. To
        find it, list the workflow's runs right afterwards (there is a
        short delay before it appears).
        """
        if not ref:
            raise ValueError(
                "`ref` required: the branch or tag to trigger on.")
        body: Dict[str, Any] = {"ref": ref}
        if inputs:
            body["inputs"] = inputs
        return self._request(
            "POST",
            f"/repos/{owner}/{repo}/actions/workflows/{workflow}/dispatches",
            json=body)

    # --- runs -------------------------------------------------------------------

    def list_workflow_runs(self, owner: str, repo: str,
                           workflow: Optional[Any] = None,
                           actor: Optional[str] = None,
                           branch: Optional[str] = None,
                           event: Optional[str] = None,
                           status: Optional[str] = None,
                           created: Optional[str] = None,
                           head_sha: Optional[str] = None,
                           per_page: Optional[int] = None,
                           page: Optional[int] = None) -> Any:
        """GET /…/actions/runs — runs of the repository (or of a given workflow).

        With `workflow`, queries `/actions/workflows/{workflow}/runs`.
        `created` accepts GitHub's range syntax (`>=2026-01-01`).

        ⚠️ Returns `{total_count, workflow_runs: [...]}`, not an array.
        ⚠️ `status` mixes progress states (`queued`, `in_progress`,
        `completed`) and CONCLUSIONS (`success`, `failure`, `cancelled`…) in
        a single parameter — it is indeed the API that is like this.
        """
        self._check_choice("status", status, RUN_STATUSES)
        path = (f"/repos/{owner}/{repo}/actions/workflows/{workflow}/runs"
                if workflow is not None
                else f"/repos/{owner}/{repo}/actions/runs")
        return self._get(path, {
            "actor": actor, "branch": branch, "event": event,
            "status": status, "created": created, "head_sha": head_sha},
            per_page, page)

    def get_workflow_run(self, owner: str, repo: str, run_id: Any) -> Any:
        """GET /repos/{owner}/{repo}/actions/runs/{run_id} — one run.

        ⚠️ Read `status` AND `conclusion`: a `completed` run may have
        failed. `conclusion` is `null` while the work is not finished.
        """
        return self._request("GET",
                             f"/repos/{owner}/{repo}/actions/runs/{run_id}")

    def cancel_workflow_run(self, owner: str, repo: str, run_id: Any) -> Any:
        """POST /…/actions/runs/{run_id}/cancel — **CANCELS a run in progress**.

        ⚠️ Interrupts real work. **202** response: the cancellation is
        requested, not yet effective.
        """
        return self._request(
            "POST", f"/repos/{owner}/{repo}/actions/runs/{run_id}/cancel")

    def rerun_workflow_run(self, owner: str, repo: str, run_id: Any,
                           enable_debug_logging: Optional[bool] = None) -> Any:
        """POST /…/actions/runs/{run_id}/rerun — **RERUNS the whole run**.

        ⚠️ Real execution: consumes billed minutes, and may redeploy
        if the workflow deploys. To replay only what failed,
        `rerun_failed_jobs` — cheaper and less risky.
        """
        body = ({"enable_debug_logging": enable_debug_logging}
                if enable_debug_logging is not None else None)
        return self._request(
            "POST", f"/repos/{owner}/{repo}/actions/runs/{run_id}/rerun",
            json=body)

    def rerun_failed_jobs(self, owner: str, repo: str, run_id: Any,
                          enable_debug_logging: Optional[bool] = None) -> Any:
        """POST /…/actions/runs/{run_id}/rerun-failed-jobs — rerun the FAILED jobs.

        Successful jobs are not replayed: it is the cheaper and
        less risky of the two reruns.
        """
        body = ({"enable_debug_logging": enable_debug_logging}
                if enable_debug_logging is not None else None)
        return self._request(
            "POST",
            f"/repos/{owner}/{repo}/actions/runs/{run_id}/rerun-failed-jobs",
            json=body)

    def delete_workflow_run(self, owner: str, repo: str, run_id: Any) -> Any:
        """DELETE /repos/{owner}/{repo}/actions/runs/{run_id} — delete a run.

        ⚠️ Permanent: takes its logs and history with it. A run in progress
        cannot be deleted (cancel it first).
        """
        return self._request(
            "DELETE", f"/repos/{owner}/{repo}/actions/runs/{run_id}")

    # --- jobs ---------------------------------------------------------------------

    def list_run_jobs(self, owner: str, repo: str, run_id: Any,
                      filter: Optional[str] = None,
                      per_page: Optional[int] = None,
                      page: Optional[int] = None) -> Any:
        """GET /…/actions/runs/{run_id}/jobs — jobs of a run.

        `filter="all"` includes previous attempts (default: `latest`).
        ⚠️ Returns `{total_count, jobs: [...]}`, not an array.
        """
        return self._get(f"/repos/{owner}/{repo}/actions/runs/{run_id}/jobs",
                         {"filter": filter}, per_page, page)

    def get_job(self, owner: str, repo: str, job_id: Any) -> Any:
        """GET /repos/{owner}/{repo}/actions/jobs/{job_id} — a job and its steps."""
        return self._request("GET",
                             f"/repos/{owner}/{repo}/actions/jobs/{job_id}")

    def get_job_logs_url(self, owner: str, repo: str, job_id: Any) -> Optional[str]:
        """GET /…/actions/jobs/{job_id}/logs — the SIGNED URL of a job's logs.

        ⚠️ GitHub answers **302** to a storage URL with a short lifetime
        (≈ 1 minute). This method does NOT follow the redirect and returns the URL:
        replaying it with the session's `Authorization` header would make the
        storage fail, as it refuses a doubly authenticated request. Download
        the returned URL **without an auth header**, right away.

        Returns `None` if GitHub did not redirect (logs expired or absent — they
        are kept for a limited time).
        """
        resp = self._request(
            "GET", f"/repos/{owner}/{repo}/actions/jobs/{job_id}/logs",
            raw=True)
        return (getattr(resp, "headers", None) or {}).get("Location")

    # --- artifacts -----------------------------------------------------------------

    def list_artifacts(self, owner: str, repo: str,
                       run_id: Optional[Any] = None,
                       name: Optional[str] = None,
                       per_page: Optional[int] = None,
                       page: Optional[int] = None) -> Any:
        """GET /…/actions/artifacts — artifacts of the repository (or of a run).

        With `run_id`, queries `/actions/runs/{run_id}/artifacts`.
        ⚠️ Returns `{total_count, artifacts: [...]}`, not an array.
        ⚠️ An artifact **expires** (90 days by default): `expired: true` flags
        an entry whose content can no longer be downloaded.
        """
        path = (f"/repos/{owner}/{repo}/actions/runs/{run_id}/artifacts"
                if run_id is not None
                else f"/repos/{owner}/{repo}/actions/artifacts")
        return self._get(path, {"name": name}, per_page, page)

    def get_artifact(self, owner: str, repo: str, artifact_id: Any) -> Any:
        """GET /repos/{owner}/{repo}/actions/artifacts/{id} — one artifact."""
        return self._request(
            "GET", f"/repos/{owner}/{repo}/actions/artifacts/{artifact_id}")

    def get_artifact_download_url(self, owner: str, repo: str,
                                  artifact_id: Any,
                                  archive_format: str = "zip") -> Optional[str]:
        """GET /…/artifacts/{id}/{format} — the SIGNED download URL.

        Same rule as logs: **302** to an ephemeral storage URL, to
        be downloaded **without an `Authorization` header**. Returns `None` if GitHub did
        not redirect (expired artifact).
        """
        resp = self._request(
            "GET",
            f"/repos/{owner}/{repo}/actions/artifacts/{artifact_id}/{archive_format}",
            raw=True)
        return (getattr(resp, "headers", None) or {}).get("Location")

    def delete_artifact(self, owner: str, repo: str, artifact_id: Any) -> Any:
        """DELETE /repos/{owner}/{repo}/actions/artifacts/{id} — delete an artifact."""
        return self._request(
            "DELETE", f"/repos/{owner}/{repo}/actions/artifacts/{artifact_id}")
