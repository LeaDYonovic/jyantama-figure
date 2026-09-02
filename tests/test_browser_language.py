import pytest

from batchmortal.browser import (
    BrowserAutomator,
    ReviewInputError,
    _review_input_error_reason,
    build_review_url,
    normalize_review_language,
    normalize_review_ui,
)


def test_normalize_review_language():
    assert normalize_review_language(None) == "zh-CN"
    assert normalize_review_language("zh-cn") == "zh-CN"
    assert normalize_review_language("zh_CN") == "zh-CN"
    assert normalize_review_language("en") == "en"
    assert normalize_review_language("jp") == "ja"
    assert normalize_review_language("ko-KR") == "ko"


def test_build_review_url():
    assert build_review_url("zh-CN") == "https://mjai.ekyu.moe/zh-cn.html"
    assert build_review_url("en") == "https://mjai.ekyu.moe/"
    assert build_review_url("ja") == "https://mjai.ekyu.moe/ja.html"
    assert build_review_url("ko") == "https://mjai.ekyu.moe/ko.html"


def test_normalize_review_ui():
    assert normalize_review_ui(None) == "classic"
    assert normalize_review_ui("classic") == "classic"
    assert normalize_review_ui("killerducky") == "killerducky"
    assert normalize_review_ui("killer-ducky") == "killerducky"
    assert normalize_review_ui("kd") == "killerducky"


def test_normalize_review_ui_rejects_unknown_values():
    with pytest.raises(ValueError, match="Unsupported review UI"):
        normalize_review_ui("foo")


def test_mortal_validation_page_is_permanent_input_error():
    class FakeBrowser:
        def execute_script(self, script, *args):
            if "window.MM" in script:
                return False
            return {
                "url": "https://mjai.ekyu.moe/progress?task=test",
                "token_length": 0,
                "page_text": (
                    "an error occurred during the task, please check your inputs. "
                    "common causes: rule violation or ruleset is not compatible."
                ),
                "submit_disabled": True,
                "submit_busy": False,
            }

        def is_element_present(self, selector):
            return False

    automator = BrowserAutomator(
        controlled_submission=False,
        review_ui="killerducky",
    )

    with pytest.raises(ReviewInputError, match="invalid or incompatible"):
        automator._wait_for_result_or_error(FakeBrowser(), "[game]", timeout=0.1)


@pytest.mark.parametrize(
    "message",
    [
        "260601-test_a123: invalid game log",
        "test: not a hanchan game",
        "游戏长度不是半庄（东南）",
        "ゲームは半荘（東南）ではない",
    ],
)
def test_direct_invalid_game_messages_are_permanent(message):
    assert _review_input_error_reason(message)


if __name__ == "__main__":
    test_normalize_review_language()
    test_build_review_url()
