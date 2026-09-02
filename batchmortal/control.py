from __future__ import annotations

import os
from pathlib import Path


STOP_FILE_ENV = "BATCHMORTAL_STOP_FILE"


class AnalysisStopped(RuntimeError):
    """Raised when the desktop app requests a cooperative analysis stop."""


def stop_requested() -> bool:
    stop_file = os.environ.get(STOP_FILE_ENV, "").strip()
    if not stop_file:
        return False
    try:
        return Path(stop_file).is_file()
    except OSError:
        return False


def check_stop_requested() -> None:
    if stop_requested():
        raise AnalysisStopped("Analysis stop requested")
