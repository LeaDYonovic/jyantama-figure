from batchmortal.api import acc2match, build_paipu_urls, configure_access_token, SESSION

def test_acc2match():
    # From decode.py and JS selfTest: acc2match(15628582) should equal 63606719
    expected = 63606719
    got = acc2match(15628582)
    if got == expected:
        print(f"[Test] acc2match(15628582) = {got} ✓")
    else:
        print(f"[Test] acc2match MISMATCH: expected {expected}, got {got}")
        exit(1)


def test_build_paipu_urls_keeps_rank_and_pt_metadata():
    records = [
        {
            "uuid": "game-uuid",
            "startTime": 1_700_000_000_000,
            "endTime": 1_700_000_300_000,
            "players": [
                {"accountId": 20, "score": 27000, "gradingScore": 50, "level": 101},
                {"accountId": 10, "score": 41000, "gradingScore": 120, "level": 102},
                {"accountId": 30, "score": 23000, "gradingScore": -10, "level": 103},
                {"accountId": 40, "score": 9000, "gradingScore": -180, "level": 104},
            ],
        }
    ]

    item = build_paipu_urls(records, 10)[0]

    assert item["placement"] == 1
    assert item["finalScore"] == 41000
    assert item["ptDelta"] == 120
    assert item["playerLevel"] == 102


def test_access_token_is_configured_without_logging_value():
    assert configure_access_token("secret-test-token") is True
    assert SESSION.headers["Authorization"] == "Bearer secret-test-token"
    assert configure_access_token("") is False
    assert "Authorization" not in SESSION.headers

if __name__ == '__main__':
    test_acc2match()
