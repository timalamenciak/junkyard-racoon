#!/usr/bin/env python3
"""Post the daily digest summary to a Matrix room via the bot daemon's --post-digest flag."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from common.io_utils import OUTPUT_DIR, dump_json, ensure_data_dirs
from common.runtime import is_test_mode


ROOT = Path(__file__).resolve().parent.parent.parent
DAEMON_SCRIPT = ROOT / "matrix-bot-daemon.py"


def main() -> None:
    ensure_data_dirs()

    if is_test_mode():
        print("[post_matrix_digest] test mode — skipping Matrix post")
        dump_json(OUTPUT_DIR / "matrix_post.json", {"status": "skipped", "reason": "test_mode"})
        return

    homeserver = os.getenv("MATRIX_HOMESERVER", "")
    room_id = os.getenv("MATRIX_ROOM_ID", "")

    if not homeserver or not room_id:
        print(
            "[post_matrix_digest] MATRIX_HOMESERVER or MATRIX_ROOM_ID not set — skipping Matrix post",
            file=sys.stderr,
        )
        dump_json(OUTPUT_DIR / "matrix_post.json", {"status": "skipped", "reason": "missing_env"})
        return

    if not DAEMON_SCRIPT.exists():
        print(f"[post_matrix_digest] daemon script not found at {DAEMON_SCRIPT}", file=sys.stderr)
        dump_json(OUTPUT_DIR / "matrix_post.json", {"status": "error", "reason": "daemon_not_found"})
        return

    launcher = shutil.which("py")
    if os.name == "nt" and launcher:
        python_cmd = [launcher, "-3"]
    else:
        python_cmd = [sys.executable]

    result = subprocess.run(
        python_cmd + [str(DAEMON_SCRIPT), "--post-digest"],
        cwd=str(ROOT),
        check=False,
        timeout=60,
    )

    if result.returncode == 0:
        print("[post_matrix_digest] digest posted to Matrix")
        dump_json(OUTPUT_DIR / "matrix_post.json", {"status": "ok"})
    else:
        print(f"[post_matrix_digest] posting failed (exit {result.returncode})", file=sys.stderr)
        dump_json(OUTPUT_DIR / "matrix_post.json", {"status": "error", "exit_code": result.returncode})
        sys.exit(result.returncode)


if __name__ == "__main__":
    main()
