import asyncio
import logging
import re
from datetime import date, datetime, timezone

from croniter import croniter

from backend.config import get_setting
from backend.database import get_connection
from backend.backup.runner import build_backup
from backend.backup.rclone import upload_backup, apply_retention

logger = logging.getLogger(__name__)


def determine_backup_type(d: date) -> str:
    """Determine the backup type based on the date."""
    if d.month in (1, 4, 7, 10) and d.day == 1:
        return "quarterly"
    if d.day == 1:
        return "monthly"
    if d.weekday() == 6:  # Sunday
        return "weekly"
    return "daily"


async def run_backup_scheduler(data_dir: str) -> None:
    """Background loop that runs backups on schedule."""
    logger.info("Backup scheduler started")
    # Same placement as queue.run_queue_loop's reset_stuck_processing: a row left
    # 'running' by a dead process is resolved at boot, not left in limbo.
    reset_stuck_backups()

    while True:
        try:
            schedule = get_setting("backup_schedule")
            destination = get_setting("backup_destination")

            if not destination:
                await asyncio.sleep(60)
                continue

            now = datetime.now(timezone.utc)
            cron = croniter(schedule, now)
            next_run = cron.get_next(datetime)
            wait_seconds = (next_run - now).total_seconds()

            logger.info(f"Next backup scheduled at {next_run} ({wait_seconds:.0f}s)")
            await asyncio.sleep(wait_seconds)

            await run_backup(data_dir, "scheduled")

        except asyncio.CancelledError:
            logger.info("Backup scheduler shutting down")
            break
        except Exception as e:
            logger.error(f"Backup scheduler error: {e}")
            await asyncio.sleep(300)


# The backup status vocabulary, in one place. These strings are written to
# backups.status, read by BackupPanel, and asserted on in tests; spelled as bare
# literals they drift, and a status the frontend does not know falls through to
# its neutral style and reads as "still running".
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"

# Per-entry cap on recorded error text. rclone stderr can run to kilobytes; this
# text is persisted, returned by /backup/history for 50 rows at a time, and sent
# to Telegram (which rejects a message over 4096 chars and whose sender only logs
# the rejection). The untruncated text always goes to the log.
MAX_ERROR_CHARS = 400


def reset_stuck_backups() -> int:
    """Mark rows a dead process left mid-run as failed.

    Mirrors queue.reset_stuck_processing. Without it a container restart or
    power loss during a backup leaves status='running' forever, which the panel
    renders as in-progress -- a worse lie than the 'completed' this module was
    fixed to stop telling, because it never resolves.
    """
    with get_connection() as conn:
        conn.execute(
            "UPDATE backups SET status = ?, completed_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'), "
            "error = 'interrupted - the process restarted before this run finished' "
            "WHERE status = ?",
            (STATUS_FAILED, STATUS_RUNNING),
        )
        count = conn.execute("SELECT changes()").fetchone()[0]
    if count > 0:
        logger.warning(f"Reset {count} interrupted backup(s) to '{STATUS_FAILED}'")
    return count


# backup_destination is a comma-separated list, and rclone inline connection
# strings are themselves comma-separated (':s3,access_key_id=...,
# secret_access_key=...:bucket'), so such a string is already split into
# fragments before anything here sees it. Redact per fragment: whatever the
# owner pasted, no credential reaches the database or a chat message.
_SECRET_ASSIGNMENT = re.compile(
    r"([\w-]*(?:key|secret|token|password|pass|credential)[\w-]*)\s*=\s*([^,]+)",
    re.IGNORECASE,
)


def _safe_destination(dest: str) -> str:
    """Strip credentials from a destination before storing or sending it."""
    return _SECRET_ASSIGNMENT.sub(r"\1=[redacted]", dest)


def _record(dest: str, err: Exception, suffix: str = "") -> str:
    """One short, credential-free line describing a failed step."""
    text = str(err).strip()
    first_line = text.splitlines()[0] if text else repr(err)
    return f"{_safe_destination(dest)}{suffix}: {first_line[:MAX_ERROR_CHARS]}"


def upload_status(configured: int, uploaded: int) -> str:
    """Backup status from how many destinations took the upload.

    'completed' with no destinations configured is not a lie: an unconfigured
    backup is local by choice, and there is nothing to have failed. The caller
    is responsible for rejecting a destination setting that is non-empty but
    parses to nothing, which is a misconfiguration rather than a choice.
    """
    if configured == 0 or uploaded == configured:
        return STATUS_COMPLETED
    return STATUS_FAILED if uploaded == 0 else STATUS_PARTIAL


def _degraded_message(status: str, uploaded: int, configured: int, error_text: str | None) -> str:
    """Wording for a run that did not fully succeed.

    Branches on the status already computed rather than re-deriving the failure
    mode from `uploaded`, so the two cannot disagree.
    """
    reach = (
        "no destination accepted the upload"
        if status == STATUS_FAILED
        else f"only {uploaded} of {configured} destinations accepted the upload"
    )
    return f"The backup was written locally but {reach}. {error_text}"


