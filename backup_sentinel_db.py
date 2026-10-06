"""Administrator-run online SQLite backup; does not print database contents."""
import argparse
import os
import sqlite3
from pathlib import Path

def backup(source: Path, destination: Path) -> None:
    if not source.is_file():
        raise FileNotFoundError(source)
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True, timeout=30) as src:
            with sqlite3.connect(destination) as dst:
                src.backup(dst, pages=256, sleep=0.1)
                if dst.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                    raise RuntimeError("Backup integrity check failed")
    except BaseException:
        destination.unlink(missing_ok=True)
        raise

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    args = parser.parse_args()
    backup(args.source, args.destination)
    print("SQLite backup completed; integrity check passed.")
