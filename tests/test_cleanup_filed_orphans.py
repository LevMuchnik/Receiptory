"""Tests for scripts/cleanup_filed_orphans.py.

This script had no tests and produced nine defects across two review rounds,
four of them able to destroy data that has no other copy. It deletes files the
user cannot get back, so its classification and its safety guards are pinned
here rather than argued about in comments.

Note scripts/ is not in the Docker image (the Dockerfile copies backend/,
migrations/ and frontend/dist only), so this runs from a checkout, like the
script itself.
"""

import os
import sqlite3
import sys

import pytest

from scripts.cleanup_filed_orphans import main


def _install(tmp_path, rows, filed, originals=()):
    """A data dir shaped like the real one: a database, filed/, originals/."""
    d = tmp_path / "data"
    (d / "storage" / "filed").mkdir(parents=True)
    (d / "storage" / "originals").mkdir(parents=True)
    conn = sqlite3.connect(str(d / "receiptory.db"))
    conn.execute("CREATE TABLE documents (id INTEGER PRIMARY KEY, stored_filename TEXT)")
    conn.executemany(
        "INSERT INTO documents (stored_filename) VALUES (?)", [(r,) for r in rows]
    )
    conn.commit()
    conn.close()
    for name, data in filed.items():
        (d / "storage" / "filed" / name).write_bytes(data)
    import hashlib
    for name in originals:
        # The filename must BE the sha256 of the contents: the delete gate now
        # hashes the original instead of trusting its name.
        body = name.encode()
        real = hashlib.sha256(body).hexdigest()
        (d / "storage" / "originals" / (real + ".pdf")).write_bytes(body)
    return d


def _run(monkeypatch, d, *extra):
    monkeypatch.setattr(sys, "argv", ["cleanup", "--data-dir", str(d), *extra])
    return main()


def _filed(d):
    return sorted(os.listdir(d / "storage" / "filed"))


def test_a_dry_run_changes_nothing_at_all(monkeypatch, tmp_path):
    """Including the database side. An earlier version opened the live database
    read-only, which CREATES receiptory.db-shm, and then deleted it again --
    on dry runs too, contradicting the docstring."""
    d = _install(
        tmp_path,
        ["2026-01-01-A-aaaaaaaa.pdf"],
        {"2026-01-01-A-aaaaaaaa.pdf": b"live", "2019-01-01-A-aaaaaaaa.pdf": b"live"},
        ["aaaaaaaa1111.pdf"],
    )

    assert _run(monkeypatch, d) == 0

    assert _filed(d) == ["2019-01-01-A-aaaaaaaa.pdf", "2026-01-01-A-aaaaaaaa.pdf"]
    assert not (d / "storage" / "quarantine").exists()
    assert not (d / "receiptory.db-shm").exists(), "a dry run wrote into data/"
    assert not (d / "receiptory.db-wal").exists()


def test_a_directory_in_filed_is_ignored_not_hashed(monkeypatch, tmp_path):
    """backup/verify.py filters filed/ to files and has a test for it. This
    script must agree: a directory in the orphan set reaches _sha256 and raises
    IsADirectoryError, aborting the run AFTER printing a count."""
    d = _install(tmp_path, ["a-aaaaaaaa.pdf"], {"a-aaaaaaaa.pdf": b"x"})
    (d / "storage" / "filed" / "a_subdir").mkdir()

    assert _run(monkeypatch, d) == 0


def test_a_live_file_is_never_orphaned_by_a_non_basename_row(monkeypatch, tmp_path):
    """The orphan set is on_disk minus referenced. `referenced` holds raw
    stored_filename values; on_disk holds basenames. A row storing
    "filed/x.pdf" would therefore fail to match its OWN file, putting a live
    file in the orphan set to be quarantined or deleted."""
    d = _install(
        tmp_path,
        ["filed/2026-01-01-A-aaaaaaaa.pdf"],
        {"2026-01-01-A-aaaaaaaa.pdf": b"live"},
        ["aaaaaaaa1111.pdf"],
    )

    assert _run(monkeypatch, d) == 0
    assert _filed(d) == ["2026-01-01-A-aaaaaaaa.pdf"], "a live file was classified"


