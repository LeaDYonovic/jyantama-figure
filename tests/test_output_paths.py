from pathlib import Path

import pytest

from main import build_output_path


@pytest.mark.parametrize("source", ["majsoul", "tenhou"])
def test_results_are_grouped_by_source(source):
    output_root, output_file = build_output_path("测试 玩家", "xlsx", source)

    root_path = Path(output_root)
    assert root_path.parts[-3:] == ("results", source, "测试_玩家")
    assert Path(output_file) == root_path / "results.xlsx"
