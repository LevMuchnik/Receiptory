# CLAUDE.md

## Project Overview

Receiptory is a self-hosted receipt, invoice, and document management system for self-employed professionals. It uses LLM-powered extraction (via litellm) to process scanned and digital documents. Single-user system designed to run on a home NAS.

## Tech Stack

- **Backend:** Python 3.12+, FastAPI, SQLite (WAL + FTS5), litellm, PyMuPDF, Pillow, weasyprint
- **Frontend:** React 19, TypeScript, Vite, shadcn/ui (base-ui variant), Tailwind CSS v4, vitest
- **Package management:** uv (Python), npm (frontend)
- **Deployment:** Docker Compose, single container

## Project Structure

```
backend/                 # FastAPI application
  main.py               # App factory, lifespan, route registration
  config.py             # Settings with env > db > default precedence
  auth.py               # Session-based auth (bcrypt + itsdangerous)
  database.py           # SQLite connection, WAL, migration runner
  storage.py            # File I/O, page rendering (PyMuPDF)
  models.py             # Pydantic request/response models
  api/                  # REST endpoint routers
  processing/           # LLM pipeline, queue, normalize, extract, filing
  backup/               # Scheduler, runner, rclone wrapper
frontend/src/           # React SPA
  lib/api.ts            # Fetch wrapper with auth handling
  lib/pdf-builder.ts    # Scanned pages -> PDF (page size from pixels, see Gotchas)
  lib/scanner/          # Detection + geometry: canvas-utils, geometry, coords,
                        # smoother, classical-detector, ml-detector
  lib/**/*.test.ts      # vitest suite (pure logic, node env)
  contexts/             # AuthContext
  pages/                # Page components
  components/           # Reusable UI components
  components/scanner/   # Mobile scanner UI (viewfinder, review, nav)
frontend/vitest.config.ts  # Frontend test config
migrations/             # Numbered SQL files (001_initial_schema.sql, ...)
scripts/                # Dev utilities (compare_json_mode.py A/B harness)
tests/                  # pytest test suite
```

## Development Commands

```bash
# Backend
uv sync --all-extras                    # Install dependencies
uv run uvicorn backend.main:create_app --factory --reload --port 8484

# Frontend (separate terminal)
cd frontend && npm install && npm run dev

# Tests
uv run pytest tests/ -v
uv run pytest tests/test_e2e.py -v      # E2E only
cd frontend && npm test                 # vitest, single run
cd frontend && npm run test:watch       # vitest, watch mode

# Build frontend for production
cd frontend && npm run build

# gstack (Claude Code skills — run once after cloning)
git submodule update --init --depth 1
cd .claude/skills/gstack && ./setup
```

## Environment

- `.env` file is loaded automatically by litellm on import. All settings use `RECEIPTORY_` prefix.
- `RECEIPTORY_DEV=1` must be set during development to disable static file serving (otherwise the `frontend/dist` mount intercepts API routes).
- `RECEIPTORY_LLM_API_KEY` is required for document processing.

## Architecture Notes

- **Single process:** FastAPI app with background asyncio tasks for the processing queue and backup scheduler. No separate worker or message broker.
- **Database:** SQLite in WAL mode. Hand-rolled migrations (numbered SQL files in `migrations/`). `schema_version` table tracks applied versions. Global `_db_path` is set by `init_db()` and protected by a threading lock.
- **Processing pipeline:** Documents are processed sequentially. Queue polls for `status='pending'`, sets to `processing`, runs normalize → LLM extract → file → update DB. Failures set `status='failed'` with error message.
- **LLM extraction:** Single-pass via litellm. Prompt includes user's business info (names, addresses, tax IDs in multiple languages) and category list with descriptions. Response is parsed into `ExtractionResult` via a tolerant JSON parse ladder (strict parse → fence/salvage recovery).
- **Static files in production:** `app.mount("/", StaticFiles(...))` serves `frontend/dist/`. This catch-all mount MUST be registered last (after all API routes) and is disabled when `RECEIPTORY_DEV=1`.

## Testing

- Tests use pytest with `tmp_path` fixtures for isolated SQLite databases.
- An `autouse` fixture in `conftest.py` resets `_db_path` to None and clears all `RECEIPTORY_*` env vars before each test (litellm auto-loads `.env` on import, which pollutes the test environment).
- `test_normalize.py::test_html_to_pdf` is skipped on Windows (weasyprint requires GTK/Pango native libs). Passes in Docker/Linux.
- Backend API tests use `create_app(data_dir, run_background=False)` with `TestClient`.

**Frontend (vitest):** `cd frontend && npm test` (9 files, 115 tests).

- Config is `frontend/vitest.config.ts`: `environment: "node"`, **no jsdom**, `include: ["src/**/*.test.ts"]`. Tests live next to the module they cover (`src/lib/scanner/geometry.test.ts`, ...).
- Pure logic only, on purpose. `ImageData`, `HTMLCanvasElement`, and `drawImage` do not exist in Node, so anything touching a canvas (`src/lib/scanner/canvas-utils.ts`) is deliberately untested — covering it means jsdom plus the native `canvas` package, which drags cairo/pango build deps into the Docker image.
- If a test needs `document`, it is testing the wrong thing. Test the maths (smoother release branches, coordinate conversions, quad geometry, PDF page sizing) and keep the DOM out.

