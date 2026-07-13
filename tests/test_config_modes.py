import pytest

from batchmortal.config import (
    normalize_source_mode,
    resolve_mode_config,
    source_for_mode,
)


CONFIG = {
    "mode": "mj",
    "review_language": "zh-CN",
    "mj": {
        "nickname": "雀魂玩家",
        "limit": 70,
        "modes": "12",
    },
    "th": {
        "nickname": "天凤玩家",
        "limit": 10,
        "modes": "4p-south",
    },
}


def test_mj_and_th_sections_are_selected_exclusively():
    mode, source, selected = resolve_mode_config(CONFIG)
    assert (mode, source) == ("mj", "majsoul")
    assert selected["nickname"] == "雀魂玩家"
    assert selected["modes"] == "12"

    mode, source, selected = resolve_mode_config(CONFIG, requested_mode="th")
    assert (mode, source) == ("th", "tenhou")
    assert selected["nickname"] == "天凤玩家"
    assert selected["modes"] == "4p-south"


def test_numeric_and_long_source_aliases_remain_supported():
    assert normalize_source_mode(0) == "mj"
    assert normalize_source_mode(1) == "th"
    assert normalize_source_mode("majsoul") == "mj"
    assert normalize_source_mode("tenhou") == "th"
    assert source_for_mode("th") == "tenhou"


def test_legacy_source_config_is_still_readable():
    mode, source, selected = resolve_mode_config(
        {
            "source": "tenhou",
            "nickname": "旧配置玩家",
            "tenhou_modes": "4p",
        }
    )
    assert (mode, source) == ("th", "tenhou")
    assert selected["nickname"] == "旧配置玩家"
    assert selected["modes"] == "4p"


def test_conflicting_config_selectors_are_rejected():
    with pytest.raises(ValueError, match="both 'mode' and legacy 'source'"):
        resolve_mode_config({"mode": "mj", "source": "tenhou"})

    with pytest.raises(ValueError, match="both 'mode' and legacy 'source'"):
        resolve_mode_config({"mode": "mj", "source": "majsoul"})


def test_source_section_must_be_a_mapping():
    with pytest.raises(ValueError, match="section 'th'"):
        resolve_mode_config({"mode": "th", "th": "not-a-mapping"})
