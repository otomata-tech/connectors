"""
Notion API client with caching support.
"""
import json
import hashlib
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Dict, Any, List
import requests

from ...common.credentials import require
from ...common.local_dirs import get_cache_dir

_HTTP_TIMEOUT = (10, 60)  # (connexion, lecture) — jamais d'attente illimitée

logger = logging.getLogger(__name__)

# Notion caps `children` at 100 blocks per request (create page / append).
_MAX_CHILDREN = 100


def _search_object(filter_type: str) -> str:
    """Map our `filter_type` to Notion's search filter value.

    Since API 2025-09-03 search returns data sources, not databases: the
    filter only accepts "page" or "data_source" — "database" is a 400.
    """
    return "data_source" if filter_type == "database" else filter_type



class NotionClient:
    """Notion API client with automatic caching."""

    def __init__(self, token: Optional[str] = None, cache_enabled: bool = True):
        """Initialize Notion client.

        Args:
            token: Notion integration token. If not provided, reads from config.
            cache_enabled: persist GET responses on disk. Disable on a shared
                multi-user host: the cache key is the request, not the token,
                so a cached file could leak another user's data.
        """
        self.base_url = "https://api.notion.com/v1"
        self.token = require(token, "NOTION_API_KEY")
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": "2025-09-03",
            "Content-Type": "application/json"
        }

        # Setup cache directory
        self.cache_enabled = cache_enabled
        self.cache_dir = get_cache_dir() / 'notion'
        if cache_enabled:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.cache_ttl = 86400  # 24 hours

    def _get_cache_key(self, method: str, endpoint: str, params: Dict = None, data: Dict = None) -> str:
        """Generate cache key from request parameters."""
        cache_data = {
            'method': method,
            'endpoint': endpoint,
            'params': params or {},
            'data': data or {}
        }
        cache_string = json.dumps(cache_data, sort_keys=True)
        return hashlib.sha256(cache_string.encode()).hexdigest()

    def _get_cached(self, cache_key: str) -> Optional[Dict]:
        """Get cached response if valid."""
        if not self.cache_enabled:
            return None
        cache_file = self.cache_dir / f"{cache_key}.json"

        if not cache_file.exists():
            return None

        # Check TTL
        file_age = time.time() - cache_file.stat().st_mtime
        if file_age > self.cache_ttl:
            cache_file.unlink()  # Remove expired cache
            return None

        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None

    def _set_cache(self, cache_key: str, data: Dict):
        """Save response to cache."""
        if not self.cache_enabled:
            return
        cache_file = self.cache_dir / f"{cache_key}.json"

        try:
            with open(cache_file, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning("Failed to cache response: %s", e)

    def _request(self, method: str, endpoint: str, params: Dict = None,
                 data: Dict = None, use_cache: bool = True) -> Dict:
        """Make API request with caching."""

        # Check cache for GET requests
        if use_cache and method == 'GET':
            cache_key = self._get_cache_key(method, endpoint, params, data)
            cached = self._get_cached(cache_key)
            if cached:
                age = int(time.time() - (self.cache_dir / f'{cache_key}.json').stat().st_mtime)
                logger.debug("Using cached response (age: %ss)", age)
                return cached

        # Make API request
        url = f"{self.base_url}/{endpoint}"

        try:
            response = requests.request(
                method=method,
                url=url,
                headers=self.headers,
                params=params,
                json=data
            , timeout=_HTTP_TIMEOUT)
            response.raise_for_status()
            result = response.json()

            # Cache GET requests
            if use_cache and method == 'GET':
                cache_key = self._get_cache_key(method, endpoint, params, data)
                self._set_cache(cache_key, result)

            return result

        except requests.exceptions.HTTPError as e:
            error_data = e.response.json() if e.response.text else {}
            error_msg = error_data.get('message', str(e))
            error_code = error_data.get('code', '')
            status_code = e.response.status_code

            # Enhanced error messages based on status code
            if status_code == 404:
                raise Exception(
                    f"Notion API error (404): Resource not found.\n"
                    f"  Endpoint: {endpoint}\n"
                    f"  Message: {error_msg}\n"
                    f"  Possible causes:\n"
                    f"  - Invalid ID (database/page does not exist)\n"
                    f"  - Integration lacks access permissions to this resource\n"
                    f"  - Resource is in trash or archived"
                )
            elif status_code == 403:
                raise Exception(
                    f"Notion API error (403): Forbidden.\n"
                    f"  Message: {error_msg}\n"
                    f"  The integration does not have permission to access this resource.\n"
                    f"  Add the integration to the page/database in Notion."
                )
            elif status_code == 401:
                raise Exception(
                    f"Notion API error (401): Unauthorized.\n"
                    f"  Message: {error_msg}\n"
                    f"  Check that your Notion integration token is valid."
                )
            elif status_code == 400:
                raise Exception(
                    f"Notion API error (400): Bad request.\n"
                    f"  Message: {error_msg}\n"
                    f"  Code: {error_code}\n"
                    f"  Check the request parameters."
                )
            else:
                raise Exception(
                    f"Notion API error ({status_code}): {error_msg}\n"
                    f"  Code: {error_code}"
                )
        except Exception as e:
            raise Exception(f"Request failed: {str(e)}")

    # API Methods

    def search(self, query: str, filter_type: Optional[str] = None,
               sort: str = "relevance", start_cursor: Optional[str] = None) -> Dict:
        """Search Notion workspace — ONE page of results (at most 100).

        Notion paginates `POST /search`: the answer carries `has_more` and
        `next_cursor`. Pass that cursor back as `start_cursor` to read the next
        page (otomata-tech/oto#249) — without it a caller only ever sees the
        first 100 objects of the workspace.
        """
        data = {"query": query}
        if start_cursor:
            data["start_cursor"] = start_cursor

        # Only add sort if not relevance (Notion API default)
        if sort != "relevance":
            data["sort"] = {"direction": "descending", "timestamp": sort}

        if filter_type:
            data["filter"] = {"value": _search_object(filter_type), "property": "object"}

        return self._request('POST', 'search', data=data, use_cache=True)

    def search_edited_on(
        self,
        date: str,
        filter_type: Optional[str] = None,
        query: str = "",
        max_pages: int = 50,
    ) -> List[Dict]:
        """List pages/databases last edited on a given calendar day (UTC).

        Signal oto-core#69 (feedback plateforme #468/#469, 16/08) : Notion's
        `POST /search` can only ever FILTER on object type — the only
        server-side lever for a date-bounded read is `sort` on
        `last_edited_time` (confirmed against the API reference: `search`
        takes no timestamp filter, unlike a database query's `filter.
        timestamp`). Without it, a run that wants "what changed today" has to
        fetch everything and throw most of it away, and "nothing changed"
        becomes an INFERENCE (diff each candidate against what's already
        known) instead of a computed answer — the exact cost the signal
        reports for a daily ingestion run reading Notion four days running.

        This walks `search` sorted descending by `last_edited_time` and
        stops at the first result older than `date`: everything after it is
        older still, by construction of the sort. Cost tracks the number of
        objects edited since `date`, never workspace size.

        Args:
            date: calendar day, UTC, "YYYY-MM-DD".
            filter_type: "page" or "database" to restrict object type.
            query: optional text query (default: every object shared with
                the integration).
            max_pages: safety bound on the number of `search` calls — raises
                rather than silently returning a truncated answer.

        Returns:
            Page/database objects (raw Notion shape) whose `last_edited_time`
            falls on `date`, most recent first.
        """
        try:
            day_start = datetime.strptime(date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError as e:
            raise ValueError(f"date invalide {date!r}, attendu 'YYYY-MM-DD' : {e}") from e
        day_end = day_start + timedelta(days=1)

        matches: List[Dict] = []
        start_cursor: Optional[str] = None

        for _ in range(max_pages):
            data: Dict[str, Any] = {
                "query": query,
                "sort": {"direction": "descending", "timestamp": "last_edited_time"},
                "page_size": 100,
            }
            if filter_type:
                data["filter"] = {"value": _search_object(filter_type), "property": "object"}
            if start_cursor:
                data["start_cursor"] = start_cursor

            result = self._request('POST', 'search', data=data, use_cache=False)

            exhausted = False
            for obj in result.get('results', []):
                edited = obj.get('last_edited_time')
                if not edited:
                    continue
                edited_dt = datetime.fromisoformat(edited.replace('Z', '+00:00'))
                if edited_dt >= day_end:
                    continue  # edited after the target day: keep descending
                if edited_dt >= day_start:
                    matches.append(obj)
                else:
                    exhausted = True
                    break

            if exhausted or not result.get('has_more'):
                return matches
            start_cursor = result.get('next_cursor')

        raise Exception(
            f"search_edited_on({date!r}): {max_pages} pages Notion parcourues "
            f"sans atteindre le début de la fenêtre — augmenter max_pages ou "
            f"vérifier la date."
        )

    def get_page(self, page_id: str) -> Dict:
        """Get page metadata."""
        page_id = page_id.replace('-', '')
        return self._request('GET', f'pages/{page_id}')

    def get_page_blocks(self, page_id: str, recursive: bool = False) -> Dict:
        """Get page blocks (content).

        Args:
            page_id: Page or block ID
            recursive: If True, fetch children of blocks recursively
        """
        page_id = page_id.replace('-', '')
        result = self._request('GET', f'blocks/{page_id}/children')

        if recursive and 'results' in result:
            for block in result['results']:
                if block.get('has_children'):
                    block_id = block['id'].replace('-', '')
                    children = self.get_page_blocks(block_id, recursive=True)
                    block['children'] = children.get('results', [])

        return result

    def get_block_children(self, block_id: str, recursive: bool = False) -> Dict:
        """Get children of a specific block.

        Args:
            block_id: Block ID
            recursive: If True, fetch children recursively
        """
        return self.get_page_blocks(block_id, recursive=recursive)

    def query_data_source(self, data_source_id: str, filter_obj: Optional[Dict] = None,
                          sorts: Optional[list] = None, page_size: int = 100) -> Dict:
        """Query data source (Notion API 2025-09-03)."""
        data_source_id = data_source_id.replace('-', '')
        data = {"page_size": page_size}

        if filter_obj:
            data["filter"] = filter_obj
        if sorts:
            data["sorts"] = sorts

        return self._request('POST', f'data_sources/{data_source_id}/query', data=data)

    def query_database(self, database_id: str, filter_obj: Optional[Dict] = None,
                       sorts: Optional[list] = None, page_size: int = 100) -> Dict:
        """Query a database's rows.

        In Notion API 2025-09-03, rows live in data sources. `database_id`
        may be a database id (we query its first data source) or directly a
        data source id (what `search` returns).
        """
        data_source = self.resolve_data_source(database_id)
        return self.query_data_source(data_source['id'], filter_obj, sorts, page_size)

    def get_database(self, database_id: str) -> Dict:
        """Get database metadata and schema.

        Also accepts a data source id (what `search` returns since 2025-09-03):
        the data source is returned then, it carries the `properties` schema.
        """
        database_id = database_id.replace('-', '')
        try:
            db_info = self._request('GET', f'databases/{database_id}')
        except Exception as db_error:
            try:
                return self._request('GET', f'data_sources/{database_id}')
            except Exception:
                raise self._not_a_database(database_id, db_error) from db_error
        # Since 2025-09-03 the columns live on the data source, not the database.
        data_sources = db_info.get('data_sources') or []
        if data_sources and 'properties' not in db_info:
            data_source = self._request(
                'GET', f"data_sources/{data_sources[0]['id'].replace('-', '')}")
            db_info = {**db_info, "properties": data_source.get('properties', {})}
        return db_info

    def resolve_data_source(self, database_id: str) -> Dict:
        """Data source object (with `properties`) for a database or data source id."""
        database_id = database_id.replace('-', '')
        try:
            return self._request('GET', f'data_sources/{database_id}')
        except Exception:
            pass
        try:
            db_info = self._request('GET', f'databases/{database_id}')
        except Exception as db_error:
            raise self._not_a_database(database_id, db_error) from db_error
        data_sources = db_info.get('data_sources') or []
        if not data_sources:
            raise Exception(
                f"Database {database_id} has no data source the integration can "
                f"see (empty database, or a linked view of a database that was "
                f"not shared with the integration)."
            )
        return self._request(
            'GET', f"data_sources/{data_sources[0]['id'].replace('-', '')}")

    def _not_a_database(self, object_id: str, error: Exception) -> Exception:
        """Error for an id that is neither a database nor a data source."""
        hint = ""
        if "is a page" in str(error):
            hint = ("\n  This id is a PAGE, not a database: read it as a page; "
                    "to create under it, use parent_type 'page'.")
        else:
            try:
                block = self._request('GET', f'blocks/{object_id}')
            except Exception:
                block = {}
            if block.get('type') == 'child_database':
                hint = ("\n  This id is a LINKED VIEW of a database: the API cannot "
                        "read or write through a view. Use the id of the source "
                        "database (shared with the integration).")
        return Exception(
            f"{object_id} is not a database or data source the integration can see."
            f"{hint}\n  Original error: {error}")

    def create_page(self, parent_id: str, parent_type: str,
                    title: str, properties: Optional[Dict] = None,
                    content: Optional[list] = None) -> Dict:
        """Create new page.

        Args:
            parent_type: "page", or "database"/"data_source" for a database
                row — either a database id or a data source id works.
            content: block objects; more than 100 are appended in batches.
        """
        parent_id = parent_id.replace('-', '')
        properties = dict(properties or {})

        if parent_type in ("database", "data_source"):
            data_source = self.resolve_data_source(parent_id)
            parent = {"type": "data_source_id", "data_source_id": data_source['id']}
            title_prop = next(
                (name for name, prop in (data_source.get('properties') or {}).items()
                 if prop.get('type') == 'title'),
                "Name",
            )
        elif parent_type == "page":
            parent = {"page_id": parent_id}
            title_prop = "title"
        else:
            raise ValueError(
                f"parent_type {parent_type!r}: expected 'page' or 'database'.")

        # The caller may already set the title column in `properties`.
        if not any(isinstance(v, dict) and 'title' in v for v in properties.values()):
            properties[title_prop] = {"title": [{"text": {"content": title}}]}

        data = {"parent": parent, "properties": properties}
        content = list(content or [])
        if content:
            data["children"] = content[:_MAX_CHILDREN]

        page = self._request('POST', 'pages', data=data, use_cache=False)
        if len(content) > _MAX_CHILDREN:
            self.append_blocks(page['id'], content[_MAX_CHILDREN:])
        return page

    def update_page(self, page_id: str, properties: Optional[Dict] = None,
                    archived: Optional[bool] = None) -> Dict:
        """Update page properties."""
        page_id = page_id.replace('-', '')
        data = {}

        if properties:
            data["properties"] = properties
        if archived is not None:
            data["archived"] = archived

        return self._request('PATCH', f'pages/{page_id}', data=data, use_cache=False)

    def append_blocks(self, page_id: str, blocks: list,
                      after: Optional[str] = None) -> Dict:
        """Append blocks to a page/block, at the end or right after `after`.

        Args:
            after: id of an existing child block; the new blocks go just below it.
        """
        page_id = page_id.replace('-', '')
        result: Dict = {}
        appended: list = []
        for i in range(0, len(blocks), _MAX_CHILDREN):
            data: Dict[str, Any] = {"children": blocks[i:i + _MAX_CHILDREN]}
            if after:
                data["after"] = after
            result = self._request(
                'PATCH', f'blocks/{page_id}/children', data=data, use_cache=False)
            appended.extend(result.get('results', []))
            if after and appended:
                after = appended[-1]['id']  # keep the batches in order
        if len(blocks) > _MAX_CHILDREN:
            result = {**result, "results": appended}
        return result
