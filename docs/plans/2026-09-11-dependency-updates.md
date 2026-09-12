# Plan: dependency and runtime updates

Written 2026-09-11. Owner-approved order: scanic 1.6 ships first (PR stacked on #36), then the
steps below, then the Expense Distribution totals feature.

Each step is its own branch: branch → implement → /review → deploy from the branch and check on
device where relevant → /ship. Every deploy first tags the running image
(`receiptory-receiptory:rollback-<step>`) so rollback is a retag plus
`docker compose up -d --no-build receiptory`, about 8 seconds, with no rebuild.

## Survey (2026-09-11)

Versions are the ones in the running image: Python from `uv.lock` (`uv sync --frozen`),
frontend from `package-lock.json` (`npm ci`).

### Runtime

| Component | Now | Target | Why |
|---|---|---|---|
| Node (Docker frontend build stage only) | `node:20-slim` | `node:24-slim` | Node 20 end-of-life 2026-04-30. 24 is the active LTS and already runs in the claude-dev sidecar (24.21.0), where every build and test runs. Vite 8 needs `^20.19 \|\| >=22.12`; scanic 1.6 declares `>=22`. |

Node is not in the runtime image. The Python image serves the built `frontend/dist`.

### Python (pip-audit on the locked runtime set)

| Package | Locked | Fixed / latest | Advisories | Exposure |
|---|---|---|---|---|
| pillow | 12.1.1 | 12.3.0 | 18 | Opens every uploaded, emailed and Telegram image |
| aiohttp | 3.13.3 | 3.14.3 | 24 | HTTP client under litellm and python-telegram-bot |
| python-multipart | 0.0.22 | 0.0.31 (latest 0.0.32) | 5 | Parses web uploads |
| starlette | 1.0.0 | 1.3.1 (via fastapi 0.141.x) | 5 | The web framework |
| weasyprint | 68.1 | 70.0 (major) | 2 | Renders untrusted email HTML to PDF |
| urllib3 | 2.6.3 | 2.7.0 | 2 | Transitive |
| soupsieve | 2.8.3 | 2.8.4 | 2 | Transitive (beautifulsoup4) |
| idna, pygments, click | — | patch | 1 each | Transitive |

Behind, no advisories: uvicorn 0.42 → 0.52, pymupdf 1.27.2 → 1.28.2, playwright 1.58 → 1.62,
beautifulsoup4 4.14 → 4.15, croniter, python-telegram-bot 22.7 → 22.8, pytest / pytest-asyncio.

Held back on purpose: **litellm 1.93 → 1.100.** It drives receipt extraction, so it gets its
own A/B run (the harness generalized on `chore/model-ab-harness`, commit fbd4fb9).

### Frontend (npm audit: 22 findings, 4 low / 6 moderate / 12 high)

Shipped to the browser: react-router / react-router-dom (high, fixed ≥ 7.18.2), dompurify, fflate,
protobufjs (via onnxruntime-web). The rest is build or dev tooling: vite (dev-server `fs.deny`
bypass), postcss, esbuild, browserslist, nanoid, js-yaml, brace-expansion, fast-uri, and the
hono / express-rate-limit / body-parser / qs / ip-address chain.

That last chain is there only because the **`shadcn` CLI is listed under `dependencies`**. It is
a code generator, never imported at runtime, and belongs in `devDependencies`.

Everything except vitest is fixable within its current major. vitest needs 3 → 5.

In-range updates available: react / react-dom 19.3, react-router-dom 7.18.3, vite 8.3,
@base-ui/react 1.8, tailwindcss 4.3, lucide-react 1.45, onnxruntime-web 1.29, shadcn 4.21,
typescript-eslint 8.70, and small ones.

Majors, deferred: vitest 5, eslint 10 (+ @eslint/js 10), TypeScript 7 (the native compiler
rewrite). `@types/node` stays on the 24.x line to match Node 24.

Stale: PR #29 "chore(frontend): remediate dependency vulnerabilities"
(`fix/frontend-dependency-security`, last updated 2026-07-29) overlaps step 3. Close it in favor
of step 3, after checking it has nothing step 3 lacks.

## Steps

### 1. Node 24 for the frontend build — `chore/docker-node-24`

- `Dockerfile`: `FROM node:20-slim` → `FROM node:24-slim`.
- Verify: build the frontend stage with both images from the same lockfile and diff the
  `frontend/dist` file lists and hashes. Identical, or explainable differences, is the gate.
  Then run the full image build, deploy, and confirm HTTP 200 plus the scanner page loading.
- Risk: very low. Build stage only, lockfile unchanged. Commit this plan on this branch.

### 2. Python security bumps — `chore/python-security-bumps`

- Raise the floors in `pyproject.toml` so the lock cannot regress: `Pillow>=12.3.0`,
  `python-multipart>=0.0.31`, `fastapi>=0.141` (pulls starlette ≥ 1.3.1), `weasyprint>=70.0`.
  Then `uv lock --upgrade-package` for those, plus aiohttp, urllib3, idna, soupsieve, pygments
  and click (transitive, lock refresh only). Leave litellm alone.
- Verify:
  - `uv run pytest tests/ -v`, with the documented weasyprint and GTK caveat (passes in Docker).
  - Re-run pip-audit on the new lock: zero findings is the gate.
  - WeasyPrint 70 is a major: render a few real stored emails (HTML → PDF) through
    `backend/processing/normalize` before and after, and compare page count and appearance.
  - Deploy; ingest one image upload, one email and one Telegram document end to end.
- Risk: medium, concentrated in weasyprint 70 and fastapi / starlette request handling
  (uploads, auth cookies). The end-to-end ingests cover both.

### 3. Frontend: audit fixes and in-range updates — `chore/frontend-deps`

- Move `shadcn` to `devDependencies`.
- `npm update` within ranges, then `npm audit fix` without `--force`. Confirm what remains is
  only the vitest-major chain.
- Keep `scanic` pinned at exactly `1.6.0`. `opencv-loader.test.ts` has a version tripwire; a
  bump means re-running the scanner evaluation in `docs/designs/scanic-1.6-upgrade.md` first.
- Verify: `npm test`, `npx tsc -b`, `npm run build`. Compare main-chunk size (current
  1,195 kB / 382 kB gzip). Deploy; click through documents, filters, settings and the scanner,
  and check on the S26 Ultra (react-router and base-ui both touch navigation and dialogs).
- Close PR #29.
- Risk: low to medium, mostly UI regressions from base-ui 1.3 → 1.8 and react-router 7.13 → 7.18.

### 4. litellm — `chore/litellm-1.100` (separate, measured)

- Bump litellm only, then run the extraction A/B harness on a fixed set of stored documents,
  old vs new, same model. Gate: no field-level regressions and no JSON-mode or `drop_params`
  behavior change.
- Risk: medium. litellm changes fast, and extraction is the product.

### 5. Deferred majors (each its own branch, no date)

- vitest 3 → 5: clears the last dev-only audit finding; config and mock API changes.
- eslint 9 → 10 with @eslint/js 10: flat-config changes.
- TypeScript 5.9 → 7: new compiler. Wait until the Vite and typescript-eslint ecosystem
  supports it.

## After this

Expense Distribution opening screen: total expenses for the period, and total income from issued
invoices without double-counting a tax invoice, a receipt (קבלה) and a combined tax-invoice
receipt for the same deal. It needs a matching rule (same client and amount, or a receipt that
cites the invoice number) agreed before it is built. Current data: 20 issued invoices, mixed
"Tax invoice N", "קבלה N" and "חשבונית מס קבלה N".
