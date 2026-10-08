"""Metrics, events, metric aggregates and values reports.

`query_metric_aggregates` and the two values reports are POSTs that only
read. The reports are paced by Klaviyo at 1 call per second, 2 per minute and
225 per day: ask for every statistic needed in one call.
"""

from __future__ import annotations

from typing import List, Optional

from .shapes import path_id, resource

_METRIC_NOT_FOUND = (
    "metric_not_found",
    "No metric with this id in this Klaviyo account: read the ids from the "
    "list of metrics.")


class MetricsMixin:
    """Needs `_call` and `_list` (see `KlaviyoClient`)."""

    def list_metrics(self, filter: Optional[str] = None,
                     page_cursor: Optional[str] = None, *,
                     all_pages: bool = False, max_pages: int = 10) -> dict:
        """List the metrics (event types). Scope metrics:read."""
        return self._list("/metrics", "list_metrics", {
            "filter": filter, "page[cursor]": page_cursor,
        }, all_pages=all_pages, max_pages=max_pages)

    def get_metric(self, metric_id: str) -> dict:
        """One metric by id. Scope metrics:read."""
        return self._call(
            "GET", f"/metrics/{path_id('metric_id', metric_id)}",
            "get_metric", refusals={404: _METRIC_NOT_FOUND})

    def list_events(self, filter: Optional[str] = None,
                    sort: Optional[str] = None,
                    page_size: Optional[int] = None,
                    page_cursor: Optional[str] = None,
                    include: Optional[List[str]] = None, *,
                    all_pages: bool = False, max_pages: int = 10) -> dict:
        """List events (page_size <= 1000), e.g.
        `filter='equals(metric_id,"UxxK4u"),greater-or-equal(datetime,2026-09-01T00:00:00Z)'`.
        `include`: metric, profile. Scope events:read."""
        return self._list("/events", "list_events", {
            "filter": filter, "sort": sort, "page[size]": page_size,
            "page[cursor]": page_cursor, "include": include,
        }, all_pages=all_pages, max_pages=max_pages)

    def create_event(self, data: dict) -> dict:
        """Record an event (`{type: "event", attributes: {properties,
        metric, profile, ...}}`). Flows triggered by the metric may start,
        unless `attributes.backfill` is true. Returns `{}` (202, queued).
        Scope events:write."""
        return self._call("POST", "/events", "create_event",
                          body=resource(data, "event", "create_event"))

    def query_metric_aggregates(self, data: dict) -> dict:
        """Aggregate one metric's events (`{type: "metric-aggregate",
        attributes: {metric_id, measurements, filter, ...}}`; the filter
        bounds `datetime`). Scope metrics:read; 3/s, 60/min."""
        return self._call(
            "POST", "/metric-aggregates", "query_metric_aggregates",
            body=resource(data, "metric-aggregate", "query_metric_aggregates"))

    def query_campaign_values(self, data: dict,
                              page_cursor: Optional[str] = None) -> dict:
        """Campaign performance (`{type: "campaign-values-report",
        attributes: {statistics, timeframe, conversion_metric_id, ...}}`).
        Scope campaigns:read; 1/s, 2/min, 225/day."""
        return self._call(
            "POST", "/campaign-values-reports", "query_campaign_values",
            params={"page_cursor": page_cursor},
            body=resource(data, "campaign-values-report",
                          "query_campaign_values"))

    def query_flow_values(self, data: dict,
                          page_cursor: Optional[str] = None) -> dict:
        """Flow performance (`{type: "flow-values-report", attributes:
        {statistics, timeframe, conversion_metric_id, ...}}`). Scope
        flows:read; 1/s, 2/min, 225/day."""
        return self._call(
            "POST", "/flow-values-reports", "query_flow_values",
            params={"page_cursor": page_cursor},
            body=resource(data, "flow-values-report", "query_flow_values"))
