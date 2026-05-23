# Third-Party Notices

This file summarizes third-party materials and dependency license metadata for
public repository review. It is not a substitute for legal advice.

## Bundled or Adapted Materials

| Component | Source | License / status | Notes |
| --- | --- | --- | --- |
| `bundled_skills/web-access` | https://github.com/eze-is/web-access | MIT, per bundled metadata | LocalClaw-native adaptation. Preserve upstream attribution when redistributing. |
| `test_invoices/` | LocalClaw test fixtures | Project MIT license | Synthetic PDFs generated for tests; no real personal or tax data identified. |

## Runtime Python Dependencies

These dependencies are referenced by `requirements.txt` / `pyproject.toml` and
are not vendored in this repository.

| Package | Observed license metadata |
| --- | --- |
| `pydantic` | MIT |
| `pydantic-settings` | MIT |
| `fastapi` | MIT |
| `python-multipart` | Apache-2.0 |
| `uvicorn` | BSD-3-Clause |
| `websockets` | BSD-3-Clause |
| `click` | BSD-3-Clause |
| `python-dotenv` | BSD-3-Clause |
| `aiosqlite` | MIT |
| `httpx` | BSD-3-Clause |
| `pyyaml` | MIT |
| `jinja2` | BSD |
| `apscheduler` | MIT |
| `aiohttp` | Apache-2.0 AND MIT |
| `pypdf` | BSD-3-Clause |

## Development and Optional Dependencies

| Package | Observed license metadata | Scope |
| --- | --- | --- |
| `pytest` | MIT | Development |
| `pytest-asyncio` | Apache-2.0 | Development |
| `pytest-cov` | MIT | Development |
| `black` | MIT | Development |
| `isort` | MIT | Development |
| `mypy` | MIT | Development |
| `ruff` | MIT | Development |
| `python-telegram-bot` | LGPL-3.0-only | Optional `telegram` extra |
| `ollama` | MIT | Optional `ollama` extra |
| `openai` | Apache-2.0 | Optional `openai` extra |

Before publishing binary distributions or bundled installers, re-check the
exact locked dependency versions and include any required license texts.

## CDN Dependencies

The static UI loads browser libraries from public CDNs at runtime instead of
vendoring them in this repository:

- Axios from `unpkg.com`
- Alpine.js from `unpkg.com`
- qrcodejs from `cdnjs.cloudflare.com`

If these assets are later vendored into the repository or packaged into an
offline build, add their full license notices here.
