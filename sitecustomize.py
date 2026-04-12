from __future__ import annotations

import os
import tempfile
from pathlib import Path


def _stable_temp_root() -> str:
    root = Path(r"C:\Users\victo\.codex\memories\scrapperlanas-pytest")
    root.mkdir(parents=True, exist_ok=True)
    return str(root)


_ROOT = _stable_temp_root()

# Force Python and pytest temp usage onto a stable writable location before
# pytest or tempfile cache their default root on Windows.
os.environ.setdefault("TMP", _ROOT)
os.environ.setdefault("TEMP", _ROOT)
os.environ.setdefault("TMPDIR", _ROOT)
tempfile.tempdir = _ROOT
