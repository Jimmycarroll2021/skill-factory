"""Cross-platform serialized runtime state. Standard library only."""
from contextlib import contextmanager
from pathlib import Path
import json
import os
import sqlite3
import tempfile
import time

SCHEMA = 3


def root():
    return Path(os.environ.get("CLAUDE_PROJECT_DIR", ".")).resolve()


def state():
    return root() / ".claude" / "state"


def load(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def atomic(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = value if isinstance(value, str) else json.dumps(value, indent=2) + "\n"
    fd, name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


@contextmanager
def transaction():
    state().mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(state() / "runtime.sqlite3", timeout=15)
    try:
        db.execute("PRAGMA busy_timeout=15000")
        db.execute("CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.commit()
        db.execute("BEGIN IMMEDIATE")
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def get(db, key, default=None):
    row = db.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else default


def put(db, key, value):
    db.execute("INSERT OR REPLACE INTO kv VALUES (?, ?)", (key, json.dumps(value)))


def increment(db, key):
    value = get(db, key, 0) + 1
    put(db, key, value)
    return value


def active():
    return load(state() / "controller.json", {}).get("schema") == SCHEMA


@contextmanager
def file_lock(name="ledger.lock"):
    state().mkdir(parents=True, exist_ok=True)
    with (state() / name).open("a+b") as f:
        f.seek(0, 2)
        if f.tell() == 0:
            f.write(b"0")
            f.flush()
        until = time.monotonic() + 15
        while True:
            try:
                f.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= until:
                    raise TimeoutError("runtime lock timed out")
                time.sleep(0.02)
        try:
            yield
        finally:
            f.seek(0)
            if os.name == "nt":
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f, fcntl.LOCK_UN)
