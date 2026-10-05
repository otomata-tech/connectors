#!/usr/bin/env python3
"""
Google Slides API client for generating presentations

Structure du package (découpage 2026-08-27, surface publique INCHANGÉE) :
`slides_client.py` porte la classe `SlidesClient` — résolution des
credentials et construction des services — et compose les familles
d'opérations de `_api/` (présentations & Drive, layouts, styles de texte,
édition de texte, images, copie de slides). Les helpers de mise en forme
vivent dans `markup.py` et restent **réexportés ici** : `slides_client` est
le chemin d'import du connecteur.
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

# Surface figée : `parse_bold_markdown` / `_hex_to_rgb` restent importables
# depuis ce module, comme avant le découpage.
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

        Les credentials sont fournis par le consommateur, l'un ou l'autre :
        1. `credentials` — objet credentials Google (OAuth utilisateur, pour
           manipuler le Drive personnel d'un utilisateur) ; prioritaire.
        2. `credentials_json` (chemin ou chaîne JSON) — service account.
        Aucun des deux → `MissingCredential('GOOGLE_CREDENTIALS')`. La lib ne
        lit aucune variable d'environnement.

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
