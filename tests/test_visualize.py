import pytest

from batchmortal import visualize
from batchmortal.visualize import prepare_dashboard_data, rolling_average


def make_result(
    index: int,
    *,
    rating: float,
    ai_rate: str = "",
    ai_numerator: str = "",
    ai_denominator: str = "",
) -> dict:
    return {
        "nickname": "测试玩家",
        "source": "majsoul",
        "mode": "16",
        "uuid": f"game-{index}",
        "startTime": f"2026-07-{index:02d} 12:00:00",
        "resultUrl": f"https://example.com/report/{index}",
        "modelTag": "4.1b",
        "rating": str(rating),
        "aiConsistencyRate": ai_rate,
        "aiConsistencyNumerator": ai_numerator,
        "aiConsistencyDenominator": ai_denominator,
    }


def test_rolling_average_requires_a_full_window():
    assert rolling_average([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]

    with pytest.raises(ValueError, match="positive"):
        rolling_average([1, 2], 0)


def test_dashboard_data_keeps_missing_ai_null_and_weights_valid_samples():
    missing_ai = make_result(1, rating=90)
    measured = make_result(
        2,
        rating=92,
        ai_rate="1%",
        ai_numerator="8",
        ai_denominator="10",
    )
    measured.update(
        {
            "badMoveCount5": "1",
            "badMoveCount10": "2",
            "badMoveDenominator": "20",
        }
    )

    data = prepare_dashboard_data([missing_ai, measured])

    assert data is not None
    assert data["points"][0]["aiRate"] is None
    assert data["points"][1]["aiRate"] == pytest.approx(80.0)
    assert data["aiRate"] == pytest.approx(80.0)
    assert data["aiDenominator"] == 10
    assert data["aiWeighted"] is True
    assert data["aiAxisMin"] == 60
    assert data["badRate5"] == pytest.approx(5.0)
    assert data["badRate10"] == pytest.approx(10.0)
    assert data["trendWindow"] is None
    assert data["histogram"] == []


def test_dashboard_data_uses_recent_comparison_and_rolling_trend():
    records = [make_result(index, rating=79 + index) for index in range(1, 21)]

    data = prepare_dashboard_data(records)

    assert data is not None
    assert data["trendWindow"] == 10
    assert data["comparisonWindow"] == 10
    assert data["recentAverage"] == pytest.approx(94.5)
    assert data["comparisonDelta"] == pytest.approx(10.0)
    assert data["ratingRolling"][8] is None
    assert data["ratingRolling"][9] == pytest.approx(84.5)
    assert len(data["histogram"]) == 10


def test_generate_html_contains_professional_sections_and_escapes_nickname(
    monkeypatch,
    tmp_path,
):
    records = [
        make_result(
            index,
            rating=88 + index,
            ai_numerator=str(80 + index),
            ai_denominator="100",
        )
        for index in range(1, 11)
    ]
    monkeypatch.setattr(
        visualize,
        "read_results",
        lambda nickname, output_format, output_root=None: records,
    )
    output_path = tmp_path / "report.html"

    result = visualize.generate_html("<测试玩家>", str(output_path))
    rendered = output_path.read_text(encoding="utf-8")

    assert result == str(output_path)
    assert "&lt;测试玩家&gt;" in rendered
    assert "Rating 推移" in rendered
    assert "AI 一致率推移" in rendered
    assert "Rating 分布" in rendered
    assert "检讨候选" in rendered
    assert "半庄移动平均" in rendered
    assert '"trendWindow":10' in rendered
    assert "观察常见水平" not in rendered
    assert "section-note" not in rendered
    assert "趋势预测线" not in rendered
    assert "linear-gradient" not in rendered
    assert "$payload" not in rendered
