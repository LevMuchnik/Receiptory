-- Migration 006: Scanner test-frame capture for the scanner lab
-- Stores raw camera frames from the scanner so different detectors and
-- parameter sets can be evaluated against a fixed corpus.

CREATE TABLE IF NOT EXISTS scanner_test_frames (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    frame_path TEXT NOT NULL,
    captured_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    detector_name TEXT,
    corners_at_capture_json TEXT,
    ground_truth_json TEXT,
    notes TEXT
);

CREATE INDEX IF NOT EXISTS idx_scanner_test_frames_captured_at
    ON scanner_test_frames(captured_at);
