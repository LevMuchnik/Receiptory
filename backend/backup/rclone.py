import subprocess
import os
import logging
from datetime import date

from backend.config import get_setting

logger = logging.getLogger(__name__)


def upload_backup(backup_dir: str, destination: str, backup_type: str, backup_date: date) -> None:
    """Upload backup directory to rclone destination."""
    remote_path = f"{destination}/{backup_date.isoformat()}-{backup_type}"
    cmd = ["rclone", "copy", backup_dir, remote_path, "--progress"]

    logger.info(f"Uploading backup to {remote_path}")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"rclone upload failed: {result.stderr}")
    logger.info("Backup upload complete")


def apply_retention(destination: str, data_dir: str) -> None:
    """Delete backups that exceed retention policy."""
    retention_daily = get_setting("backup_retention_daily")
    retention_weekly = get_setting("backup_retention_weekly")
    retention_monthly = get_setting("backup_retention_monthly")

    # List remote directories
    cmd = ["rclone", "lsf", destination, "--dirs-only"]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.warning(f"Failed to list backups for retention: {result.stderr}")
        return

    today = date.today()
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
            logger.info(f"Deleting expired backup: {dirname}")
            subprocess.run(
                ["rclone", "purge", f"{destination}/{dirname}"],
                capture_output=True,
            )
