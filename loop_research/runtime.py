"""Identify owned Linux processes without mistaking a reused PID for live work."""

from pathlib import Path


def process_identity(pid):
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError):
        return None


def process_alive(pid, identity):
    if not (pid and identity and process_identity(pid) == identity):
        return False
    try:
        return Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0] != "Z"
    except (OSError, IndexError):
        return False
