#!/usr/bin/env python3
# Calibre-Web Automated – Multi-Library Ingest Router
# SPDX-License-Identifier: GPL-3.0-or-later
#
# Usage: python3 multi_library_ingest.py /cwa-book-ingest/<library-name>/book.epub
#
# Reads the library path from app.db (cwa_library table) by matching the
# parent directory name against registered library names, then delegates
# to the standard ingest_processor.py with CWA_LIBRARY_OVERRIDE set.

import os
import sqlite3
import subprocess
import sys
from pathlib import Path


APP_DB_PATH = os.environ.get("CWA_APP_DB_PATH", "/config/app.db")
INGEST_PROCESSOR = os.path.join(os.path.dirname(__file__), "ingest_processor.py")


def lookup_library_path(library_name: str) -> str | None:
    """Return the registered path for a library by name, or None if not found."""
    try:
        with sqlite3.connect(APP_DB_PATH, timeout=30) as con:
            row = con.execute(
                "SELECT path FROM cwa_library WHERE name = ? AND is_active = 1 LIMIT 1",
                (library_name,),
            ).fetchone()
            return row[0] if row else None
    except sqlite3.OperationalError:
        # Table may not exist on older installs — fall through silently
        return None
    except Exception as e:
        print(f"[multi-library-ingest] ERROR reading app.db: {e}", flush=True)
        return None


def main() -> int:
    if len(sys.argv) < 2:
        print("[multi-library-ingest] Usage: multi_library_ingest.py <filepath>", flush=True)
        return 1

    filepath = sys.argv[1]
    parent_dir = Path(filepath).parent.name  # e.g. "belletristik" from /cwa-book-ingest/belletristik/book.epub

    library_path = lookup_library_path(parent_dir)
    if not library_path:
        print(
            f"[multi-library-ingest] No active library named '{parent_dir}' found in app.db. "
            "Falling back to default ingest (no CWA_LIBRARY_OVERRIDE).",
            flush=True,
        )

    env = os.environ.copy()
    if library_path:
        print(f"[multi-library-ingest] Routing '{filepath}' → library '{parent_dir}' at {library_path}", flush=True)
        env["CWA_LIBRARY_OVERRIDE"] = library_path

    result = subprocess.run(
        [sys.executable, INGEST_PROCESSOR, filepath],
        env=env,
    )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
