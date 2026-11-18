"""Automated Snapshot & Backup Utility for K-Pop App.

Creates an atomic, timestamped backup of the production database and all core
source files inside the backups/ directory.

Usage:
    python backup.py "Optional label or Backlog item description"
"""

import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

import database

BASE_DIR = database.DATA_DIR
BACKUP_DIR = BASE_DIR / "backups"

CORE_FILES = [
    "app.py",
    "database.py",
    "fetcher.py",
    "schema.sql",
    "static/app.js",
    "templates/index.html",
    "GEMINI.md",
    "BACKLOG.md",
]


def create_snapshot(label: str = "") -> Path:
    """Creates an atomic, timestamped snapshot containing the database and core source files.

    Saves a complete snapshot to `backups/snapshots/{timestamp}_{label}/` and maintains
    a flat latest copy in `backups/`.

    Args:
        label: Optional descriptive text or backlog task identifier appended to the directory name.

    Returns:
        Path: Absolute path to the created snapshot directory.
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    sanitized_label = re.sub(r"[^a-zA-Z0-9_\-]+", "_", label).strip("_") if label else ""
    folder_name = f"{timestamp}_{sanitized_label}" if sanitized_label else timestamp
    snapshot_dir = BACKUP_DIR / "snapshots" / folder_name

    snapshot_dir.mkdir(parents=True, exist_ok=True)
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)

    # Flush SQLite WAL journal into main database before taking snapshot
    try:
        database.checkpoint_wal()
    except Exception as e:
        print(f"⚠️ Warning: WAL checkpoint failed before backup: {e}")

    copied = []

    # 1. Back up active database dynamically using database.DB_NAME
    db_path = Path(database.DB_NAME)
    if not db_path.is_absolute():
        db_path = BASE_DIR / db_path

    if db_path.exists():
        dest = snapshot_dir / db_path.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(db_path, dest)

        mirror_dest = BACKUP_DIR / db_path.name
        try:
            shutil.copy2(db_path, mirror_dest)
        except Exception:
            pass

        copied.append(db_path.name)

    # 2. Back up core application code and documentation
    for rel_path in CORE_FILES:
        src = BASE_DIR / rel_path
        if not src.exists():
            continue

        # Target in timestamped snapshot folder
        dest = snapshot_dir / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)

        # Also maintain latest flat mirror in backups/
        mirror_dest = BACKUP_DIR / src.name
        try:
            shutil.copy2(src, mirror_dest)
        except Exception:
            pass

        copied.append(rel_path)

    print(f"✅ Snapshot created at: backups/snapshots/{folder_name}")
    print(f"   Files backed up ({len(copied)}): {', '.join(copied)}")
    return snapshot_dir


if __name__ == "__main__":
    lbl = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "manual"
    create_snapshot(lbl)
