"""Folk CRM API Client — https://developer.folk.app/api-reference"""

import time
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List
from urllib.parse import urlparse, parse_qs, quote

import requests

from ..common.credentials import require
from ..common import FieldFilter, raise_for_upstream


# RELATION fields: Folk does not accept text operators on them (`like` → 422
# unrecognized_keys), but `in`/`not_in`, and their value is an OBJECT that carries
# the id: `filter[groups][in][id]=grp_…` (verified live on 2026-08-03, doc
# developer.folk.app §list-people). As long as the client wrapped EVERY filter in
# `[like]`, listing a group's members was impossible (signal #260).
RELATION_FIELDS = frozenset({"groups", "companies"})
RELATION_OPS = frozenset({"in", "not_in"})

# Valid values of `subscribedEvents[].eventType` for webhooks (doc
# developer.folk.app/api-reference/webhooks/create-a-webhook, 2026-08-04).
# `object.*` covers deals AND any other custom object_type (no per-object_type
# variant — scoping is done via `filter.objectType`).
WEBHOOK_EVENT_TYPES = frozenset({
    "person.created", "person.updated", "person.deleted",
    "person.groups_updated", "person.workspace_interaction_metadata_updated",
    "company.created", "company.updated", "company.deleted",
    "company.groups_updated",
    "object.created", "object.updated", "object.deleted",
    "note.created", "note.updated", "note.deleted",
    "reminder.created", "reminder.updated", "reminder.deleted",
    "reminder.triggered",
})


def filter_params(filters: Dict[str, Any]) -> Dict[str, Any]:
    """Translate `{field: value}` into Folk's `filter[...]` query params.

    - plain value on a relation field → `filter[field][in][id]`
      ("belongs to this group / this company");
    - plain value on a text field → `filter[field][like]` (the historical
      default: contains);
    - value `{operator: value}` → the requested operator, as is
      (`eq`, `not_eq`, `not_like`, `empty`, `not_empty`, `gt`, `in`, `not_in`)
      — a caller who wants strict equality no longer has to work around the client.

    On a relation, the value of `in`/`not_in` is the BARE id (or a list of ids):
    the parameter's `[id]` is added HERE. An object value (`{"in": {"id": [...]}}`)
    is refused — passed through, `requests` would serialize its KEYS and Folk would receive
    the literal string `id` (opaque 422, oto#146).
    """
    params: Dict[str, Any] = {}
    for key, val in (filters or {}).items():
        if isinstance(val, dict):
            for op, v in val.items():
                if key in RELATION_FIELDS and op in RELATION_OPS:
                    if isinstance(v, dict):
                        raise ValueError(
                            f"filter {key!r}: the value of {op!r} is the bare id or a "
                            f"list of ids, not an object — write "
                            f'{{"{key}": {{"{op}": ["<id>", ...]}}}} or '
                            f'{{"{key}": "<id>"}} (the [id] of the Folk parameter is '
                            f"added by the connector), not {{{op!r}: {v!r}}}.")
                    params[f"filter[{key}][{op}][id]"] = v
                else:
                    params[f"filter[{key}][{op}]"] = v
        elif key in RELATION_FIELDS:
            params[f"filter[{key}][in][id]"] = val
        else:
            params[f"filter[{key}][like]"] = val
    return params


# Filters of `GET /v1/tasks` — field → LEGAL operators (doc
# developer.folk.app/api-reference/filtering §Filterable fields for tasks,
# 2026-08-27). Two reasons NOT to reuse `filter_params` here:
#
# 1. the default operator of `filter_params` is `like`, which exists on
#    NO task field — `{"dueAt": "2026-08-27"}` would go out as
#    `filter[dueAt][like]` (422, or worse: silently ignored);
# 2. `entity` is a RELATION field but is written FLAT
#    (`filter[entity][in]=per_…`), whereas `groups`/`companies` on
#    people want `filter[groups][in][id]=grp_…`. Adding it to
#    RELATION_FIELDS would therefore produce the wrong encoding.
#
# Hard-coded allow-list, like `_CREATE_FIELDS` on the backend side: an unknown field or
# operator must raise NAMING what exists, never go out as is
# to Folk.
TASK_FILTER_OPS: Dict[str, frozenset] = {
    "dueAt": frozenset({"eq", "not_eq", "gt", "lt"}),
    "createdAt": frozenset({"gt", "lt"}),
    "assigneeUserId": frozenset({"in", "not_in"}),
    "entity": frozenset({"in", "not_in"}),
    "completedAt": frozenset({"empty", "not_empty", "gt", "lt"}),
}