def test_apply_refuses_when_a_row_is_not_a_plain_filename(monkeypatch, tmp_path, capsys):
    """If any row's path cannot be resolved, the script cannot tell that row's
    file apart from an orphan, so it must not delete anything."""
    d = _install(
        tmp_path,
        ["/etc/shadow", "2026-01-01-A-aaaaaaaa.pdf"],
        {"2026-01-01-A-aaaaaaaa.pdf": b"live", "2019-01-01-B-bbbbbbbb.pdf": b"orphan"},
    )

    assert _run(monkeypatch, d, "--apply") == 2
    assert "2019-01-01-B-bbbbbbbb.pdf" in _filed(d), "deleted despite refusing"
    assert "REFUSING" in capsys.readouterr().err


def test_apply_refuses_while_the_app_is_mid_operation(monkeypatch, tmp_path, capsys):
    """receiptory.db-shm exists while a connection is open. Deleting inside
    build_backup's copytree discards the whole nightly run."""
    d = _install(
        tmp_path,
        ["2026-01-01-A-aaaaaaaa.pdf"],
        {"2026-01-01-A-aaaaaaaa.pdf": b"live", "2019-01-01-B-bbbbbbbb.pdf": b"orphan"},
    )
    (d / "receiptory.db-shm").write_bytes(b"")

    assert _run(monkeypatch, d, "--apply") == 2
    assert "2019-01-01-B-bbbbbbbb.pdf" in _filed(d)
    assert "REFUSING" in capsys.readouterr().err


def test_a_duplicate_is_deleted_only_when_its_original_survives(monkeypatch, tmp_path):
    """The docstring justifies deleting a duplicate by "originals/ holds the
    source". With no original the bytes must be kept, not deleted."""
    d = _install(
        tmp_path,
        ["2026-01-01-A-aaaaaaaa.pdf"],
        {"2026-01-01-A-aaaaaaaa.pdf": b"same", "2019-01-01-A-aaaaaaaa.pdf": b"same"},
        # deliberately NO originals/aaaaaaaa*
    )

    assert _run(monkeypatch, d, "--apply") == 0
    assert (d / "storage" / "quarantine" / "2019-01-01-A-aaaaaaaa.pdf").exists(), (
        "a duplicate with no original was deleted instead of quarantined"
    )


def test_an_unparseable_hash_prefix_does_not_match_every_original(monkeypatch, tmp_path):
    """glob(originals/ + "" + "*") matches EVERYTHING, so an orphan named
    "-.pdf" would pass the "original is present" precondition vacuously and be
    deleted. The prefix must be validated before it is trusted."""
    d = _install(
        tmp_path,
        ["2026-01-01-A-aaaaaaaa.pdf"],
        {"2026-01-01-A-aaaaaaaa.pdf": b"same", "-.pdf": b"same"},
        ["aaaaaaaa1111.pdf"],
    )

    assert _run(monkeypatch, d, "--apply") == 0
    assert (d / "storage" / "quarantine" / "-.pdf").exists(), (
        "an unparseable name was deleted on a vacuous originals/ match"
    )


def test_quarantine_never_overwrites_an_earlier_artifact(monkeypatch, tmp_path, capsys):
    """A quarantined file has no copy in any backup tree. shutil.move is
    os.rename on one filesystem, which clobbers silently, so a second run
    would destroy the only surviving copy."""
    d = _install(tmp_path, [], {"2019-01-01-B-bbbbbbbb.pdf": b"second"})
    (d / "storage" / "quarantine").mkdir()
    (d / "storage" / "quarantine" / "2019-01-01-B-bbbbbbbb.pdf").write_bytes(b"first, precious")

    assert _run(monkeypatch, d, "--apply") == 0

    assert (d / "storage" / "quarantine" / "2019-01-01-B-bbbbbbbb.pdf").read_bytes() == b"first, precious"
    assert "SKIPPED" in capsys.readouterr().out


