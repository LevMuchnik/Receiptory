# Plan: DB-managed LLM API keys (issue #25)

Move LLM provider API keys entirely into the DB as a named, UI-editable list.
Retire the env-based `RECEIPTORY_LLM_API_KEY_<LABEL>` named-key scheme. Keep the
single legacy `RECEIPTORY_LLM_API_KEY` as a provider-agnostic fallback.

## Decisions (from eng review 2026-07-22)

1. **Key plumbing — dedicated endpoints, `llm_api_keys` NOT in DEFAULTS.**
   Generic `GET /settings` and `PATCH /settings` never see key material, so they
   can neither leak it nor clobber it. `llm_api_key_ref` stays in DEFAULTS (it is
   a non-secret name).
2. **Migration — auto-import on startup** (once, when the DB list is empty).
3. **Integrity — override model.** Names unique; add-with-existing-name replaces
   the value (rotate). Delete removes the DB entry and, if it was selected,
   clears the ref. Resolution falls back to the legacy env key, then empty.
4. **Env fallback — single legacy key only.** `RECEIPTORY_LLM_API_KEY_<LABEL>`
   scheme is retired. Env is read once at startup to seed the DB, then ignored.

## Data model

```
setting "llm_api_keys"  (NOT in DEFAULTS; raw DB row, default [])
  = [ { "name": "Gemini", "key": "AIza..." },
      { "name": "OpenAI", "key": "sk-proj-..." } ]

setting "llm_api_key_ref"  (in DEFAULTS, default "")  = "Gemini"   # selected name
```

## Resolution (backend/config.py::resolve_llm_api_key)

```
                        ┌─────────────────────────────────────────┐
                        │ resolve_llm_api_key()                    │
                        └─────────────────────────────────────────┘
                                        │
             ref = get_setting("llm_api_key_ref")   # in DEFAULTS
                                        │
              ┌─────────────────────────┴─────────────────────────┐
        ref non-empty AND                                    otherwise
        name found in db llm_api_keys                             │
              │                                                   │
        return entry.key   ◄── DB WINS over env      legacy = RECEIPTORY_LLM_API_KEY
        (inverted env>db, intentional:                    (env > db, get_setting)
         UI is authoritative for keys)                          │
                                                        legacy ? return legacy : ""
```

Note the **inverted precedence**: for keys specifically, a selected DB entry wins
over env. Everywhere else the app is env > db. This is deliberate — the UI must be
authoritative so an `.env` value can't silently mask a UI edit. Add a code comment
saying so (this is a known past-pain class in this repo).

