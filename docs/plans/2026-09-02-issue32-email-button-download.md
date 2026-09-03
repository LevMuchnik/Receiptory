# Issue #32 — Email bills behind a JS download button capture the viewer shell, not the PDF

Branch: `fix/issue32-email-button-download`
Issue: https://github.com/LevMuchnik/Receiptory/issues/32
Related: #27 (Adobe canvas-viewer streamed-PDF capture — same function)

## Problem

Email-ingested bills from `mast.co.il/bill-viewer/<token>` (מועצה מקומית מבשרת ציון,
sender `mast@outbox.co.il`) store a **1-page render of the viewer web page** instead
of the real multi-page PDF. The doc still extracts at 0.95 and is marked `processed`,
so the failure is **silent**. Evidence: doc 266 (email) = 1 page / 309 KB; doc 185
(real PDF, hand-submitted) = 2 pages / 680 KB.

## Root cause (confirmed — see issue #32)

`fetch_url` → `_playwright_fetch` (`backend/ingestion/url_fetcher.py`) resolves a viewer
URL through three shapes, and the mast Angular SPA matches none:

```
_playwright_fetch(url)
  goto(load)
  ├─ (1) application/pdf streamed on load?  ── #27 path (_read_streamed_pdf) ──▶ save real PDF
  │        mast: NO stream on load (PDF built only on button click)          ✗
  ├─ (2) password field? ─▶ page.pdf() auth_wall capture
  ├─ (3) <a href> doc link in rendered DOM? (_find_document_links)
  │        - scans <a> ONLY (mast control is a <button>)                     ✗
  │        - DOWNLOAD_KEYWORDS = /download|invoice|receipt/i  (English only;
  │          mast button text is Hebrew "הורדה")                             ✗
  └─ (4) FALLBACK: page.pdf() ──▶ renders the VIEWER SHELL (1 page)  ◀── lands here, filed as the bill
```

Two defects compound: (a) no button-triggered-download handling exists at all
(`page.expect_download()` appears nowhere), and (b) the `page.pdf()` fallback produces
a plausible artifact that extracts cleanly, so a capture failure is filed as `processed`.

## Goals

1. Capture the real PDF when it sits behind a JS download button/link (mast and the
   general class of button-gated Hebrew/English receipt portals).
2. Stop the silent-success masking: when we fall back to a `page.pdf()` viewer-shell
   capture, route the document to `needs_review` instead of `pending`/`processed`.

## Design

### Change 1 — button-triggered download in `_playwright_fetch`

