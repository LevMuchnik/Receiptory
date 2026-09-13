import subprocess
import os
import logging
from datetime import date

from backend.config import get_setting

logger = logging.getLogger(__name__)


def _rclone_env() -> dict:
    """Return env dict with RCLONE_CONFIG pointing to persistent volume."""
    data_dir = os.environ.get("RECEIPTORY_DATA_DIR", "/app/data")
    return {**os.environ, "RCLONE_CONFIG": os.path.join(data_dir, "rclone.conf")}


def upload_backup(backup_dir: str, destination: str, backup_type: str, backup_date: date) -> None:
    """Upload backup directory to rclone destination."""
    remote_path = f"{destination}/{backup_date.isoformat()}-{backup_type}"
    # No --progress: under capture_output nobody watches it, and rclone writes
    # its progress renderer (with terminal control codes) to stderr. That spew
    # ends up in the exception, then in backups.error and the failure
    # notification, where it is worse than useless.
    cmd = ["rclone", "copy", backup_dir, remote_path, "--stats-log-level", "ERROR"]

    logger.info(f"Uploading backup to {remote_path}")
    result = subprocess.run(cmd, capture_output=True, text=True, env=_rclone_env())
    if result.returncode != 0:
        raise RuntimeError(f"rclone upload failed: {result.stderr.strip()}")
    logger.info("Backup upload complete")

    # Sync refreshed tokens back to DB
    _sync_tokens_if_cloud(destination)


def apply_retention(destination: str, data_dir: str) -> None:
    """Delete backups that exceed retention policy.

    Raises RuntimeError if the listing fails or any purge fails. Callers decide
    whether that is fatal: scheduler.run_backup records it against the run
    without demoting an upload that already succeeded, because stale copies left
    on the remote are untidy rather than dangerous.
    """
    retention_daily = get_setting("backup_retention_daily")
    retention_weekly = get_setting("backup_retention_weekly")
    retention_monthly = get_setting("backup_retention_monthly")

    # List remote directories
    cmd = ["rclone", "lsf", destination, "--dirs-only"]
    result = subprocess.run(cmd, capture_output=True, text=True, env=_rclone_env())
    if result.returncode != 0:
        # Raise rather than warn-and-return: a silent return means retention
        # never ran while the run was still recorded as fully healthy, which is
        # the same silence this module is being fixed for.
        raise RuntimeError(
            f"rclone lsf failed for {destination}: {result.stderr.strip()}"
        )

    today = date.today()
    failures: list[str] = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        dirname = line.strip("/")
        try:
            parts = dirname.split("-")
            backup_date = date(int(parts[0]), int(parts[1]), int(parts[2]))
            backup_type = parts[3] if len(parts) > 3 else "daily"
        except (ValueError, IndexError):
            continue

        days_old = (today - backup_date).days
        should_delete = False

        if backup_type == "daily" and days_old > retention_daily:
            should_delete = True
        elif backup_type == "weekly" and days_old > retention_weekly * 7:
            should_delete = True
        elif backup_type == "monthly" and days_old > retention_monthly * 30:
            should_delete = True
        # quarterly: never auto-delete

        if should_delete:
            # Logged as intent, not as fact. The old line said "Deleting" before
            # the call and was never retracted on failure, so the log claimed a
            # deletion that had not happened.
            logger.info(f"Purging expired backup: {dirname}")
            purge = subprocess.run(
                ["rclone", "purge", f"{destination}/{dirname}"],
                capture_output=True, text=True, env=_rclone_env(),
            )
            if purge.returncode != 0:
                # Collected, not raised here: one unpurgeable directory must not
                # stop the sweep and leave every later expired backup in place.
                failures.append(f"{dirname}: {purge.stderr.strip()}")
                logger.error(f"Purge failed for {dirname}: {purge.stderr.strip()}")
            else:
                logger.info(f"Purged expired backup: {dirname}")

    if failures:
        raise RuntimeError(f"rclone purge failed for {len(failures)}: {'; '.join(failures)}")


def _sync_tokens_if_cloud(destination: str) -> None:
    """After rclone operations, sync any refreshed tokens back to DB."""
    try:
        from backend.backup.cloud_auth import sync_token_from_rclone
        for provider in ("gdrive", "onedrive"):
            if destination.startswith(f"receiptory_{provider}:"):
                sync_token_from_rclone(provider)
    except Exception as e:
        logger.debug(f"Token sync skipped: {e}")