**Single source of truth (finding #5).** The legacy `llm_api_key` DEFAULTS entry is
retired — it is no longer written and no longer read as a DB value; the only DB
home for key material is `llm_api_keys`. The legacy path reads *env only*
(`RECEIPTORY_LLM_API_KEY`), so there is exactly one DB representation of keys.
Remove `llm_api_key` from DEFAULTS and from `SENSITIVE_KEYS` masking.

**Narrow the try/except (finding #7).** The `get_setting("llm_api_key_ref")` guard
must catch only the DB-unavailable case, not swallow every exception — a transient
error should not silently fall through to the (now env-only) legacy key and send a
wrong provider's key. Wrap only the ref+list read; let unexpected errors surface.

## Startup migration (backend/config.py, called from init path)

```
if not get_setting("llm_api_keys_migrated"):          # dedicated flag, NOT empty-list
    imported = []
    # legacy single key -> name inferred from the current model's provider
    if env RECEIPTORY_LLM_API_KEY:
        name = provider_of(get_setting("llm_model")) or "default"   # e.g. "gemini" -> "Gemini"
        imported += [{name, key: env RECEIPTORY_LLM_API_KEY}]
    # named env keys -> LABEL as name  (read ONCE; scheme retired after)
    for LABEL, val in RECEIPTORY_LLM_API_KEY_<LABEL> in env:
        imported += [{name: LABEL, key: val}]     # dedupe names, first wins
    if imported:
        set_setting("llm_api_keys", imported)
        if not get_setting("llm_api_key_ref"):
            # select the imported key whose name/provider matches the model, else first
            set_setting("llm_api_key_ref", best_match or imported[0].name)
    set_setting("llm_api_keys_migrated", True)        # set regardless of import result
```

**Idempotency via a dedicated `llm_api_keys_migrated` flag, not an empty list**
(outside-voice finding #2). An empty-list guard is wrong: after the user imports
keys and then deletes them all in the UI, the list is empty again — so on the
next restart the still-present `.env` values would silently re-seed. The flag is
set once (even when nothing was imported) and never re-runs. `llm_api_keys_migrated`
is NOT in DEFAULTS and is read via `_get_raw_setting` (bool, default False).

## API (backend/api/settings.py)

- `GET  /settings/llm-api-keys` → `{ keys: [{name, last4}], selected, model, model_provider, legacy_env_key_set }`
  (never returns key material; `last4` = last 4 chars for UI disambiguation).
- `POST /settings/llm-api-keys` body `{name, key}` → add or **replace** by name.
  Validation (finding #6): trim `name`; reject empty `name` or empty `key` (400);
  **case-insensitive name match** so "OpenAI" replaces "openai" rather than
  creating a collision (store the first-seen casing, or the newest — pick newest so
  a re-add can fix casing). Returns updated `{keys, selected}`.
- `DELETE /settings/llm-api-keys/{name}` → remove entry (case-insensitive match);
  if it was the selected ref, clear ref. Returns updated `{keys, selected}`.
- `PUT  /settings/llm-api-keys/selected` body `{name|""}` → set ref; validate the
  name exists in the list (empty clears, 400 on unknown name). A dedicated setter
  (not PATCH /settings) so we can validate membership.
- Remove env-scan branch from the existing endpoint; `legacy_env_key_set` is now
  `bool(RECEIPTORY_LLM_API_KEY in env)` for the read-only fallback hint, decoupled
  from the retired label scheme.
- **All four mutating reads+writes run inside a single `get_connection()`
  transaction** (finding #4) so the read-modify-write on the JSON list can't
  interleave under FastAPI's sync-endpoint threadpool.

## Removed code

- `backend/config.py`: `_NAMED_API_KEY_RE`, `list_named_api_keys()`, the per-label
  env lookup inside `resolve_llm_api_key`.
- `backend/api/settings.py`: import of `list_named_api_keys`; env-label payload.
- `.env` / `.env.example`: the `RECEIPTORY_LLM_API_KEY_<LABEL>` lines and docs.
- `frontend`: the "(not found in .env)" option and env-label picker branch.

## Frontend (frontend/src/pages/SettingsPage.tsx)

Settings → LLM Engine, replace the env-label picker with a key manager:
- List rows: `name` · `••••last4` · [radio: active] · [Delete].
- "Add / update key" row: name input + key input (type=password) + Save
  (POST). Same name replaces.
- Active selector writes the ref (PUT selected).
- Keep a read-only "legacy env key detected" hint when `RECEIPTORY_LLM_API_KEY`
  is set and no DB key is selected (so the fallback state is visible).
- All key reads/writes go through `/settings/llm-api-keys`, never generic settings.

## DB write concurrency

`mutate_llm_api_keys` does read-modify-write on the JSON list. `get_connection`
opens a fresh connection with NO process-level lock and a DEFERRED transaction
(SELECT takes no write lock), so wrapping the read+write in one `with` block is
NOT sufficient — two threadpool callers could read the same snapshot and lose an
update. The mutation issues `BEGIN IMMEDIATE` so the write lock is taken up front
and the RMW is genuinely serialized. (Caught in /review adversarial pass; the
single-user UI makes the race unrealistic, but the fix makes the claim true.)

## NOT in scope

- Encrypting key material at rest in the DB, or excluding it from backups. Tracked
  as a TODO (below); accepted for now on a single-user NAS backing up to the
  owner's own Drive.
- Per-model automatic key selection by litellm. The app resolves exactly one key
  and passes it explicitly; the user picks which via the ref.
- Multiple keys per provider / key rotation history. One named entry per name.

## What already exists (reused, not rebuilt)

- `settings` table + `get_setting`/`set_setting` JSON upsert — the storage layer.
- `scanner_active_config` — precedent for a structured-JSON setting.
- `llm_api_key_ref` setting + Settings → LLM Engine picker UI — repurposed from the
  env-label scheme to select among DB entries.
- `get_connection()` context manager with per-context commit + threading lock.
- Write-only secret handling pattern (GET masks, dedicated mutators) from #13.
- `resolve_llm_api_key()` call site in `litellm.completion(api_key=...)` — unchanged
  signature, new internals.

## Failure modes

| Condition | Behavior |
|---|---|
| ref set, name missing from list (deleted) | fall through to legacy env key, else "" |
| list empty, no env key | resolve returns "" → `test-llm`/processing raises "No API key configured" |
| DB unavailable during resolve | narrow guard → legacy env read; unexpected errors surface (not swallowed) |
| POST empty name or key | 400, list unchanged |
| POST name case-collision | replaces existing entry, does not duplicate |
| user deletes all keys, env still set | flag already set → no re-seed; resolve uses env legacy |
| restart after migration | flag set → migration skipped entirely |

## TODOS

- [ ] **Secrets in DB backups** — `backend/backup/runner.py:23` copies raw
  `receiptory.db`, so DB-stored keys leave the box in every backup (`.env` never
  did). Accepted for single-user NAS now; revisit with column-level encryption or a
  backup-time key scrub if the threat model changes.

## GSTACK REVIEW REPORT

**Verdict:** APPROVE WITH CHANGES → all folded in above. Ready to implement.

**Scope challenge (Step 0):** Simpler alternative considered — keep keys in `.env`,
add only a UI *selector*. Rejected: the user's explicit goal is UI-editable keys
with no new env vars, and a selector-only design can't add a key without editing
`.env`. Current scope is the minimum that meets the intent.

**Architecture (4 decisions, locked with user):**
1. Dedicated endpoints; `llm_api_keys` NOT in DEFAULTS → generic GET/PATCH can't
   leak or clobber key material. ✔
2. Auto-import from env on first startup, gated by `llm_api_keys_migrated` flag. ✔
3. Override integrity model — unique (case-insensitive) names, add replaces,
   delete clears ref if selected. ✔
4. Single legacy env key only; `_<LABEL>` scheme retired. ✔

**Code Quality:** add `_get_raw_setting(key, default)` helper for non-DEFAULTS DB
reads (`llm_api_keys`, `llm_api_keys_migrated`); keeps `get_setting` semantics
(env>db>default over DEFAULTS) uncorrupted.

**Test Coverage (required):**
- `resolve` env-inversion: selected DB key wins over `RECEIPTORY_LLM_API_KEY`.
- `resolve` fallback chain: ref-missing → legacy env → "".
- **Leak guard:** `GET /settings` payload contains no key material; `GET
  /settings/llm-api-keys` returns only name+last4, never full keys.
- Migration idempotency: second `init` does not re-seed after keys deleted.
- Migration seeding: legacy + named env keys imported once, ref auto-selected.
- POST: add, replace-by-name, case-insensitive replace, empty-name 400, empty-key 400.
- DELETE: removes entry, clears ref when it was selected.
- PUT selected: sets ref, 400 on unknown name, "" clears.

**Performance:** read-modify-write on JSON list wrapped in one transaction per
mutation (finding #4). No hot-path cost — resolve does one indexed settings read.

**Outside-voice findings folded:** #1 (backups → TODO, accepted), #2 (migration
flag), #4 (transaction), #5 (single source of truth), #6 (POST validation +
case-norm), #7 (narrow try/except). #3/#8 no action needed.

## Implementation tasks

1. `backend/config.py`: remove `_NAMED_API_KEY_RE`, `list_named_api_keys`, `llm_api_key`
   from DEFAULTS + SENSITIVE_KEYS. Add `_get_raw_setting`, `llm_api_keys` accessors,
   `provider_of` helper, rewrite `resolve_llm_api_key` (narrow guard), add
   `migrate_llm_api_keys()` (flag-gated). Add `llm_api_keys_migrated` handling.
2. `backend/main.py` (or init path): call `migrate_llm_api_keys()` after `init_settings()`.
3. `backend/api/settings.py`: rewrite `GET /settings/llm-api-keys`; add `POST`,
   `DELETE /{name}`, `PUT /selected`; drop `list_named_api_keys` import.
4. `backend/models.py`: add request models (`ApiKeyCreate{name,key}`, `SelectedUpdate{name}`).
5. `frontend/src/pages/SettingsPage.tsx`: replace env-label picker with key-manager
   rows (name · ••••last4 · radio · delete) + add/update row + legacy-hint.
6. `.env` / `.env.example`: remove `RECEIPTORY_LLM_API_KEY_OPENAI` line + label docs.
7. Tests: `tests/test_config.py`, `tests/test_settings_api.py` per coverage list.
