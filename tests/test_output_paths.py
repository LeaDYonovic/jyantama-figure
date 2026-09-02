from pathlib import Path

import pytest

from main import build_output_path


@pytest.mark.parametrize("source", ["majsoul", "tenhou"])
def test_results_are_grouped_by_source(source):
    output_root, output_file = build_output_path("测试 玩家", "xlsx", source)

    root_path = Path(output_root)
    assert root_path.parts[-3:] == ("results", source, "测试_玩家")
    assert Path(output_file) == root_path / "results.xlsx"


def test_results_root_can_be_overridden_for_desktop(monkeypatch, tmp_path):
    monkeypatch.setenv("BATCHMORTAL_RESULTS_ROOT", str(tmp_path))

    output_root, output_file = build_output_path("桌面玩家", "xlsx", "majsoul")

    expected_root = tmp_path / "majsoul" / "桌面玩家"
    assert Path(output_root) == expected_root
    assert Path(output_file) == expected_root / "results.xlsx"
