"""Metabase — read and query: collections, questions, dashboards, schema, SQL."""

from .client import (MetabaseClient, MetabaseQueryError, MetabaseRedirect,
                     normalize_instance_url, records)

__all__ = ["MetabaseClient", "MetabaseQueryError", "MetabaseRedirect",
           "normalize_instance_url", "records"]
