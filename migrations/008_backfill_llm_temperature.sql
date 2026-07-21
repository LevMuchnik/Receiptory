-- Issue #11: align llm_temperature default at 1.0.
-- init_settings() persisted the old default (0.0) to the settings table on first
-- run, and config precedence is env > db > default — so changing the DEFAULTS
-- value alone is a no-op for existing installs (the stale db row wins). Rewrite
-- rows still holding the old default so the realignment takes effect. Values are
-- JSON-encoded (json.dumps(0.0) -> '0.0'); match both float and int encodings.
-- Note: this cannot distinguish the auto-seeded default 0.0 from a user who
-- deliberately chose 0.0 for deterministic extraction — both are reset to 1.0.
-- Accepted for this single-user app (the schema has no is-default marker); any
-- other custom temperature (e.g. 0.5) is left untouched.
UPDATE settings
SET value = '1.0',
    updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
WHERE key = 'llm_temperature' AND value IN ('0.0', '0');