# Implicit operator when the caller passes a bare value rather than a
# `{op: value}`. Defined only where there is no ambiguity: a bare
# `createdAt`/`completedAt` date means nothing (before? after?), we require
# the operator rather than inventing one.
TASK_FILTER_DEFAULT_OP = {"dueAt": "eq", "assigneeUserId": "in", "entity": "in"}

# `empty`/`not_empty` are predicates without an operand: Folk wants them with an
# EMPTY value (`filter[completedAt][empty]=`). Whatever the caller passes (True,
# None, "yes"…), we normalize — otherwise `filter[completedAt][empty]=True` tests
# an equality that makes no sense.
_TASK_VALUELESS_OPS = frozenset({"empty", "not_empty"})


def task_filter_params(filters: Dict[str, Any]) -> Dict[str, Any]:
    """Translate `{field: value}` / `{field: {op: value}}` into `filter[...]` for
    `GET /v1/tasks`, refusing any field or operator outside the docs.

    Lists (`in`/`not_in`) are left as is: `requests` serializes them as a
    REPEATED param (`filter[entity][in]=a&filter[entity][in]=b`).
    Verified live on 2026-08-27 on two entities: the repeated key is indeed
    understood as a union (2 ids → 4 tasks, 1 id → 2).
    """
    params: Dict[str, Any] = {}
    for key, val in (filters or {}).items():
        allowed = TASK_FILTER_OPS.get(key)
        if allowed is None:
            raise ValueError(
                f"unknown task filter: {key!r}. Filterable fields: "
                f"{sorted(TASK_FILTER_OPS)}.")
        if isinstance(val, dict):
            pairs = list(val.items())
        else:
            op = TASK_FILTER_DEFAULT_OP.get(key)
            if op is None:
                raise ValueError(
                    f"filter {key!r}: specify the operator, e.g. "
                    f"{{{key!r}: {{'gt': '2026-01-01'}}}} — accepted "
                    f"operators: {sorted(allowed)}.")
            pairs = [(op, val)]
        for op, v in pairs:
            if op not in allowed:
                raise ValueError(
                    f"operator {op!r} not supported on filter {key!r} — "
                    f"accepted: {sorted(allowed)}.")
            params[f"filter[{key}][{op}]"] = "" if op in _TASK_VALUELESS_OPS else v
    return params


def _assigned_users_payload(assigned_users: List[Any]) -> List[Dict[str, str]]:
    """Normalize `assigned_users` into `[{"id": …}]` OR `[{"email": …}]`.

    Folk accepts both forms but **not mixed** in the same
    call (doc create/update a task): a mixed batch goes out as an opaque 422. We
    refuse it here, naming both halves — the caller then knows what to
    cut, which a Folk 422 does not tell them.
    """
    ids, emails, out = [], [], []
    for u in assigned_users:
        if isinstance(u, dict):
            entry = {k: v for k, v in u.items() if k in ("id", "email")}
            if not entry:
                raise ValueError(
                    f"assigned_users: {u!r} has neither 'id' nor 'email'.")
            if len(entry) > 1:
                # The anti-mix guard only applied BETWEEN entries: a dict
                # carrying BOTH keys went out as is — the opaque 422 this
                # helper exists to prevent.
                raise ValueError(
                    f"assigned_users: {u!r} carries 'id' AND 'email' — an "
                    "entry designates only one.")
        elif isinstance(u, str):
            entry = {"email": u} if "@" in u else {"id": u}
        else:
            raise ValueError(
                f"assigned_users: {u!r} must be an id, an email, or a "
                "dict {'id'|'email'}.")
        (emails if "email" in entry else ids).append(next(iter(entry.values())))
        out.append(entry)
    if ids and emails:
        raise ValueError(
            "assigned_users: Folk accepts ids OR emails, not "
            f"both in the same call — ids={ids}, emails={emails}.")
    return out


