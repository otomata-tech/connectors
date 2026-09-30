"""Claap API client — meeting recordings, transcripts, recording views.

API v1 (`https://api.claap.io/v1`, docs https://docs.claap.io), authenticated by
the **`X-Claap-Key: cla_…`** header. One method = one endpoint; responses are
returned as-is (including the `{"result": …}` envelope), the client invents no
semantics. Paths, verbs, parameters and shapes follow the vendor's published
OpenAPI (`https://docs.claap.io/api-reference/openapi.json`).

Scope: the **recordings** domain only (list, detail, transcript, create, delete)
and reading **recording views** — without them, the `view_id` filter of
`list_recordings` would be unusable. Deals, companies, contacts, folders, AI
fields, admin automations and users of the Claap API are not covered here.

What the caller needs to know, and cannot guess:

- ⚠️ **The key is a WORKSPACE key, not a user's.** It sees every recording that
  members can access AND that is visible in global search (or filed in a folder
  that is) — so colleagues' meetings the key holder never attended. For "my
  recordings", filter by `recorder_email`. Conversely, a **404** on
  `get_recording` can mean "private / not searchable", not "does not exist". A
  key acts as an admin and only sees PRIVATE recordings if it was created with
  "Full access".

- **`labels` and `sources` are sent as a comma-separated string**, not as a
  repeated parameter. The client takes a list and joins it once; a label that
  itself contains a comma is refused (it would be silently split in two). And
  upstream only accepts `labels` **together with `channel_id`**: refused here
  before the round-trip.

- ⚠️ **A `format="text"` transcript comes back as `text/plain`**, not JSON:
  `get_recording_transcript` then returns a `str`. The JSON form carries every
  word (`words[]`) and is very heavy.

- **`get_recording` has two shapes depending on `state`.** `Ready`: full payload
  (transcripts, summary, action items, AI fields…). `Empty`/`Uploaded`/`Failed`:
  thin shape (id, title, author, `upload`). Signed URLs (`video.url`,
  `transcripts[].url`/`textUrl`) expire after 24 h — never store them.

- **Creation is asynchronous**: `Empty` → `Uploaded` → `Ready` | `Failed`. Two
  modes are exposed: media fetched by Claap from a URL (`video_url`, < 2 GiB
  within 5 min) or a media-less recording created from its transcript alone.
  Raw byte upload (`video.type="upload"`) is not exposed. The deprecated
  `downloadUrl` field is never sent.

- ⚠️ **The transcript supplied on creation does NOT have the shape of the one
  you read**: segments `{start, end, speakerId, text}` when writing, versus
  `{startedAt, endedAt, speaker, text}` when reading. `_check_transcript`
  refuses the read shape and says so. It is sent by a PUT to the
  `upload.metaUrl` returned by the creation: if that PUT fails, the recording
  already exists (empty) and the error carries its id.

- **Creation consumes the workspace plan's recording quota** (403 "Recording
  quota exceeded" once reached; nothing is created).

- **`delete_recording` is permanent** (no trash on the API side).

Upstream limits: 3 requests/s per endpoint (with bursts), **3,000/day per
workspace**, shared by all the workspace's keys. 429 and 5xx are retried **for
reads only**: the API has no idempotency key, replaying a POST would create a
duplicate.

⚠️ Creation is not live-verified: the PUT to `metaUrl` (presigned URL, sent here
without the Claap key, as `application/json`) remains unproven.

Requires: requests
"""
from __future__ import annotations

import ipaddress
import time
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote, urlsplit

import requests

from ..common import raise_for_upstream
from ..common.credentials import require
from ..common.errors import UpstreamHTTPError

# (connect, read) — never an unbounded wait.
HTTP_TIMEOUT = (10, 60)

MIN_LIMIT, MAX_LIMIT = 1, 100
DEFAULT_LIMIT = 20

RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
MAX_ATTEMPTS = 3

RECORDING_SORTS = ("created_asc", "created_desc", "duration_asc",
                   "duration_desc", "title_asc", "title_desc")

