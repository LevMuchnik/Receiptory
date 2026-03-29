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

        if destination:
            await loop.run_in_executor(None, upload_backup, backup_dir, destination, backup_type, today)
            await loop.run_in_executor(None, apply_retention, destination, data_dir)

        size = _dir_size(backup_dir)
        with get_connection() as conn:
            conn.execute(
                """UPDATE backups SET
                    status = 'completed',
                    completed_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now'),
                    size_bytes = ?,
                    local_path = ?
                WHERE id = ?""",
                (size, backup_dir, backup_id),
            )
        logger.info(f"Backup {backup_id} completed ({size} bytes)")
        try:
            from backend.notifications.notifier import notify
            notify("backup_ok", {
                "backup_type": backup_type,
                "size_bytes": size,
                "destination": destination,
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
