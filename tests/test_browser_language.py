from batchmortal.browser import build_review_url, normalize_review_language


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


if __name__ == "__main__":
    test_normalize_review_language()
    test_build_review_url()