#: Sources filterable when READING (`sources` parameter of `GET /v1/recordings`).
RECORDING_SOURCES = ("Aircall", "Allo", "Api", "Call", "GoogleMeet",
                     "LemlistVoip", "Loom", "MobileApp", "MsTeams", "Ringover",
                     "Uploaded", "Zoom")

#: Sources accepted on CREATION — a subset: `Api` is the default when none is
#: given, `Uploaded` and `MobileApp` cannot be declared through the API.
CREATE_SOURCES = ("Aircall", "Allo", "Call", "GoogleMeet", "LemlistVoip",
                  "Loom", "MsTeams", "Ringover", "Zoom")

TRANSCRIPT_FORMATS = ("json", "text")

DEAL_CRMS = ("attio", "hubspot", "pipedrive", "salesforce")

# Keys of the READ shape of a segment: seeing them in a transcript to upload
# means the caller is copying a transcript it read, as-is.
_READ_SHAPE_KEYS = frozenset({"startedAt", "endedAt", "speaker"})


class ClaapClient:
    """Claap v1 client (https://api.claap.io/v1), `X-Claap-Key` auth."""

    BASE_URL = "https://api.claap.io/v1"

    #: The most neutral call of the API: no parameter, no meeting data, and a
    #: clean 401 if the key is wrong.
    PROBE_PATH = "/workspaces/mine"

    def __init__(self, api_key: Optional[str] = None):
        """
        Args:
            api_key: Claap key `cla_…`, passed by the consumer. Created at
                workspace level, by an admin, in Claap → Settings →
                API & Webhooks.
        """
        self.api_key = require(api_key, "CLAAP_API_KEY")
        self.session = requests.Session()
        self.session.headers.update({
            "X-Claap-Key": self.api_key,
            "Accept": "application/json",
        })

    # --- validation ---------------------------------------------------------

    @staticmethod
    def _check_limit(limit: Optional[int]) -> None:
        if limit is None:
            return
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise ValueError("`limit` must be an integer.")
        if not (MIN_LIMIT <= limit <= MAX_LIMIT):
            raise ValueError(
                f"`limit` must be between {MIN_LIMIT} and {MAX_LIMIT} (Claap API "
                f"cap); got {limit}. Beyond that, paginate with `cursor`.")

    @staticmethod
    def _segment(name: str, value: str) -> str:
        """An id placed IN the path, escaped: never a way to reach another endpoint.

        `quote(safe="")` turns `/` into `%2F`, but leaves `.` and `..` intact —
        `/recordings/..` would still climb one level, with a workspace key that
        can delete. Those two are refused, as is anything that is not a
        non-empty string.
        """
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"`{name}` must be a non-empty string.")
        if value in (".", ".."):
            raise ValueError(f"invalid `{name}`: {value!r}.")
        return quote(value, safe="")

    @staticmethod
    def _check_meta_url(meta_url: Any, recording_id: Optional[str]) -> None:
        """The presigned `metaUrl` comes from Claap's answer: only an https URL
        to a public host name is followed — never a bare IP or a local name."""
        parts = urlsplit(meta_url) if isinstance(meta_url, str) else None
        host = (parts.hostname or "") if parts else ""
        refus = None
        if parts is None or parts.scheme != "https" or not host:
            refus = "is not an https URL"
        elif host == "localhost" or host.endswith((".localhost", ".local",
                                                   ".internal")):
            refus = "points to a local host"
        else:
            try:
                ipaddress.ip_address(host)
                refus = "is a bare IP address"
            except ValueError:
                pass
        if refus:
            raise UpstreamHTTPError(
                502, {"recording_id": recording_id,
                      "error": f"Claap's `upload.metaUrl` {refus}: the "
                               "transcript was not sent; the recording already "
                               "exists, empty — delete it or retry."},
                service="claap")

    @staticmethod
    def _check_choice(name: str, value: Optional[str],
                      allowed: Iterable[str]) -> None:
        if value is None:
            return
        allowed = tuple(allowed)
        if value not in allowed:
            raise ValueError(
                f"invalid `{name}`: {value!r}. Accepted values: "
                + ", ".join(repr(a) for a in allowed))

    @classmethod
    def _join_csv(cls, name: str, values: Optional[Iterable[str]],
                  allowed: Optional[Iterable[str]] = None) -> Optional[str]:
        """List → `a,b,c`, the form upstream expects for `labels`/`sources`.

        A value containing a comma is refused: upstream would split it into two
        filters and answer 200 on a filter that is no longer the intended one.
        """
        if values is None:
            return None
        if isinstance(values, str):
            values = [values]
        values = list(values)
        if not values:
            return None
        for v in values:
            if not isinstance(v, str) or not v:
                raise ValueError(f"`{name}`: each value must be a non-empty string.")
            if "," in v:
                raise ValueError(
                    f"`{name}`: {v!r} contains a comma, which Claap uses as a "
                    "separator — the value would be split into two filters.")
            if allowed is not None:
                cls._check_choice(name, v, allowed)
        return ",".join(values)

    @staticmethod
    def _check_transcript(transcript: Dict[str, Any]) -> None:
        """Validate the WRITE shape of a transcript (the one PUT to `metaUrl`).

        `{"langIso2"?, "segments": [{start, end, speakerId, text}], "speakers"?:
        [{name, speakerId, email?, isRecorder?}]}` — this is the content of the
        `transcript` key; the client wraps it itself.
        """
        if not isinstance(transcript, dict):
            raise ValueError("`transcript` must be an object {segments, speakers?, langIso2?}.")
        if "transcript" in transcript and "segments" not in transcript:
            raise ValueError(
                "`transcript`: pass {segments, speakers?, langIso2?} directly, "
                "without wrapping it in a `transcript` key — the client does that.")
        segments = transcript.get("segments")
        if not isinstance(segments, list) or not segments:
            raise ValueError("`transcript.segments`: a non-empty list is required.")
        for i, seg in enumerate(segments):
            if not isinstance(seg, dict):
                raise ValueError(f"`transcript.segments[{i}]` must be an object.")
            if _READ_SHAPE_KEYS & seg.keys():
                raise ValueError(
                    f"`transcript.segments[{i}]` has the READ shape "
                    "(startedAt/endedAt/speaker). Writing expects "
                    "{start, end, speakerId, text}.")
            missing = [k for k in ("start", "end", "speakerId", "text") if k not in seg]
            if missing:
                raise ValueError(
                    f"`transcript.segments[{i}]`: missing field(s) "
                    + ", ".join(missing) + " — expected shape {start, end, speakerId, text}.")
            for k in ("start", "end"):
                if not isinstance(seg[k], (int, float)) or isinstance(seg[k], bool):
                    raise ValueError(
                        f"`transcript.segments[{i}].{k}`: timecode in seconds (a number).")
        speakers = transcript.get("speakers")
        if speakers is not None:
            if not isinstance(speakers, list):
                raise ValueError("`transcript.speakers` must be a list.")
            for i, sp in enumerate(speakers):
                if not isinstance(sp, dict) or "name" not in sp or "speakerId" not in sp:
                    raise ValueError(
                        f"`transcript.speakers[{i}]`: `name` and `speakerId` are required.")

    # --- transport ----------------------------------------------------------

    def _request(self, method: str, path: str, *,
                 params: Optional[Dict[str, Any]] = None,
                 json: Any = None, text: bool = False) -> Any:
        params = {k: v for k, v in (params or {}).items() if v is not None}
        # Retry 429/5xx for READS only: Claap has no idempotency key, a replayed
        # POST would create a second recording.
        retryable = method.upper() in ("GET", "HEAD")
        last = None
        for attempt in range(MAX_ATTEMPTS):
            last = self.session.request(
                method, f"{self.BASE_URL}{path}", params=params or None,
                json=json, timeout=HTTP_TIMEOUT)
            if (last.status_code not in RETRY_STATUSES
                    or not retryable or attempt == MAX_ATTEMPTS - 1):
                break
            time.sleep(float(2 ** attempt))
        raise_for_upstream(last, service="claap")
        if text:
            return last.text
        return last.json() if last.content else {}

    # --- probe --------------------------------------------------------------

    def get_workspace(self) -> Any:
        """GET /v1/workspaces/mine — the key's workspace."""
        return self._request("GET", self.PROBE_PATH)

    def probe(self) -> Any:
        """Check that the key authenticates, for the cost of one call (401 if wrong)."""
        return self.get_workspace()

    # --- recordings ---------------------------------------------------------

    def list_recordings(self, cursor: Optional[str] = None,
                        limit: Optional[int] = None,
                        sort: Optional[str] = None,
                        channel_id: Optional[str] = None,
                        labels: Optional[List[str]] = None,
                        sources: Optional[List[str]] = None,
                        recorder_email: Optional[str] = None,
                        recorder_id: Optional[str] = None,
                        created_after: Optional[str] = None,
                        created_before: Optional[str] = None,
                        view_id: Optional[str] = None) -> Any:
        """GET /v1/recordings — recordings visible to the key, cursor-paginated.

        ⚠️ Without `recorder_email`/`recorder_id`, returns the recordings of the
        WHOLE workspace visible in search, not the key holder's.

        `labels` requires `channel_id` (upstream rule). `view_id` applies a
        view's filters and sort; other filters intersect with it (a value that
        contradicts the view returns an empty page). `sort` overrides the view's
        sort. Dates: ISO 8601 (`2025-02-25`, `2025-02-25T13:00Z`…).
        """
        self._check_limit(limit)
        self._check_choice("sort", sort, RECORDING_SORTS)
        if labels and not channel_id:
            raise ValueError(
                "`labels` is only accepted by Claap together with `channel_id` "
                "(filter by folder first).")
        return self._request("GET", "/recordings", params={
            "cursor": cursor, "limit": limit, "sort": sort,
            "channelId": channel_id,
            "labels": self._join_csv("labels", labels),
            "sources": self._join_csv("sources", sources, RECORDING_SOURCES),
            "recorderEmail": recorder_email, "recorderId": recorder_id,
            "createdAfter": created_after, "createdBefore": created_before,
            "viewId": view_id,
        })

    def get_recording(self, recording_id: str) -> Any:
        """GET /v1/recordings/{id} — detail. Two shapes depending on `state` (see module)."""
        return self._request(
            "GET", f"/recordings/{self._segment('recording_id', recording_id)}")

    def get_recording_transcript(self, recording_id: str,
                                 lang: Optional[str] = None,
                                 format: Optional[str] = None) -> Any:
        """GET /v1/recordings/{id}/transcript.

        `format="text"` → returns a **str** (`text/plain`, "02:17 speaker_1: …").
        `format="json"` (upstream default) → timed segments with per-word detail.
        `lang` (2-letter ISO code) picks a translation; absent = the original.
        """
        self._check_choice("format", format, TRANSCRIPT_FORMATS)
        return self._request(
            "GET", f"/recordings/{self._segment('recording_id', recording_id)}/transcript",
            params={"lang": lang, "format": format}, text=(format == "text"))

    def create_recording(self, author_email: str, *,
                         title: Optional[str] = None,
                         channel_id: Optional[str] = None,
                         source: Optional[str] = None,
                         meeting: Optional[Dict[str, Any]] = None,
                         deal: Optional[Dict[str, Any]] = None,
                         video_url: Optional[str] = None,
                         transcript: Optional[Dict[str, Any]] = None) -> Any:
        """POST /v1/recordings (+ PUT of the transcript to `upload.metaUrl` if given).

        Two modes:
        - `video_url`: Claap fetches the media by GET (< 2 GiB, < 5 min) —
          `video: {type: "download"}`. `transcript` optional on top.
        - no `video_url`: a recording WITHOUT media (`video: {type: "none"}`),
          created from its transcript alone — `transcript` is then required.

        `transcript` = write shape `{segments: [{start, end, speakerId, text}],
        speakers?: [{name, speakerId, email?, isRecorder?}], langIso2?}`.

        `meeting` = `{startedAt, endedAt?, participants?: [{name, email?,
        isOrganizer?}]}`. `deal` = `{type: attio|hubspot|pipedrive|salesforce,
        id}` and requires `meeting` (and the same CRM connected to Claap).

        Creation is asynchronous: the response is `Empty`, then `Uploaded`, then
        `Ready` or `Failed`.

        If the transcript PUT fails, the recording already exists (empty): the
        raised `UpstreamHTTPError` carries `recording_id` in its body.
        """
        if not author_email:
            raise ValueError("`author_email` is required: a member of the Claap workspace.")
        self._check_choice("source", source, CREATE_SOURCES)
        if deal is not None:
            if not meeting:
                raise ValueError("`deal` requires `meeting` (upstream rule).")
            if not isinstance(deal, dict) or not deal.get("id"):
                raise ValueError("`deal`: {type, id} is required.")
            self._check_choice("deal.type", deal.get("type"), DEAL_CRMS)
            if deal.get("type") is None:
                raise ValueError("`deal.type` is required: " + ", ".join(DEAL_CRMS))
        if meeting is not None and not (isinstance(meeting, dict) and meeting.get("startedAt")):
            raise ValueError("`meeting.startedAt` is required when `meeting` is given.")
        if video_url is None and transcript is None:
            raise ValueError(
                "Provide `video_url` (media fetched by Claap) or `transcript` "
                "(a media-less recording, created from its transcript).")
        if transcript is not None:
            self._check_transcript(transcript)

        body: Dict[str, Any] = {
            "authorEmail": author_email,
            "video": ({"type": "download", "url": video_url} if video_url
                      else {"type": "none"}),
        }
        for key, value in (("title", title), ("channelId", channel_id),
                           ("source", source), ("meeting", meeting),
                           ("deal", deal)):
            if value is not None:
                body[key] = value
        if transcript is not None:
            body["transcript"] = {"type": "upload"}

        created = self._request("POST", "/recordings", json=body)
        if transcript is None:
            return created

        rec = ((created or {}).get("result") or {}).get("recording") or {}
        meta_url = (rec.get("upload") or {}).get("metaUrl")
        if not meta_url:
            raise UpstreamHTTPError(
                502, {"recording_id": rec.get("id"),
                      "error": "Claap did not return `upload.metaUrl`: the "
                               "transcript could not be uploaded."},
                service="claap")
        self.upload_transcript(meta_url, transcript, recording_id=rec.get("id"))
        return created

    def upload_transcript(self, meta_url: str, transcript: Dict[str, Any],
                          recording_id: Optional[str] = None) -> None:
        """PUT the transcript (write shape) to the presigned `upload.metaUrl`.

        Bare request (no `X-Claap-Key`: the URL carries its own signature).
        Never retried: replaying a PUT is safe in principle, but a failure here
        reads better as-is, with the id of the already-created recording.
        """
        self._check_transcript(transcript)
        self._check_meta_url(meta_url, recording_id)
        resp = requests.put(meta_url, json={"transcript": transcript},
                            headers={"Content-Type": "application/json"},
                            timeout=HTTP_TIMEOUT)
        if resp.status_code >= 400:
            raise UpstreamHTTPError(
                resp.status_code,
                {"recording_id": recording_id,
                 "error": "transcript PUT refused; the recording already "
                          "exists, empty — delete it or retry.",
                 "body": resp.text[:500]},
                service="claap")

    def delete_recording(self, recording_id: str) -> Any:
        """DELETE /v1/recordings/{id} — ⚠️ permanent, cannot be undone."""
        return self._request(
            "DELETE", f"/recordings/{self._segment('recording_id', recording_id)}")

    # --- recording views (read) ---------------------------------------------

    def list_recording_views(self) -> Any:
        """GET /v1/recordings/views — PUBLIC views, then default views (`isDefault`).

        Private views are invisible to the API. A view using the `Me` filter has
        it resolved against the whole workspace (the key is nobody in particular).
        """
        return self._request("GET", "/recordings/views")

    def get_recording_view(self, view_id: str) -> Any:
        """GET /v1/recordings/views/{id} — a view's columns, filters and sort."""
        return self._request(
            "GET", f"/recordings/views/{self._segment('view_id', view_id)}")


__all__ = [
    "ClaapClient", "HTTP_TIMEOUT", "MIN_LIMIT", "MAX_LIMIT", "DEFAULT_LIMIT",
    "RETRY_STATUSES", "MAX_ATTEMPTS", "RECORDING_SORTS", "RECORDING_SOURCES",
    "CREATE_SOURCES", "TRANSCRIPT_FORMATS", "DEAL_CRMS",
]