def _finish(backup_id: int, status: str, *, size=None, local_path=None, error=None) -> None:
    """Stamp a backup row terminal. One writer, so no path can forget a column."""
    with get_connection() as conn:
        conn.execute(
            """UPDATE backups SET
                status = ?,
                completed_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                size_bytes = COALESCE(?, size_bytes),
                local_path = COALESCE(?, local_path),
                error = ?
            WHERE id = ?""",
            (status, size, local_path, error, backup_id),
        )


def _notify(event: str, payload: dict) -> None:
    try:
        from backend.notifications.notifier import notify
        notify(event, payload)
    except Exception as e:  # a broken notifier must not change the run's outcome
        logger.error(f"Backup notification '{event}' failed to send: {e}")


async def run_backup(data_dir: str, trigger: str = "manual") -> int:
    """Execute a backup. Returns the backup record ID."""
    today = date.today()
    backup_type = trigger if trigger == "manual" else determine_backup_type(today)
    destination = get_setting("backup_destination")

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO backups (backup_type, destination, status) VALUES (?, ?, ?)",
            # Redacted here too. Redacting only the error text while storing the
            # raw setting in the column beside it, which /backup/history returns
            # with SELECT *, would defeat the point.
            (backup_type, _safe_destination(destination or ""), STATUS_RUNNING),
        )
        backup_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    try:
        loop = asyncio.get_event_loop()
        backup_dir, verification = await loop.run_in_executor(None, build_backup, data_dir)

        destinations = [d.strip() for d in (destination or "").split(",") if d.strip()]
        uploaded = 0
        errors: list[str] = []

        # Damaged documents do not stop the backup -- 313 intact documents are
        # worth keeping when the 314th lost its file -- but the owner is told,
        # through the same channel a failed upload uses.
        if verification["problems"]:
            errors.append(
                f"{len(verification['problems'])} document(s) could not be verified: "
                + "; ".join(verification["problems"][:3])
            )

        for dest in destinations:
            try:
                await loop.run_in_executor(None, upload_backup, backup_dir, dest, backup_type, today)
                uploaded += 1
            except Exception as upload_err:
                logger.error(f"Failed to upload to {dest}: {upload_err}")
                errors.append(_record(dest, upload_err))
                continue
            # Retention runs only after that destination's upload succeeded, so a
            # failed run never purges older good backups. A retention failure
            # leaves stale copies on the remote, which is untidy rather than
            # dangerous, so it is reported without demoting the upload.
            try:
                await loop.run_in_executor(None, apply_retention, dest, data_dir)
            except Exception as retention_err:
                logger.error(f"Retention failed for {dest}: {retention_err}")
                errors.append(_record(dest, retention_err, suffix=" (retention)"))

        # os.walk over a tree holding a full copy of storage/, so thousands of
        # stat() calls against NAS disks. Everything else here is already in the
        # executor; on the loop this would stall the whole app.
        size = await loop.run_in_executor(None, _dir_size, backup_dir)

        if destination and not destinations:
            # Set, but nothing usable parsed out of it (", " or whitespace). The
            # owner believes a remote is configured, so this is a failure, not
            # the "local by choice" case.
            status = STATUS_FAILED
            errors.append("backup_destination is set but contains no usable remote")
        else:
            status = upload_status(len(destinations), uploaded)
        error_text = "; ".join(errors) or None

        _finish(backup_id, status, size=size, local_path=backup_dir, error=error_text)

        if status == STATUS_COMPLETED and not error_text:
            logger.info(f"Backup {backup_id} completed ({size} bytes)")
            _notify("backup_ok", {
                "backup_type": backup_type,
                "size_bytes": size,
                "destination": _safe_destination(destination or ""),
            })
        elif status == STATUS_COMPLETED:
            # Uploaded everywhere, but retention failed. Routed to backup_failed
            # anyway: backup_ok is off by default AND format_backup_ok never
            # renders an error, so this would otherwise reach the owner nowhere.
            logger.error(f"Backup {backup_id} completed with warnings: {error_text}")
            _notify("backup_failed", {
                "error": (
                    "The backup uploaded successfully, but clearing expired "
                    f"backups failed. {error_text}"
                ),
            })
        else:
            logger.error(f"Backup {backup_id} {status}: {error_text}")
            _notify("backup_failed", {
                "error": _degraded_message(status, uploaded, len(destinations), error_text),
            })

    except asyncio.CancelledError:
        # CancelledError is a BaseException, so `except Exception` below never
        # sees it. Without this the row stays at 'running' forever on shutdown
        # or a client disconnect, with no notification: a run that did not
        # happen, reported as neither finished nor failed.
        logger.warning(f"Backup {backup_id} cancelled")
        _finish(backup_id, STATUS_FAILED, error="cancelled before the run finished")
        raise

    except Exception as e:
        logger.error(f"Backup {backup_id} failed: {e}")
        _finish(backup_id, STATUS_FAILED, error=str(e)[:MAX_ERROR_CHARS])
        _notify("backup_failed", {"error": str(e)[:MAX_ERROR_CHARS]})

    return backup_id


def _dir_size(path: str) -> int:
    import os
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for f in filenames:
            total += os.path.getsize(os.path.join(dirpath, f))
    return total
