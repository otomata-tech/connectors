#!/usr/bin/env python3
"""
Google Slides API client for generating presentations

Package structure (split of 2026-08-27, public surface UNCHANGED):
`slides_client.py` holds the `SlidesClient` class — credential resolution
and service construction — and composes the operation families of `_api/`
(presentations & Drive, layouts, text styles, text editing, images, slide
copying). The formatting helpers live in `markup.py` and stay **re-exported
here**: `slides_client` is the connector's import path.
"""
import os
import json
from google.oauth2 import service_account
from googleapiclient.discovery import build

from oto.tools.common.credentials import require

from ._api import (
    _CopyMixin,
    _ImagesMixin,
    _LayoutsMixin,
    _PresentationsMixin,
    _TextEditMixin,
    _TextStyleMixin,
)
from .markup import _hex_to_rgb, parse_bold_markdown

# Frozen surface: `parse_bold_markdown` / `_hex_to_rgb` stay importable
# from this module, as before the split.
__all__ = ["SlidesClient", "parse_bold_markdown", "_hex_to_rgb"]


class SlidesClient(
    _PresentationsMixin,
    _LayoutsMixin,
    _TextStyleMixin,
    _TextEditMixin,
    _ImagesMixin,
    _CopyMixin,
):
    """Client for Google Slides API operations"""

    SCOPES = [
        'https://www.googleapis.com/auth/presentations',
        'https://www.googleapis.com/auth/drive'
    ]

    def __init__(self, credentials_json=None, credentials=None):
        """
        Initialize Slides client.

        Credentials are supplied by the consumer, either one:
        1. `credentials` — Google credentials object (user OAuth, to work on
           a user's personal Drive); takes priority.
        2. `credentials_json` (path or JSON string) — service account.
        Neither → `MissingCredential('GOOGLE_CREDENTIALS')`. The lib reads no
        environment variable.

        Args:
            credentials_json: Path to service account JSON or JSON string
            credentials: Google credentials object provided by the consumer
        """
        if credentials is None:
            require(credentials_json, 'GOOGLE_CREDENTIALS')
            if os.path.isfile(credentials_json):
                credentials = service_account.Credentials.from_service_account_file(
                    credentials_json, scopes=self.SCOPES)
            else:
                credentials_info = json.loads(credentials_json)
                credentials = service_account.Credentials.from_service_account_info(
                    credentials_info, scopes=self.SCOPES)

        self.slides_service = build('slides', 'v1', credentials=credentials)
        self.drive_service = build('drive', 'v3', credentials=credentials)
