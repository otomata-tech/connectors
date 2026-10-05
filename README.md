# oto-core

> Repository: **[otomata-tech/connectors](https://github.com/otomata-tech/connectors)** — renamed on 2026-09-14. The PyPI package keeps the name `oto-core`, and so does the import namespace `oto.*`.

Connector library for [Oto](https://oto.ninja) — pure Python API clients for AI agents and automation. No command line, no server: clients return plain dicts.

**Credentials are always provided by the consumer.** Every client takes its secret (API key, token, OAuth credentials…) as a constructor argument; the library never reads secrets from the environment, files or a secret provider, and a missing one raises `oto.tools.common.credentials.MissingCredential`.

```bash
pip install oto-core              # core (requests, france-opendata)
pip install "oto-core[google]"    # + Google Workspace (Drive, Docs, Sheets, Gmail, Calendar, Tasks)
pip install "oto-core[browser]"   # + browser-based scraping (LinkedIn, via o-browser)
pip install "oto-core[stock]"     # + SIRENE stock queries (DuckDB/parquet)
```

## What's inside

- `oto.tools.*` — one client per service: French company data (SIRENE, INPI, BODACC, BOAMP, DVF via [france-opendata](https://pypi.org/project/france-opendata/)), web search (Serper), email finding (Hunter), CRM (Attio, Folk), outreach (Lemlist, Kaspr, Fullenrich), Google Workspace, Slack, WhatsApp, Reddit, Pennylane, Silae (French payroll), and more.
- `oto.tools.common.FieldFilter` — reusable response redaction (mask IBANs, anonymize names, drop fields) any connector can apply; driven by code or a `field_filters.<service>` policy in `~/.otomata/config.yaml`.

## Shared connector descriptions

New shared connectors are described once, in YAML: `connectors/<name>/connector.yaml`, validated by `connectors/connector.schema.json`. A generator (the *fabrique*) turns each description into a Python client and TypeScript functions. Design docs (in French): [`docs/conception/README.md`](docs/conception/README.md), [description format](docs/conception/format-de-description.md), [generator](docs/conception/fabrique.md).

## Ecosystem

| Package | Role |
|---|---|
| **oto-core** (this) | the clients — single source of truth |
| oto-backend | hosted platform ([mcp.oto.cx](https://oto.cx) — MCP + REST, credential vault, orgs); pins a git version of this library |
| oto 2 npm package | upcoming consumer of the shared connectors, through the generator |

```python
from oto.tools.sirene import SireneClient

client = SireneClient(api_key="…")   # the consumer passes the key
company = client.get_company("130025265")
```

Conventions: clients are import-lazy per optional dependency, raise on error (no silent fallbacks), and stay free of printing concerns.

MIT — © Otomata.