## Key Design Decisions

- **Config precedence:** environment variable > database setting > default value
- **Categories:** soft-delete (`is_deleted` flag). System categories (`pending`, `uncategorized`, `failed`) cannot be deleted. Category `description` field is fed into the LLM prompt to guide classification.
- **Document type detection:** LLM classifies, but code overrides to `issued_invoice` if `vendor_tax_id` matches any of the user's `business_tax_ids`.
- **LLM JSON handling:** extraction requests JSON mode (`llm_json_mode` setting, env `RECEIPTORY_LLM_JSON_MODE`, default true; litellm `drop_params` skips it on models without `response_format` support). Salvaged parses get a confidence penalty; missing or below-threshold `extraction_confidence` routes the document to `needs_review`. A/B harness: `scripts/compare_json_mode.py`.
- **Deduplication:** SHA-256 file hash. Exact duplicates are skipped at upload, but that is **not an error**: `POST /api/upload` returns HTTP 200 with `{"documents": [...], "duplicates": [...]}`, so an all-duplicate upload is a 200 with an empty `documents` array. Callers must inspect the success payload (see `ScannerPage.handleSubmit`); a client that only checks `res.ok` reports a silent success.
- **Filing:** Stored as `yyyy-mm-dd-vendor_receipt_id-hash.pdf`. Three copies: `originals/` (by hash), `converted/` (if format conversion), `filed/` (human-readable name).
- **FTS5:** Virtual table indexes `raw_extracted_text`, `vendor_name`, `description`, `document_title`. Sync triggers on insert/update/delete.

## Specs and Plans

- Design spec: `docs/specs/2026-03-28-receiptory-v1-design.md`
- Implementation plan: `docs/plans/2026-03-28-receiptory-v1.md`
- Original requirements: `docs/initial_specifications.md`
- Design records (per-feature, written before the work): `docs/designs/` — e.g. `docs/designs/mobile-scanner-detection-and-capture.md`

## Gotchas

- litellm loads `.env` on import, setting `RECEIPTORY_*` env vars globally. This affects config precedence — env vars override DB values. In tests, the autouse fixture clears these.
- The `frontend/dist/` directory persists after builds. If present, the static files mount activates. Always set `RECEIPTORY_DEV=1` when running backend + Vite dev server together.
- SQLite `executescript()` auto-commits and can interfere with transaction isolation. The migration runner uses a dedicated connection that's closed after migrations.
- **Scanner DPI is coupled across the stack.** `TARGET_DPI` in `frontend/src/lib/pdf-builder.ts` (200) sizes each scanned PDF page in millimetres from its own pixel dimensions; `page_render_dpi` in `backend/config.py` (200) rasters that PDF back to pixels for the LLM. The round trip is 1:1 only while the two are equal — change one without the other and capture resolution is silently thrown away before the model ever sees it. Both files carry reciprocal comments.
- jsPDF **sorts** a `format: [w, h]` array and then uses `orientation` to decide which value is the page width, so `{orientation: "portrait", format: [300, 100]}` yields a 100x300 page and the image is silently clipped. Derive orientation from the dimensions instead: `w > h ? "landscape" : "portrait"`.

## gstack

Use the `/browse` skill from gstack for all web browsing. Never use `mcp__claude-in-chrome__*` tools.

Available skills: `/office-hours`, `/plan-ceo-review`, `/plan-eng-review`, `/plan-design-review`, `/design-consultation`, `/design-shotgun`, `/design-html`, `/review`, `/ship`, `/land-and-deploy`, `/canary`, `/benchmark`, `/browse`, `/connect-chrome`, `/qa`, `/qa-only`, `/design-review`, `/setup-browser-cookies`, `/setup-deploy`, `/setup-gbrain`, `/retro`, `/investigate`, `/document-release`, `/document-generate`, `/codex`, `/cso`, `/autoplan`, `/plan-devex-review`, `/devex-review`, `/careful`, `/freeze`, `/guard`, `/unfreeze`, `/gstack-upgrade`, `/learn`.

## Skill routing

When the user's request matches an available skill, ALWAYS invoke it using the Skill
tool as your FIRST action. Do NOT answer directly, do NOT use other tools first.
The skill has specialized workflows that produce better results than ad-hoc answers.

Key routing rules:
- Product ideas, "is this worth building", brainstorming → invoke office-hours
- Bugs, errors, "why is this broken", 500 errors → invoke investigate
- Ship, deploy, push, create PR → invoke ship
- QA, test the site, find bugs → invoke qa
- Code review, check my diff → invoke review
- Update docs after shipping → invoke document-release
- Weekly retro → invoke retro
- Design system, brand → invoke design-consultation
- Visual audit, design polish → invoke design-review
- Architecture review → invoke plan-eng-review
- Save progress, checkpoint, resume → invoke checkpoint
- Code quality, health check → invoke health
