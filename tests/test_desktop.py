from matplotlib.figure import Figure
from pathlib import Path

from desktop import (
    MortalDesktopApp,
    _aggregate_percentage,
    _classify_direct_urls,
    _imported_metadata_by_uuid,
    _parse_datetime,
    _matches_mode_filter,
    _record_mode_label,
    _row_percentage,
    _valid_result_rows,
)


def test_mode_filter_does_not_confuse_friend_subtag_with_copper_room():
    ranked = {"recordType": "ranked", "mode": 2}
    friend = {"recordType": "friend", "mode": 2}
    old = {"mode": 2}

    assert _record_mode_label(ranked) == "铜之间·南"
    assert _record_mode_label(friend) == "友人场·南"
    assert _record_mode_label(old) == "未知（2）"
    assert _matches_mode_filter(ranked, "铜之间·南")
    assert not _matches_mode_filter(friend, "铜之间·南")
    assert _matches_mode_filter(friend, "仅友人场")
    assert _matches_mode_filter(old, "未知/旧导入")


class _Value:
    def __init__(self, value):
        self.value = value

    def get(self):
        return self.value


class _Canvas:
    def __init__(self):
        self.drawn = False

    def draw_idle(self):
        self.drawn = True


def _row(index, rating, pt):
    return {
        "nickname": "测试玩家",
        "startTime": f"2026-09-0{index} 12:00:00",
        "modelTag": "4.1b",
        "rating": rating,
        "aiConsistencyNumerator": 70 + index,
        "aiConsistencyDenominator": 100,
        "badMoveCount5": index,
        "badMoveCount10": index + 1,
        "badMoveDenominator": 100,
        "ptDelta": pt,
    }


def test_epoch_timestamp_is_supported():
    assert _parse_datetime(1_700_000_000) is not None
    assert _parse_datetime(1_700_000_000_000) is not None


def test_weighted_rates_and_error_filtering():
    rows = [
        {
            "rating": "88.5",
            "aiConsistencyRate": "1%",
            "aiConsistencyNumerator": 8,
            "aiConsistencyDenominator": 10,
            "badMoveRate10": "1%",
            "badMoveCount10": 2,
            "badMoveDenominator": 20,
        },
        {
            "rating": "ERROR",
            "aiConsistencyNumerator": 100,
            "aiConsistencyDenominator": 100,
        },
        {
            "rating": "INVALID",
            "aiConsistencyNumerator": 100,
            "aiConsistencyDenominator": 100,
        },
    ]

    valid = _valid_result_rows(rows)

    assert len(valid) == 1
    assert _aggregate_percentage(
        valid,
        "aiConsistencyRate",
        "aiConsistencyNumerator",
        "aiConsistencyDenominator",
    ) == 80
    assert _row_percentage(valid[0], "badMoveRate10", "badMoveCount10") == 10


def test_direct_preflight_skips_full_viewpoint_uuid():
    completed_url = "https://game.maj-soul.com/1/?paipu=260901-completed-log_a123456"
    pending_url = "https://game.maj-soul.com/1/?paipu=260902-pending-log_a123456"

    completed, pending = _classify_direct_urls(
        [completed_url, pending_url],
        None,
        {"260901-completed-log_a123456"},
    )

    assert completed == [completed_url]
    assert pending == [pending_url]


def test_direct_preflight_adds_account_viewpoint_before_matching():
    bare_uuid = "260901-completed-log"

    completed, pending = _classify_direct_urls(
        [bare_uuid],
        12345678,
        {"260901-completed-log_a2684071"},
    )

    assert completed == [bare_uuid]
    assert pending == []


def test_imported_metadata_map_keeps_zero_pt_and_matches_viewpoint_uuid():
    metadata = _imported_metadata_by_uuid(
        [
            {
                "uuid": "260901-pt-refresh-test",
                "paipu_url": "260901-pt-refresh-test",
                "mode_id": 12,
                "placement": 3,
                "final_score": 25700,
                "pt_delta": 0,
                "player_level_score": 590,
            }
        ],
        12345678,
    )

    row = metadata["260901-pt-refresh-test_a2684071"]
    assert row["ptDelta"] == 0
    assert row["placement"] == 3
    assert row["finalScore"] == 25700
    assert row["playerLevelScore"] == 590


def test_redraw_builds_three_panel_chart_and_excludes_errors():
    app = object.__new__(MortalDesktopApp)
    app.rows = [_row(1, "82.0", 50), _row(2, "ERROR", -999), _row(3, "88.0", -20)]
    app.view_limit_var = _Value("全部")
    app.figure = Figure(figsize=(10, 7), constrained_layout=True)
    app.canvas = _Canvas()
    app._refresh_table = lambda: None

    app.redraw()

    assert app.canvas.drawn is True
    assert len(app.figure.axes) >= 5
    assert "2 场有效半庄" in app.figure.axes[0].get_title()
    assert any("最终累计 PT +30" in text.get_text() for text in app.figure.axes[4].texts)


class _FakeProcess:
    def __init__(self, pid=1234):
        self.pid = pid
        self.terminated = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True


class _FakeWidget:
    def __init__(self):
        self.options = {}

    def configure(self, **kwargs):
        self.options.update(kwargs)


class _FakeRoot:
    def __init__(self):
        self.after_calls = []

    def after(self, delay, callback):
        self.after_calls.append((delay, callback))


def test_stop_analysis_requests_cooperative_stop_before_force(monkeypatch, tmp_path):
    app = object.__new__(MortalDesktopApp)
    app.process = _FakeProcess()
    app.stop_file_path = Path(tmp_path) / "stop.flag"
    app.stop_requested = False
    app.stop_button = _FakeWidget()
    app.status_var = type("Status", (), {"set": lambda self, value: setattr(self, "value", value)})()
    app.root = _FakeRoot()
    logs = []
    app._log = logs.append
    monkeypatch.setattr("desktop.messagebox.askyesno", lambda *args, **kwargs: True)

    app.stop_analysis()

    assert app.stop_requested is True
    assert app.stop_file_path.exists()
    assert app.process.terminated is False
    assert app.root.after_calls[0][0] == 15_000
    assert "disabled" in str(app.stop_button.options.get("state")).lower()
    assert "安全停止" in app.status_var.value
