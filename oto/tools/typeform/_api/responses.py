"""Responses of a form: list, delete, and a bounded deterministic summary."""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from ..params import ListParam, _csv, _segment
from ..stats import summarize

#: Highest number of pages `summarize_responses` may read, whatever is asked.
MAX_SUMMARY_PAGES = 50

#: Most response ids one deletion takes.
MAX_DELETE_IDS = 1000


class _ResponsesMixin:
    """Responses (scopes responses:read, responses:write). Transport from the client."""

    def list_responses(self, form_id: str, *,
                       page_size: Optional[int] = None,
                       since: Optional[Union[str, int]] = None,
                       until: Optional[Union[str, int]] = None,
                       after: Optional[str] = None,
                       before: Optional[str] = None,
                       included_response_ids: ListParam = None,
                       excluded_response_ids: ListParam = None,
                       response_type: ListParam = None,
                       sort: Optional[str] = None,
                       query: Optional[str] = None,
                       fields: ListParam = None,
                       answered_fields: ListParam = None) -> Any:
        """GET /forms/{form_id}/responses — `{total_items, page_count, items:
        [{response_id, token, landing_id, landed_at, submitted_at, metadata,
        hidden, calculated: {score}, variables, answers: [{field: {id, type,
        ref}, type, <type>: value}]}]}`. An answer's value sits under the key
        named by its `type`: `text`, `choice` (`{label}`), `choices`
        (`{labels}`), `number`, `boolean`, `email`, `url`, `file_url`, `date`,
        `payment`, `signature` (`{url}`), `multi_format`. `answers` are in no
        particular order: match them to the form's fields by `field.id`.

        Args:
            form_id: the form id.
            page_size: default 25, maximum 1000.
            since: inclusive lower bound, ISO 8601 UTC or Unix seconds.
            until: inclusive upper bound, same formats.
            after: cursor — responses after this response `token` (exclusive).
            before: cursor — responses before this response `token`
                (exclusive); with the default newest-first sort, the last
                item's `token` here gives the next page.
            included_response_ids: only these `response_id`s.
            excluded_response_ids: all but these `response_id`s.
            response_type: `completed` (default upstream) | `partial` |
                `started`, one or several — also picks the timestamp
                `since`/`until` filter on.
            sort: `<field>,<asc|desc>`, e.g. `submitted_at,desc` (default for
                completed responses).
            query: exact phrase searched in answers, hidden fields and
                variables.
            fields: field ids — only these appear in `answers`.
            answered_fields: field ids — only responses answering at least one.
        """
        return self._get(
            f"/forms/{_segment('form_id', form_id)}/responses",
            page_size=page_size, since=since, until=until, after=after,
            before=before,
            included_response_ids=_csv("included_response_ids", included_response_ids),
            excluded_response_ids=_csv("excluded_response_ids", excluded_response_ids),
            response_type=_csv("response_type", response_type),
            sort=sort, query=query,
            fields=_csv("fields", fields),
            answered_fields=_csv("answered_fields", answered_fields))

    def delete_responses(self, form_id: str, included_response_ids: List[str]) -> None:
        """DELETE /forms/{form_id}/responses — ⚠️ irreversibly deletes these
        responses (up to 1000 ids, sent in the JSON body). Scope
        responses:write.

        The deletion is asynchronous: success means it was registered, not
        done, and unknown ids are ignored without error. Check afterwards with
        `list_responses(included_response_ids=…)`.
        """
        if isinstance(included_response_ids, str) or not isinstance(included_response_ids, list):
            raise ValueError("`included_response_ids` must be a list of response ids.")
        if not 1 <= len(included_response_ids) <= MAX_DELETE_IDS:
            raise ValueError(f"`included_response_ids` takes 1 to {MAX_DELETE_IDS} ids.")
        if not all(isinstance(i, str) and i.strip() for i in included_response_ids):
            raise ValueError("`included_response_ids`: every id must be a non-empty string.")
        self._send("DELETE", f"/forms/{_segment('form_id', form_id)}/responses",
                   json={"included_response_ids": list(included_response_ids)})

    def summarize_responses(self, form_id: str, *,
                            since: Optional[Union[str, int]] = None,
                            until: Optional[Union[str, int]] = None,
                            response_type: ListParam = None,
                            max_pages: int = 10,
                            page_size: int = 1000) -> Dict[str, Any]:
        """Statistics of a form's responses, computed here from the form and
        its responses: no model, no answer text read.

        Reads the form, then the responses newest first, `page_size` per page,
        `max_pages` pages at most (capped at 50). Returns `{form_id,
        form_title, filters, total_items, responses_analyzed, pages_read,
        max_pages, truncated, note?, responses_per_day: [{date, count}],
        score?: {count, mean, min, max}, fields: [{id, ref, title, type,
        answered, answer_rate, choices?: [{label, count, share}], ranking?:
        [{label, mean_rank, count}], numbers?: {count, mean, min, max},
        distribution?: [{value, count}], nps?: {promoters, passives,
        detractors, score}, booleans?: {true, false}}]}`.

        `answer_rate` is over the responses analyzed; `share` over the
        answers to that field (a multi-select answer counts each label).
        `truncated` is true when the page limit stopped the reading before
        the last response: the summary then covers the newest
        `responses_analyzed` of `total_items`, and `note` says so. Days are
        UTC dates of `submitted_at` (else `landed_at`). Responses from the
        last ~30 minutes may not be counted yet. Scopes forms:read and
        responses:read.

        Args:
            since, until, response_type: as in `list_responses`.
            max_pages: pages read at most, 1 to 50 (default 10).
            page_size: responses per page, 1 to 1000 (default 1000).
        """
        if isinstance(max_pages, bool) or not isinstance(max_pages, int) \
                or not 1 <= max_pages <= MAX_SUMMARY_PAGES:
            raise ValueError(f"`max_pages` must be an integer from 1 to {MAX_SUMMARY_PAGES}.")
        if isinstance(page_size, bool) or not isinstance(page_size, int) \
                or not 1 <= page_size <= 1000:
            raise ValueError("`page_size` must be an integer from 1 to 1000.")
        form = self.get_form(form_id)
        responses: List[Dict[str, Any]] = []
        total: Optional[int] = None
        before: Optional[str] = None
        pages = 0
        done = False
        while pages < max_pages:
            page = self.list_responses(form_id, page_size=page_size, since=since,
                                       until=until, before=before,
                                       response_type=response_type)
            pages += 1
            items = page.get("items") or []
            if total is None and isinstance(page.get("total_items"), int):
                total = page["total_items"]
            responses.extend(items)
            before = items[-1].get("token") if items else None
            if (not items or len(items) < page_size or not before
                    or (total is not None and len(responses) >= total)):
                done = True
                break
        out: Dict[str, Any] = {
            "form_id": form_id,
            "form_title": form.get("title"),
            "filters": {"since": since, "until": until,
                        "response_type": _csv("response_type", response_type)},
            "total_items": total,
            "responses_analyzed": len(responses),
            "pages_read": pages,
            "max_pages": max_pages,
            "truncated": not done,
        }
        if not done:
            out["note"] = (f"Stopped at max_pages ({max_pages}): the newest "
                           f"{len(responses)} of {total if total is not None else 'more'} "
                           "responses are summarized. Raise max_pages or narrow "
                           "since/until for the rest.")
        summary = summarize(form, responses)
        summary.pop("responses_analyzed")
        out.update(summary)
        return out