class FolkClient:
    BASE_URL = "https://api.folk.app/v1"

    def __init__(self, api_key: str = None, field_filter: Optional[FieldFilter] = None):
        self.api_key = require(api_key, "FOLK_API_KEY")
        # Redacts sensitive fields (emails, names…) from every response.
        # Defaults to the `field_filters.folk` policy in ~/.otomata/config.yaml.
        self.field_filter = field_filter or FieldFilter.from_config("folk")

    def _request(self, method: str, endpoint: str, **kwargs) -> Any:
        url = f"{self.BASE_URL}/{endpoint}" if not endpoint.startswith("http") else endpoint
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if method.upper() != "DELETE":
            headers["Content-Type"] = "application/json"
        for attempt in range(3):
            # (connect, read) — repo convention: an unreachable host must not
            # block indefinitely (the transport had NO timeout).
            resp = requests.request(method, url, headers=headers,
                                    timeout=(10, 60), **kwargs)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 2))
                time.sleep(wait)
                continue
            raise_for_upstream(resp, service="folk")
            return self.field_filter.apply(resp.json()) if resp.content else {}
        raise Exception("Rate limit exceeded after retries")

    def _paginate(self, endpoint: str, params: Dict = None,
                  limit: Optional[int] = 100,
                  max_items: Optional[int] = None) -> List[Dict]:
        """`limit=None`: do NOT send a `limit` at all.

        The two interaction endpoints (`/interactions/past`,
        `/interactions/upcoming`) declare ONLY `cursor` and `entity.id`.
        Verified live on 2026-08-27: a `limit` there is **silently
        ignored** (fixed page of 30), not rejected — so sending it breaks nothing,
        but promising it would be a lie. We don't send it, and cursor
        pagination does the job (it works everywhere).

        `max_items`: STOP as soon as we have enough, instead of draining the
        collection. Essential where the page is small AND the volume unbounded:
        measured live on 2026-08-27, `/interactions/past` on an active
        contact returned more than 360 interactions without reaching the end, in
        pages of 30 — dozens of round trips and several minutes
        to answer "what did we say to each other". Pulling everything to
        show only ten is not a truncation, it's a wait.
        """
        params = dict(params or {})
        if limit is not None:
            params.setdefault("limit", limit)
        all_items = []
        while True:
            data = self._request("GET", endpoint, params=params)
            items = data.get("data", {}).get("items", [])
            all_items.extend(items)
            if max_items is not None and len(all_items) >= max_items:
                return all_items[:max_items]
            next_link = data.get("data", {}).get("pagination", {}).get("nextLink")
            if not next_link:
                break
            # Extract cursor from nextLink
            parsed = parse_qs(urlparse(next_link).query)
            cursor = parsed.get("cursor", [None])[0]
            if not cursor:
                break
            params["cursor"] = cursor
        return all_items

    # --- Groups ---

    def list_groups(self) -> List[Dict]:
        return self._paginate("groups")

    def create_group(self, name: str, visibility: str) -> Dict:
        return self._request("POST", "groups", json={
            "name": name, "visibility": visibility,
        }).get("data", {})

    def update_group(self, group_id: str, **fields) -> Dict:
        return self._request("PATCH", f"groups/{group_id}", json=fields).get("data", {})

    # entity_type/custom_field_name are free-form names (spaces, accents…), not
    # opaque ids — quote() otherwise a name like "Deal Status" breaks the path.

    def get_group_custom_fields(self, group_id: str, entity_type: str = "person") -> List[Dict]:
        # Paginated on the API side (cursor, up to 100/page) — a group with >20 custom
        # fields (default limit) was silently truncated by a plain
        # `_request`.
        return self._paginate(f"groups/{group_id}/custom-fields/{quote(entity_type, safe='')}")

    def get_group_custom_field(self, group_id: str, entity_type: str,
                               custom_field_name: str) -> Dict:
        return self._request(
            "GET",
            f"groups/{group_id}/custom-fields/{quote(entity_type, safe='')}/"
            f"{quote(custom_field_name, safe='')}",
        ).get("data", {})

    def create_group_custom_field(self, group_id: str, entity_type: str, **field) -> Dict:
        return self._request(
            "POST", f"groups/{group_id}/custom-fields/{quote(entity_type, safe='')}",
            json=field,
        ).get("data", {})

    def update_group_custom_field(self, group_id: str, entity_type: str,
                                  custom_field_name: str, **fields) -> Dict:
        # The only Folk endpoint of this client whose docs show the field nested
        # under `data.item` (+ `data.nextLink`) rather than flat under `data` —
        # confirmed on the docs' raw JSON example (get/create custom field,
        # for their part, return the field flat). `.get("item", data)` covers both
        # shapes if Folk ever aligns this endpoint with its siblings.
        #
        # Rename verified LIVE (2026-08-17, test workspace): the
        # PATCH response does carry the NEW name (`item.name`), and a
        # `get_custom_field` on the old name right after returns a clean 404 —
        # no window where `custom_field_name` would become inconsistent between
        # the response and a re-fetch.
        data = self._request(
            "PATCH",
            f"groups/{group_id}/custom-fields/{quote(entity_type, safe='')}/"
            f"{quote(custom_field_name, safe='')}",
            json=fields,
        ).get("data", {})
        return data.get("item", data)

    # --- Group members ---
    # user_id is an opaque id (usr_…), no quote() needed (≠ entity_type/
    # custom_field_name which are free-form names).

    def list_group_members(self, group_id: str) -> List[Dict]:
        # Paginated on the API side (cursor, up to 100/page) — same bug as
        # get_group_custom_fields avoided up front: _paginate, not _request.
        return self._paginate(f"groups/{group_id}/members")

    def add_group_member(self, group_id: str, user_id: str, role: str) -> Dict:
        return self._request("POST", f"groups/{group_id}/members", json={
            "id": user_id, "role": role,
        }).get("data", {})

    def remove_group_member(self, group_id: str, user_id: str) -> Dict:
        return self._request("DELETE", f"groups/{group_id}/members/{user_id}")

    def update_group_member(self, group_id: str, user_id: str, role: str) -> Dict:
        return self._request(
            "PATCH", f"groups/{group_id}/members/{user_id}", json={"role": role},
        ).get("data", {})

    # --- People ---

    def list_people(self, **filters) -> List[Dict]:
        return self._paginate("people", filter_params(filters))

    def get_person(self, person_id: str) -> Dict:
        return self._request("GET", f"people/{person_id}").get("data", {})

    def create_person(self, first_name: str, last_name: str = None,
                      emails: List[str] = None, phones: List[str] = None,
                      job_title: str = None, company_name: str = None,
                      company_id: str = None, group_ids: List[str] = None,
                      urls: List[str] = None, description: str = None,
                      **kwargs) -> Dict:
        body: Dict[str, Any] = {"firstName": first_name}
        if last_name:
            body["lastName"] = last_name
        if emails:
            body["emails"] = emails
        if phones:
            body["phones"] = phones
        if urls:
            body["urls"] = urls
        if description:
            body["description"] = description
        if job_title:
            body["jobTitle"] = job_title
        companies = []
        if company_id:
            companies.append({"id": company_id})
        elif company_name:
            companies.append({"name": company_name})
        if companies:
            body["companies"] = companies
        if group_ids:
            body["groups"] = [{"id": gid} for gid in group_ids]
        body.update(kwargs)
        return self._request("POST", "people", json=body).get("data", {})

    def update_person(self, person_id: str, **fields) -> Dict:
        return self._request("PATCH", f"people/{person_id}", json=fields).get("data", {})

    def delete_person(self, person_id: str) -> Dict:
        return self._request("DELETE", f"people/{person_id}")

    # --- Companies ---

    def list_companies(self, **filters) -> List[Dict]:
        return self._paginate("companies", filter_params(filters))

    def get_company(self, company_id: str) -> Dict:
        return self._request("GET", f"companies/{company_id}").get("data", {})

    def create_company(self, name: str, emails: List[str] = None,
                       industry: str = None, **kwargs) -> Dict:
        body: Dict[str, Any] = {"name": name}
        if emails:
            body["emails"] = emails
        if industry:
            body["industry"] = industry
        body.update(kwargs)
        return self._request("POST", "companies", json=body).get("data", {})

    def update_company(self, company_id: str, **fields) -> Dict:
        return self._request("PATCH", f"companies/{company_id}", json=fields).get("data", {})

    def delete_company(self, company_id: str) -> Dict:
        return self._request("DELETE", f"companies/{company_id}")

    # --- Deals (objects in groups) ---

    def list_deals(self, group_id: str, object_type: str = "deals", **filters) -> List[Dict]:
        return self._paginate(f"groups/{group_id}/{object_type}", filter_params(filters))

    def get_deal(self, group_id: str, deal_id: str, object_type: str = "deals") -> Dict:
        return self._request(
            "GET", f"groups/{group_id}/{object_type}/{deal_id}"
        ).get("data", {})

    def create_deal(self, group_id: str, name: str, object_type: str = "deals",
                    people_ids: List[str] = None, company_ids: List[str] = None,
                    custom_fields: Dict = None) -> Dict:
        body: Dict[str, Any] = {"name": name}
        if people_ids:
            body["people"] = [{"id": pid} for pid in people_ids]
        if company_ids:
            body["companies"] = [{"id": cid} for cid in company_ids]
        if custom_fields:
            body["customFieldValues"] = custom_fields
        return self._request("POST", f"groups/{group_id}/{object_type}", json=body).get("data", {})

    def update_deal(self, group_id: str, deal_id: str, object_type: str = "deals",
                    **fields) -> Dict:
        return self._request("PATCH", f"groups/{group_id}/{object_type}/{deal_id}", json=fields).get("data", {})

    def delete_deal(self, group_id: str, deal_id: str, object_type: str = "deals") -> Dict:
        return self._request("DELETE", f"groups/{group_id}/{object_type}/{deal_id}")

    # --- Notes ---

    def list_notes(self, entity_id: str = None) -> List[Dict]:
        # The Folk API IGNORES `filter[entity.id][eq]` on /notes (verified
        # empirically: the param is accepted without error but returns the whole
        # workspace). So we filter client-side on the attached entity
        # (each note carries `entity.id`). oto-backend#224.
        notes = self._paginate("notes", {})
        if entity_id:
            notes = [n for n in notes if (n.get("entity") or {}).get("id") == entity_id]
        return notes

    def create_note(self, entity_id: str, content: str, visibility: str = "public") -> Dict:
        return self._request("POST", "notes", json={
            "entity": {"id": entity_id},
            "content": content,
            "visibility": visibility,
        }).get("data", {})

    def update_note(self, note_id: str, **fields) -> Dict:
        return self._request("PATCH", f"notes/{note_id}", json=fields).get("data", {})

    def delete_note(self, note_id: str) -> Dict:
        return self._request("DELETE", f"notes/{note_id}")

    # --- Interactions ---

    def create_interaction(self, entity_id: str, type: str, title: str,
                           content: str = None, date_time: str = None) -> Dict:
        body: Dict[str, Any] = {
            "entity": {"id": entity_id},
            "type": type,
            "title": title,
        }
        if content:
            body["content"] = content
        # `dateTime` is REQUIRED by Folk (422 `path: ['dateTime'], Required`),
        # whereas this client — and the tool doc — presented it as
        # optional: any call that omitted it failed with an opaque 422.
        # Verified live on 2026-08-27. Default = now: "log this call on this
        # contact" without a date means right now, and this default
        # cannot break any call that worked (those already passed a
        # date).
        body["dateTime"] = date_time or (
            datetime.now(timezone.utc).isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"))
        return self._request("POST", "interactions", json=body).get("data", {})

    # The three endpoints below are in **open beta** at Folk (the docs
    # warn that the surface may change). They already existed when this
    # client only carried `create_interaction`: the connector then claimed
    # that an interaction could not be READ back, which was true of
    # the connector, not of Folk.
    #
    # `entity.id` is REQUIRED in the query on past/upcoming/get/delete: an
    # interaction is only addressable through the person or company it
    # is attached to (there is no "list the whole
    # workspace"). Only the PATCH goes without it.

    def list_past_interactions(self, entity_id: str,
                               max_items: Optional[int] = None) -> List[Dict]:
        return self._paginate("interactions/past", {"entity.id": entity_id},
                              limit=None, max_items=max_items)

    def list_upcoming_interactions(self, entity_id: str,
                                   max_items: Optional[int] = None) -> List[Dict]:
        return self._paginate("interactions/upcoming", {"entity.id": entity_id},
                              limit=None, max_items=max_items)

    # quote() on the id: unlike the other Folk ids (opaque, 40 chars),
    # `get` declares an id of 1 to 512 characters — IMPORTED interactions
    # carry the synthetic id of their source. Seen live: Gmail ids of
    # 60+ characters containing `+` and `_`. Folk accepts them escaped OR raw
    # (tested both); we escape anyway, because nothing guarantees
    # that a future source's id won't carry a `/` or a `?`, which would
    # break the path. Update/delete ids are 40 chars (logged interactions
    # only): quote() is a no-op there.

    def get_interaction(self, interaction_id: str, entity_id: str) -> Dict:
        return self._request(
            "GET", f"interactions/{quote(interaction_id, safe='')}",
            params={"entity.id": entity_id},
        ).get("data", {})

    def update_interaction(self, interaction_id: str, entity_id: str,
                           **fields) -> Dict:
        """⚠️ `entity_id` is REQUIRED — verified live on 2026-08-27.

        The OpenAPI spec lists `entity` among the PATCH body properties
        without marking it required, which reads as "optional, omit it to
        keep the current entity" (that is in fact what the field description
        says on `PATCH /tasks`). Wrong here: without it, Folk answers 422
        `path: ['entity'], message: 'Required'`. So the PATCH is scoped like
        get and delete, just through the body instead of the query.

        Only LOGGED interactions can be modified — Folk rejects imported
        ones (email/calendar/WhatsApp), which belong to their source.
        """
        body: Dict[str, Any] = {"entity": {"id": entity_id}}
        body.update(fields)
        return self._request(
            "PATCH", f"interactions/{quote(interaction_id, safe='')}",
            json=body,
        ).get("data", {})

    def delete_interaction(self, interaction_id: str, entity_id: str) -> Dict:
        return self._request(
            "DELETE", f"interactions/{quote(interaction_id, safe='')}",
            params={"entity.id": entity_id},
        )

    # --- Tasks ---
    # The official successor to reminders (see the Reminders section below).

    def list_tasks(self, filters: Dict[str, Any] = None,
                   only_assigned_to_me: Optional[bool] = None,
                   combinator: Optional[str] = None) -> List[Dict]:
        """`filters` is a DICT, not a `**splat` — unlike
        `list_people`. The keys come from the caller (an agent): `**filters`
        would let a filter named `combinator` or `only_assigned_to_me` get
        swallowed by the same-named parameter, applied SILENTLY and never
        submitted to `task_filter_params`. Same family of collision as
        `_create_one` on the backend side (signal #353): a business field eaten by a
        parameter of the same name."""
        params = task_filter_params(filters)
        if only_assigned_to_me is not None:
            # Folk declares this query as a string ENUM ("true"/"false"),
            # not a boolean: `requests` would serialize a Python bool as
            # "True"/"False" (capitalized), outside the enum.
            params["onlyAssignedToMe"] = "true" if only_assigned_to_me else "false"
        if combinator:
            params["combinator"] = combinator
        return self._paginate("tasks", params)

    def get_task(self, task_id: str) -> Dict:
        return self._request("GET", f"tasks/{task_id}").get("data", {})

    def create_task(self, entity_id: str, title: str, due_at: str,
                    due_time: str = None, description: str = None,
                    recurrence_frequency: str = None,
                    assigned_users: List[Any] = None,
                    is_public: bool = None) -> Dict:
        body: Dict[str, Any] = {
            "entity": {"id": entity_id},
            "title": title,
            "dueAt": due_at,
        }
        if due_time:
            body["dueTime"] = due_time
        if description:
            body["description"] = description
        if recurrence_frequency:
            body["recurrenceFrequency"] = recurrence_frequency
        if assigned_users:
            body["assignedUsers"] = _assigned_users_payload(assigned_users)
        if is_public is not None:
            body["isPublic"] = is_public
        return self._request("POST", "tasks", json=body).get("data", {})

    def update_task(self, task_id: str, **fields) -> Dict:
        if "assignedUsers" in fields:
            fields = dict(fields)
            fields["assignedUsers"] = _assigned_users_payload(
                fields["assignedUsers"])
        return self._request("PATCH", f"tasks/{task_id}", json=fields).get("data", {})

    def delete_task(self, task_id: str) -> Dict:
        return self._request("DELETE", f"tasks/{task_id}")

    # Paths taken from the OpenAPI (`/mark-as-done`, `/mark-as-todo`), NOT from the
    # reminders→tasks migration page, whose example writes `/mark-done` —
    # the spec is authoritative, the example is a typo.
    #
    # A task NEVER completes by itself at Folk: `completedAt` only
    # changes on an explicit call. That is the fundamental difference from a
    # reminder, which marks itself "triggered" on its own schedule.

    def mark_task_done(self, task_id: str, completed_at: str = None) -> Dict:
        # `completedAt` is REQUIRED by the endpoint (and is NOT accepted in
        # a PATCH). Default: now, in ISO 8601 UTC milliseconds —
        # the form used in Folk's examples.
        if not completed_at:
            completed_at = (datetime.now(timezone.utc)
                            .isoformat(timespec="milliseconds")
                            .replace("+00:00", "Z"))
        return self._request(
            "POST", f"tasks/{task_id}/mark-as-done",
            json={"completedAt": completed_at},
        ).get("data", {})

    def mark_task_todo(self, task_id: str) -> Dict:
        # No body: reopening a task resets `completedAt` to null.
        return self._request("POST", f"tasks/{task_id}/mark-as-todo").get("data", {})

    # --- Reminders (DEPRECATED at Folk since 2026-08-13) ---
    # Removal announced for February 2027; the successor is `tasks` above
    # (field mapping: name→title, recurrenceRule→dueAt/dueTime +
    # recurrenceFrequency, visibility→isPublic). These methods stay as long as
    # the endpoints respond, but nothing new should be wired to them.
    # ⚠️ Folk says NOWHERE whether reminders already set also show up in
    # `list_tasks` (two views of the same stock) or whether they live alongside. Not
    # verified live for lack of a key — to be settled before any data
    # migration; see the connector note.

    def list_reminders(self, entity_id: str = None) -> List[Dict]:
        # Same bug as list_notes: the server-side entity filter is ignored →
        # we filter client-side on `entity.id`. oto-backend#224.
        reminders = self._paginate("reminders", {})
        if entity_id:
            reminders = [r for r in reminders if (r.get("entity") or {}).get("id") == entity_id]
        return reminders

    def create_reminder(self, entity_id: str, name: str,
                        recurrence_rule: str, visibility: str = "public") -> Dict:
        return self._request("POST", "reminders", json={
            "entity": {"id": entity_id},
            "name": name,
            "recurrenceRule": recurrence_rule,
            "visibility": visibility,
        }).get("data", {})

    def get_reminder(self, reminder_id: str) -> Dict:
        return self._request("GET", f"reminders/{reminder_id}").get("data", {})

    def update_reminder(self, reminder_id: str, **fields) -> Dict:
        return self._request("PATCH", f"reminders/{reminder_id}", json=fields).get("data", {})

    def delete_reminder(self, reminder_id: str) -> Dict:
        return self._request("DELETE", f"reminders/{reminder_id}")

    # --- Users (workspace members, read-only) ---

    def list_users(self) -> List[Dict]:
        return self._paginate("users")

    def get_current_user(self) -> Dict:
        return self._request("GET", "users/me").get("data", {})

    def get_user(self, user_id: str) -> Dict:
        """Fetch a workspace user by ID. `user_id="me"` returns the current user."""
        if user_id == "me":
            return self.get_current_user()
        return self._request("GET", f"users/{user_id}").get("data", {})

    # --- Webhooks ---

    def list_webhooks(self) -> List[Dict]:
        return self._paginate("webhooks")

    def get_webhook(self, webhook_id: str) -> Dict:
        return self._request("GET", f"webhooks/{webhook_id}").get("data", {})

    def create_webhook(self, name: str, target_url: str,
                       subscribed_events: List[Dict]) -> Dict:
        return self._request("POST", "webhooks", json={
            "name": name,
            "targetUrl": target_url,
            "subscribedEvents": subscribed_events,
        }).get("data", {})

    def update_webhook(self, webhook_id: str, **fields) -> Dict:
        return self._request("PATCH", f"webhooks/{webhook_id}", json=fields).get("data", {})

    def delete_webhook(self, webhook_id: str) -> Dict:
        return self._request("DELETE", f"webhooks/{webhook_id}")
