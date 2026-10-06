"""Re-export — `SpectacleClient` (live-performance entrepreneur licences, LES)
has moved to `france-opendata` (shared French public data lib). Kept for
backward compatibility of the `oto.tools.culture` imports (oto-mcp `tools/culture.py`
imports `SpectacleClient`). Add nothing here: edit `france_opendata.culture_spectacle`.
"""
from france_opendata.culture_spectacle import (  # noqa: F401
    SpectacleClient,
    PORTAL,
    DATASET,
    STATUS_VALUES,
    CATEGORIES,
)