Insert a new capture attempt **between the DOM `<a href>` scan (step 3) and the
`page.pdf()` fallback (step 4)**. Order matters: the streamed-PDF path (#27) and the
existing `<a href>` follow stay ahead of it (they are cheaper and already proven); the
button click is the new last resort before giving up to `page.pdf()`.

```
(3) <a href> doc-link follow           (unchanged)
(3b) NEW: click a download control inside page.expect_download()
        - locate candidate controls: <button>, [role=button], <a>, elements whose
          visible text / aria-label / title / value / download attr matches a
          multilingual DOWNLOAD keyword net
        - for the first candidate: async with page.expect_download() as dl_info:
              await candidate.click()
          save download.path() -> temp file; SSRF re-check download.url; size cap
        - method="playwright_download_click"
(4) page.pdf() fallback                (now flagged — see Change 2)
```

Details:
- **Two keyword nets (eng-review decision, finding 3 → scoped, NOT shared).** Do NOT
  broaden the existing `DOWNLOAD_KEYWORDS`: it is used by `_find_document_links` in the
  static httpx `<a>`-scan (`fetch_url:407`), and broadening it there raises false-positive
  link-follows. Instead add a SEPARATE `_CLICK_DOWNLOAD_KEYWORDS` net for step 3b only:
  Hebrew `הורדה`/`הורד`/`להורדה`/`הורדת` + English `download`/`pdf`/`save`, matched against
  the control's visible text AND `aria-label`/`title`/`value`/`download` attr. The static
  scan keeps its conservative English-link net unchanged.
- **Locating controls.** Use Playwright locators, not BeautifulSoup — the download is a
  live DOM element with JS handlers. Query `button, [role=button], a[href]`
  (drop `input[type=submit]` — outside voice #8: it contradicts the pay/submit denylist
  and is not a download affordance), filter by `_CLICK_DOWNLOAD_KEYWORDS` against
  text/attrs, take visible+enabled ones.
- **Ranking + denylist (eng-review decision, finding 1 → guarded single-click).** Rank
  candidates: download/PDF keywords (`הורדה`/`download`/`pdf`) above print/save; and a
  hard **never-click denylist** — skip any control matching pay/submit/checkout/delete
  and their Hebrew equivalents (`שלם`/`תשלום`/`מחק`). These are billing portals; a wrong
  click could trigger a real account action.
- **Dual capture after the click (eng-review decision, finding 1+2 → dual capture).**
  A client-side SPA can deliver the PDF two ways when the button is clicked: as a browser
  **download event** (attachment) OR as an **inline `application/pdf` response**. Cover
  both — do NOT remove the `_on_response` listener before clicking:
  - Keep the `_on_response` PDF listener (line 245) ALIVE across the click.
  - `async with page.expect_download(timeout=...)`: click the **single best** candidate,
    then take `download.path()` if the event fired.
  - After the click, ALSO re-check `pdf_responses` via `_read_streamed_pdf` — if the PDF
    came back as an inline response, capture it there.
  - Take whichever fired. If neither, fall through to `page.pdf()`.
  `expect_download` is the safety property: a mis-click that produces no download/response
  can never be filed as a fake success (it lands in the `page.pdf()` → needs_review path).
- **SSRF re-check allows blob:/data: (eng-review decision, finding 1).** `_is_safe_url`
  (url_fetcher.py:48) returns False for any non-http(s) scheme, so a client-generated
  `blob:`/`data:` download URL would be wrongly rejected — silently defeating the fix for
  the most likely mast case. Apply `_is_safe_url` ONLY to `http`/`https` download URLs;
  ALLOW `blob:`/`data:` (same-origin, browser-created; the top-level page already passed
  `_is_safe_url` at navigation). The inline-response path keeps its existing per-response
  `_is_safe_url` gate (those are real http(s) URLs).
- **Bounded wait.** Bound the click/download wait with a **~15s window** (not 5s — a
  build-on-click PDF can take longer; #27's settle is 10s; not the 30s nav floor). This
  wait applies ONLY after a matched download control is clicked, so button-less viewer
  pages skip it entirely and are not slowed.
- **Reuse guards** (prior learning `playwright-network-capture-hardening`):
  `_MAX_CAPTURE_BYTES` and a per-action `asyncio.wait_for` so a stalled download can't
  hang the single-process queue. Note (outside voice #9): for the download-event path the
  size cap is **post-hoc** — Playwright streams to disk before `download.path()` is
  readable, so the cap is checked after the file is written (acceptable on the NAS;
  discard + None if over). Wrap click+save in try/except: any exception (element detached,
  click intercepted) falls through to `page.pdf()`.
- New `FetchResult.method` value: `"playwright_download_click"`.

### Change 2 — surface the viewer-shell fallback

`page.pdf()` fallback (step 4) is a last-resort screenshot-of-a-webpage, not a real
document. Mark it so downstream can route it to review.

- Set a flag on `FetchResult` for the shell capture. Reuse the existing
  `auth_wall`-style downgrade path rather than inventing a parallel mechanism:
  add `FetchResult.page_capture: bool = False`, set `True` on the step-4
  `playwright_capture` return (NOT on the auth-wall capture, which already downgrades).
- Scope of the downgrade (eng-review decision, finding 2 → **all** `page.pdf()` captures):
  `page_capture=True` is set on EVERY step-4 `playwright_capture` fallback (a page.pdf()
  capture is by definition "we couldn't get the real file"), and always routes to
  `needs_review`. Not narrowed to "a control was seen" — the mast bug was an *unrecognized*
  control, so narrowing would re-open the exact hole.
- In `gmail.py::_ingest_url` (line ~340), extend the existing downgrade:
  `if fetch_result.auth_wall or fetch_result.page_capture: status = "needs_review"`,
  and set `user_notes` ("Captured viewer page render, not a downloaded file — verify the PDF").
- **`telegram.py` needs the SAME fix (confirmed).** `telegram.py:125` today downgrades only
  on `auth_wall` (`if result.auth_wall: status = "needs_review"` else `"pending"`). Add
  `or result.page_capture` there too, or the silent-success bug persists for Telegram-submitted
  viewer URLs. Both callers are in scope.

### Change 3 — mast.co.il site handler (added during implementation, T5 finding)

Live verification (T5) proved the generic button-click CANNOT capture the mast bill:
the viewer nests content in an iframe + a shadow-DOM web component (`APP-BILL-VIEWER-LIST`),
labels the control in Hebrew ("שובר"/"צפייה", not "download"), and loads an invisible
reCAPTCHA v3. The generic path only ever captured the 1-page viewer shell.

But the SPA fetches the bill from a **public JSON API keyed by the same guid** that is in
the viewer URL, and that JSON carries a direct `pdfLink` (Azure blob). No browser, no
shadow DOM, no CAPTCHA needed. So a deterministic site handler is the correct fix:

```
mast.co.il/bill-viewer/<guid>
  → GET api.mast.co.il/mast/api/Stubs/GetStubsByGuidForPresentingStubs?guid=<guid>   (JSON)
  → stubExtentionList[].pdfLink  →  GET Azure blob  →  real PDF   (method="mast_api")
```

- `_mast_guid(url)` detects `mast.co.il`/`www.mast.co.il` + `/bill-viewer/<guid>` and
  URL-decodes the guid; `_fetch_mast_bill` calls the API, follows the first `pdfLink`
  (logs when a period carries >1 stub — one file per URL), SSRF-checks the blob URL,
  size-caps, returns `method="mast_api"`.
- Wired at the TOP of `fetch_url` (after the SSRF gate, before the generic path). Returns
  None on any failure so a mast API change can't strand ingestion — it degrades to the
  generic path → `page.pdf()` → `page_capture` → needs_review.
- Verified live: returns the real 2-page / 680,854-byte bill, matching hand-submitted
  doc 185 (2 pages / 680,886 B).

This is the "known-viewer registry" the plan had deferred — justified now because it is a
clean, deterministic API call (not the brittle DOM crawler the deferral worried about).
Kept as a single named handler, not a registry abstraction (YAGNI until a 2nd site).

### Data flow after the fix

```
email URL ─▶ fetch_url ─▶ httpx GET (text/html)
                          ─▶ _find_document_links (static)      ── none
                          ─▶ _playwright_fetch
                               (1) streamed application/pdf?     ── real PDF ✓ (method=playwright_network)
                               (3) <a href> doc link?            ── real PDF ✓ (method=playwright_download)
                               (3b) button-click download?  ◀── mast lands here ▶ real PDF ✓ (method=playwright_download_click)
                               (4) page.pdf() shell             ── page_capture=True ▶ needs_review
```

## Edge cases

- Multiple download-like buttons (print vs download vs share): rank PDF/download
  keywords above print; take the single best; never click more than one.
- Button triggers a same-tab navigation to a PDF instead of a download event: covered
  by `page.expect_download` (Chromium fires `download` for `application/pdf` responses
  with content-disposition); if it navigates and renders instead, we still fall to
  page.pdf() → needs_review (no regression).
- Download saves a non-document (html/zip): existing `normalize`/classify already gates
  this; the fetched file still runs through `_render_first_page_from_file` + LLM classify
  in `_process_urls`, so a junk download is discarded like any non-qualifying URL.
- No Chromium / playwright missing: unchanged — `_playwright_fetch` already returns None
  on ImportError.
- Click throws / element detaches: catch, fall through to page.pdf() fallback.

## Test plan (mirroring #27's mocked-Playwright style)

`tests/test_url_fetcher.py` (existing patterns: patch `httpx.AsyncClient`, patch
`_playwright_fetch`/playwright objects with AsyncMock):

1. Button-click happy path: mocked page with a Hebrew "הורדה" button; `expect_download`
   yields a temp PDF → `FetchResult.method == "playwright_download_click"`, file saved,
   `page_capture == False`.
2. English "Download" button → same.
3. Keyword matcher matches on `aria-label`/`title`, not just visible text.
4. Ranking + denylist: page with "print", "הורדה", and a "שלם/pay" button → clicks the
   download control, NEVER the pay control.
5. No download control + no stream + no link → `page.pdf()` fallback returns
   `page_capture=True`, `method="playwright_capture"`.
6. **[gap→added]** Click raises / element detaches → caught, falls to `page.pdf()`
   fallback (`page_capture=True`), no crash.
7. **[gap→added]** Download event never fires within the detect window (button navigated
   instead) → falls to `page.pdf()` fallback (`page_capture=True`).
8. **[gap→added, regression guard]** Auth-wall `page.pdf()` capture sets `auth_wall=True`
   but `page_capture=False` — the auth path is unchanged and not double-counted.
9. SSRF re-check rejects a download whose resolved URL is private.
10. Size cap: oversized download discarded (`_MAX_CAPTURE_BYTES`).

`tests/test_gmail_urls.py` (where `_ingest_url` is covered):

11. `_ingest_url` with `page_capture=True` → status `needs_review` + user_notes set;
    and `auth_wall=True` still → `needs_review` (unchanged).

`tests/test_telegram_urls.py`:

12. **[gap→added, 2nd caller]** Telegram URL ingest with `page_capture=True` → status
    `needs_review` (today it would be `pending` — this is the regression the fix closes
    for Telegram).

**Live verification (MANDATORY — outside voice #5, not optional).** Mocked tests prove
the plumbing, not the capture — the original bug hid *because* unit behavior looked fine
while real capture silently failed. Before shipping, fetch the live
`mast.co.il/bill-viewer/<token>` URL (from doc 266) through `fetch_url` and assert the
captured PDF is the **real multi-page bill** — page_count and size in the ballpark of
doc 185 (2 pages / ~680 KB), NOT a 1-page / ~300 KB shell. This is the metric from the
issue's own evidence; a mocked-only suite cannot catch a recurrence.

## NOT in scope

- **Remediating already-ingested shell docs (outside voice #6, eng-review decision → by hand).**
  The fix is forward-only: Gmail marked those emails `\Seen` and `unread_only` won't re-poll
  them, so doc 266 and any prior viewer-shell captures stay wrong. Decision: **no remediation
  code.** Re-submit doc 266 by hand (as doc 185 was) once the fix ships. Accepted risk: other
  already-ingested shells stay silently wrong with no automated way to find them (single-user
  NAS, low volume — acceptable).
- Site-specific handler / known-viewer registry for mast/outbox (only if the generic
  button-click proves brittle in practice — deferred, noted in issue #32).
- Retrying/queuing the click across multiple candidates (single best-candidate click only).
- Distribution/pipeline changes (none — internal web service).

## Files

- `backend/ingestion/url_fetcher.py` — `_playwright_fetch` (new step 3b button-click),
  `DOWNLOAD_KEYWORDS` → multilingual shared matcher, `FetchResult` (+`page_capture` bool,
  +`playwright_download_click` method), ranking/denylist helper.
- `backend/ingestion/gmail.py` — `_ingest_url` (line ~340) downgrade on `page_capture`.
- `backend/ingestion/telegram.py` — line ~125 downgrade on `page_capture` (confirmed needed).
- `tests/test_url_fetcher.py` — tests 1-10.
- `tests/test_gmail_urls.py` — test 11.
- `tests/test_telegram_urls.py` — test 12.

## What already exists (reused, not rebuilt)

- `_playwright_fetch` streamed-`application/pdf` capture (`_on_response` + `_read_streamed_pdf`,
  #27) — reused and now kept alive across the click (dual capture).
- `_find_document_links` + `DOWNLOAD_KEYWORDS` — reused unchanged for the static `<a>` scan;
  the click path gets a separate net.
- Guards `_is_safe_url`, `_MAX_CAPTURE_BYTES`, `_MIN_RENDER_TIMEOUT` — reused (with the
  blob/data carve-out on `_is_safe_url` for the download path).
- `auth_wall → needs_review` downgrade in `gmail._ingest_url` and `telegram` — the
  `page_capture` downgrade extends this exact branch, not a parallel mechanism.

## Failure modes (new codepaths)

| Failure | Test? | Error handling? | User sees |
|---------|-------|-----------------|-----------|
| Click navigates instead of downloading | yes (#7) | falls to page.pdf() | needs_review (not silent) ✓ |
| Click raises / element detaches | yes (#6) | try/except → page.pdf() | needs_review ✓ |
| blob:/data: download URL | yes (#1 happy) | allowed past `_is_safe_url` | real PDF ✓ |
| PDF served inline, no download event | covered by dual capture (#2) | `_on_response` catches it | real PDF ✓ |
| Download exceeds size cap | yes (#10) | post-hoc discard → None → page.pdf() | needs_review ✓ |
| Build-on-click PDF slower than 15s | (live verify #live) | times out → page.pdf() | needs_review (not silent) ✓ |

No new **silent** failure mode: every miss lands in `page.pdf()` → `page_capture=True` →
`needs_review`. That is the whole point of Change 2.

## Worktree parallelization strategy

Sequential implementation, no parallelization opportunity — every change lives in
`backend/ingestion/` (url_fetcher is the core; gmail + telegram are 1-line downgrades that
depend on the `page_capture` field the core adds). One lane.

## Implementation Tasks
Synthesized from this review's findings. Each derives from a finding above.

- [ ] **T1 (P1, human: ~3h / CC: ~25min)** — url_fetcher — add step 3b dual-capture button-click download
  - Surfaced by: Architecture finding 1 + outside voice #1/#2 — client-generated PDF (blob or inline) behind a JS button
  - Files: `backend/ingestion/url_fetcher.py` (`_playwright_fetch`, new `_CLICK_DOWNLOAD_KEYWORDS`, ranking/denylist helper, blob/data carve-out, keep `_on_response` alive across click, `expect_download`, 15s window)
  - Verify: `test_url_fetcher.py` tests 1-10
- [ ] **T2 (P1, human: ~30min / CC: ~5min)** — url_fetcher — `FetchResult.page_capture` set on the page.pdf() fallback only
  - Surfaced by: Architecture finding 2 — stop silent success
  - Files: `backend/ingestion/url_fetcher.py` (`FetchResult`, step-4 return)
  - Verify: test #5, #8 (auth_wall regression guard)
- [ ] **T3 (P1, human: ~30min / CC: ~5min)** — ingestion — downgrade `page_capture` → needs_review in BOTH callers
  - Surfaced by: finding 2 + outside voice #7 — telegram has the identical hole
  - Files: `backend/ingestion/gmail.py` (~340), `backend/ingestion/telegram.py` (~125)
  - Verify: `test_gmail_urls.py` #11, `test_telegram_urls.py` #12
- [ ] **T4 (P1, human: ~2h / CC: ~15min)** — tests — 12 mocked-Playwright tests
  - Surfaced by: Test review — 4 gaps + happy paths
  - Files: `test_url_fetcher.py`, `test_gmail_urls.py`, `test_telegram_urls.py`
  - Verify: `uv run pytest tests/test_url_fetcher.py tests/test_gmail_urls.py tests/test_telegram_urls.py`
- [ ] **T5 (P1, human: ~15min / CC: ~10min)** — verification — live capture against doc 266's mast URL
  - Surfaced by: outside voice #5 — mocked tests can't catch real-capture recurrence
  - Verify: fetched PDF ≈ doc 185 (2 pages / ~680 KB), not a 1-page shell

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 0 | — | not run (bug fix) |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | CLEAR | 6 issues, 0 critical gaps — all folded |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | — | not run (backend-only) |
| Outside Voice | Claude subagent | Independent plan challenge | 1 | issues_found | 9 raised; 6 substantive folded, 3 minor noted |

**Completion summary**
- Step 0: scope accepted as-is (~5 files, 0 new classes — under threshold)
- Architecture: 2 findings (guarded single-click; page_capture→needs_review) — both folded
- Code Quality: 0 blocking (DRY note superseded by finding-3 two-nets decision)
- Test Review: diagram produced, 4 gaps → all added (12 tests + mandatory live verification)
- Performance: 1 note (15s window, post-click only) — folded
- Outside Voice: ran (Claude subagent, Codex not installed); 6 substantive findings folded
  (#1 blob/data SSRF carve-out, #2 dual capture, #3 two scoped nets, #4 15s window,
  #5 live verification, #6 forward-only stated), 3 minor noted (#7 already committed,
  #8 dropped submit-input, #9 post-hoc size cap)
- NOT in scope: written (remediation forward-only; known-viewer registry; single-click)
- Failure modes: 0 critical gaps — every miss routes to needs_review, none silent
- TODOS.md: 0 added (remediation chosen as by-hand, no code)
- Parallelization: 1 lane, sequential (all in backend/ingestion/)

**OUTSIDE VOICE:** headline catch — reusing `_is_safe_url` on the download URL would
reject a `blob:`/`data:` client-generated PDF and silently defeat the fix; folded a
scheme carve-out + dual capture. Same model family (Codex unavailable), weighed accordingly.

**VERDICT:** ENG CLEARED — ready to implement. Bug fix; CEO/Design review not required.

NO UNRESOLVED DECISIONS