def test_a_rename_committed_only_to_the_wal_is_still_visible(monkeypatch, tmp_path):
    """The script reads a COPY of the database, and copies the -wal with it.

    Opening with immutable=1 disables WAL reading entirely -- measured: a
    committed-but-not-checkpointed row is invisible. A reprocess minutes before
    the run would then look unreferenced and its live file would be destroyed.
    """
    d = _install(tmp_path, [], {"2026-09-14-NEW-cccccccc.pdf": b"just renamed"})
    conn = sqlite3.connect(str(d / "receiptory.db"))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("INSERT INTO documents (stored_filename) VALUES (?)",
                 ("2026-09-14-NEW-cccccccc.pdf",))
    conn.commit()          # in the -wal, not yet checkpointed
    assert (d / "receiptory.db-wal").exists()

    rc = _run(monkeypatch, d, "--apply")
    conn.close()

    assert rc == 2, "the app was live (shm present); it must refuse"
    assert "2026-09-14-NEW-cccccccc.pdf" in _filed(d)


def test_a_victim_that_became_referenced_during_the_scan_is_not_deleted(
    monkeypatch, tmp_path
):
    """The plan is computed from a snapshot, then ~160MB of hashing happens, then
    deletes start. A save_filed in that window produces a file that is LIVE but
    absent from the stale snapshot -- which the plan already classified as an
    orphan. restore_backup.py learned this first: "Re-check immediately before
    the swap. The first check was minutes ago." """
    import scripts.cleanup_filed_orphans as mod

    d = _install(
        tmp_path,
        ["2026-01-01-A-aaaaaaaa.pdf"],
        {"2026-01-01-A-aaaaaaaa.pdf": b"live", "2019-01-01-B-bbbbbbbb.pdf": b"orphan"},
    )

    # First read returns the stale set; the re-check sees the row that landed
    # while we were hashing.
    real = mod._read_referenced
    calls = {"n": 0}

    def racing(db_path):
        calls["n"] += 1
        out = real(db_path)
        if calls["n"] > 1:
            out = out | {"2019-01-01-B-bbbbbbbb.pdf"}
        return out

    monkeypatch.setattr(mod, "_read_referenced", racing)

    assert _run(monkeypatch, d, "--apply") == 2
    assert calls["n"] >= 2, "the plan was never re-checked before deleting"
    assert "2019-01-01-B-bbbbbbbb.pdf" in _filed(d), (
        "a file that became referenced during the scan was destroyed"
    )


def test_a_duplicate_whose_original_is_truncated_is_not_deleted(monkeypatch, tmp_path):
    """originals/<name> IS its own sha256, so a file left truncated by a crashed
    copy has the right NAME and the wrong bytes. Globbing the name would let it
    justify an irreversible delete; the gate hashes it."""
    d = _install(
        tmp_path,
        ["2026-01-01-A-aaaaaaaa.pdf"],
        {"2026-01-01-A-aaaaaaaa.pdf": b"same", "2019-01-01-A-aaaaaaaa.pdf": b"same"},
    )
    # Right name, wrong contents.
    (d / "storage" / "originals" / ("aaaaaaaa" + "0" * 56 + ".pdf")).write_bytes(b"TRUNCATED")

    assert _run(monkeypatch, d, "--apply") == 0
    assert (d / "storage" / "quarantine" / "2019-01-01-A-aaaaaaaa.pdf").exists(), (
        "a duplicate was deleted on the strength of a truncated original"
    )


def test_quarantine_lives_inside_a_backed_up_tree(monkeypatch, tmp_path):
    """The quarantined file is by definition the one the script could not prove
    is reproducible. It must not also be the one with no backup copy."""
    from backend.backup.runner import BACKUP_TREES, EXCLUDED_STORAGE_DIRS

    d = _install(tmp_path, [], {"2019-01-01-B-bbbbbbbb.pdf": b"only copy"})
    assert _run(monkeypatch, d, "--apply") == 0

    dest = d / "storage" / "quarantine" / "2019-01-01-B-bbbbbbbb.pdf"
    assert dest.exists()
    assert "storage" in BACKUP_TREES
    assert "quarantine" not in EXCLUDED_STORAGE_DIRS, (
        "quarantine is inside storage/ but excluded from the copy"
    )
