from pathlib import Path
from types import SimpleNamespace

import pytest

from batchmortal.control import AnalysisStopped, STOP_FILE_ENV, check_stop_requested, stop_requested
import main
from batchmortal.browser import ReviewInputError
from batchmortal.results import read_result_rows


def test_stop_signal_uses_explicit_file(monkeypatch, tmp_path):
    stop_file = tmp_path / "stop.flag"
    monkeypatch.setenv(STOP_FILE_ENV, str(stop_file))

    assert stop_requested() is False

    stop_file.touch()

    assert stop_requested() is True
    with pytest.raises(AnalysisStopped):
        check_stop_requested()


def test_stop_signal_is_disabled_without_environment(monkeypatch):
    monkeypatch.delenv(STOP_FILE_ENV, raising=False)

    assert stop_requested() is False
    check_stop_requested()


def test_serial_runner_stops_without_recording_an_error(monkeypatch, tmp_path):
    stop_file = tmp_path / "stop.flag"
    stop_file.touch()
    monkeypatch.setenv(STOP_FILE_ENV, str(stop_file))

    class FakeSB:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback):
            return False

    class FakeAutomator:
        headless = True
        proxy = None

        def __init__(self):
            self.calls = 0

        def analyze_single(self, sb, task):
            self.calls += 1
            raise AssertionError("analysis should not start after a stop request")

    monkeypatch.setattr(main, "SB", FakeSB)
    args = SimpleNamespace(
        output="csv",
        retry=3,
        model_tag="4.1b",
        save_screenshot=False,
        save_local_paipu=False,
        analyze_bad_move_rate=True,
    )
    task = {"uuid": "game-1", "log_prefix": "[1/1 game-1]"}
    automator = FakeAutomator()

    succeeded, failed = main.run_parallel_analysis(
        args,
        [task],
        str(tmp_path / "results.csv"),
        automator,
    )

    assert (succeeded, failed) == (0, 0)
    assert automator.calls == 0
    assert "ERROR" not in (tmp_path / "results.csv").read_text(encoding="utf-8")


def test_serial_runner_does_not_retry_permanently_invalid_game(monkeypatch, tmp_path):
    monkeypatch.delenv(STOP_FILE_ENV, raising=False)

    class FakeSB:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return object()

        def __exit__(self, exc_type, exc, traceback):
            return False

    class FakeAutomator:
        headless = True
        proxy = None

        def __init__(self):
            self.calls = 0

        def analyze_single(self, sb, task):
            self.calls += 1
            raise ReviewInputError("Mortal rejected the game log")

    monkeypatch.setattr(main, "SB", FakeSB)
    args = SimpleNamespace(
        output="csv",
        retry=3,
        model_tag="4.1b",
        save_screenshot=False,
        save_local_paipu=False,
        analyze_bad_move_rate=True,
        target_name="tester",
        source="majsoul",
    )
    task = {
        "uuid": "game-invalid",
        "log_prefix": "[1/1 game-invalid]",
        "source": "majsoul",
        "mode": 12,
        "paipu_url": "https://game.maj-soul.com/1/?paipu=game-invalid_a123",
    }
    automator = FakeAutomator()
    result_path = tmp_path / "results.csv"

    succeeded, failed = main.run_parallel_analysis(
        args,
        [task],
        str(result_path),
        automator,
    )

    assert (succeeded, failed) == (0, 1)
    assert automator.calls == 1
    rows = read_result_rows(str(result_path), "csv")
    assert rows[0]["rating"] == "INVALID"
    assert rows[0]["errorMessage"] == "Mortal rejected the game log"
