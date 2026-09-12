import asyncio
import logging
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


def _upload_status(configured: int, uploaded: int) -> str:
    """Backup status from how many destinations took the upload.

    'completed' with no destinations configured is not a lie: an unconfigured
    backup is local by choice, and there is nothing to have failed.
    """
    if configured == 0 or uploaded == configured:
        return "completed"
    return "failed" if uploaded == 0 else "partial"


async def run_backup(data_dir: str, trigger: str = "manual") -> int:
    """Execute a backup. Returns the backup record ID."""
    today = date.today()
    backup_type = trigger if trigger == "manual" else determine_backup_type(today)
    destination = get_setting("backup_destination")

    with get_connection() as conn:
        conn.execute(
            "INSERT INTO backups (backup_type, destination, status) VALUES (?, ?, 'running')",
            (backup_type, destination),
        )
        backup_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]

    try:
        loop = asyncio.get_event_loop()
        backup_dir = await loop.run_in_executor(None, build_backup, data_dir)

        destinations = [d.strip() for d in (destination or "").split(",") if d.strip()]
        uploaded = 0
        errors: list[str] = []

        for dest in destinations:
            try:
                await loop.run_in_executor(None, upload_backup, backup_dir, dest, backup_type, today)
                uploaded += 1
            except Exception as upload_err:
                logger.error(f"Failed to upload to {dest}: {upload_err}")
                errors.append(f"{dest}: {upload_err}")
                continue
            # Retention runs only after that destination's upload succeeded, so a
            # failed run never purges older good backups. A retention failure
            # leaves stale copies on the remote, which is untidy rather than
            # dangerous, so it is reported without demoting the upload.
            try:
                await loop.run_in_executor(None, apply_retention, dest, data_dir)
            except Exception as retention_err:
                logger.error(f"Retention failed for {dest}: {retention_err}")
                errors.append(f"{dest} (retention): {retention_err}")

        size = _dir_size(backup_dir)
        status = _upload_status(len(destinations), uploaded)
        error_text = "; ".join(errors) or None

        with get_connection() as conn:
            conn.execute(
                """UPDATE backups SET
                    status = ?,
                    completed_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                    size_bytes = ?,
                    local_path = ?,
                    error = ?
                WHERE id = ?""",
                (status, size, backup_dir, error_text, backup_id),
            )

        if status == "completed":
            logger.info(f"Backup {backup_id} completed ({size} bytes)")
        else:
            logger.error(f"Backup {backup_id} {status}: {error_text}")

        try:
            from backend.notifications.notifier import notify
            if status == "completed":
                notify("backup_ok", {
                    "backup_type": backup_type,
                    "size_bytes": size,
                    "destination": destination,
                })
            else:
                # Deliberately backup_failed and not backup_ok. A backup that
                # reached none of its destinations, or only some, is not a
                # success, and notify_*_backup_ok is off by default -- routing a
                # degraded run through the success event would tell the owner
                # nothing at all.
                notify("backup_failed", {
                    "error": (
                        f"Backup was written locally but "
                        f"{'no destination' if uploaded == 0 else f'only {uploaded} of {len(destinations)} destinations'}"
                        f" accepted the upload. {error_text}"
                    ),
                })
        except Exception:
            pass

    except Exception as e:
        logger.error(f"Backup {backup_id} failed: {e}")
        with get_connection() as conn:
            conn.execute(
                """UPDATE backups SET
                    status = 'failed',
                    completed_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                    error = ?
                WHERE id = ?""",
                (str(e), backup_id),
            )
        try:
            from backend.notifications.notifier import notify
            notify("backup_failed", {"error": str(e)})
        except Exception:
            pass

    return backup_id


def _dir_size(path: str) -> int:
    import os
    total = 0
    for dirpath, _, filenames in os.walk(path):
        for f in filenames:
            total += os.path.getsize(os.path.join(dirpath, f))
    return total
