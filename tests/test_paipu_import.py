import base64
import json

from batchmortal.api import acc2match
from batchmortal.paipu_import import parse_koromo_records, read_paipu_import


def _varint(value: int) -> bytes:
    output = bytearray()
    while True:
        current = value & 0x7F
        value >>= 7
        if value:
            current |= 0x80
        output.append(current)
        if not value:
            return bytes(output)


def _uint(field: int, value: int) -> bytes:
    return _varint(field << 3) + _varint(value)


def _blob(field: int, value: bytes) -> bytes:
    return _varint((field << 3) | 2) + _varint(len(value)) + value


def _wrapper(method: str, payload: bytes) -> bytes:
    return _blob(1, method.encode()) + _blob(2, payload)


def _frame(direction: str, request_id: int, method: str, payload: bytes) -> dict:
    message_type = 2 if direction == "sent" else 3
    wrapper = _wrapper(method if direction == "sent" else "", payload)
    raw = bytes([message_type, request_id & 0xFF, request_id >> 8]) + wrapper
    return {"direction": direction, "body_base64": base64.b64encode(raw).decode()}


def _v2_level(level_id: int, score: int) -> bytes:
    return _uint(1, level_id) + _uint(2, score)


def _v2_player(
    account_id: int,
    *,
    placement: int = 1,
    pt_delta: int = 165,
    final_score: int = 64600,
) -> bytes:
    encoded_pt = pt_delta if pt_delta >= 0 else (1 << 64) + pt_delta
    encoded_score = final_score if final_score >= 0 else (1 << 64) + final_score
    return (
        _uint(1, placement)
        + _uint(2, account_id)
        + _blob(4, _v2_level(10401, 1465))
        + _uint(7, encoded_pt)
        + _uint(8, encoded_score)
    )


def _v2_record(
    uuid: str,
    start_time: int,
    account_id: int,
    mode_id: int = 12,
    record_type_tag: int = 0,
) -> bytes:
    return (
        _uint(5, record_type_tag)
        + _blob(2, uuid.encode())
        + _uint(3, start_time)
        + _uint(6, mode_id)
        + _blob(7, _v2_player(account_id))
    )


def test_v2_record_type_distinguishes_ranked_friend_and_activity(tmp_path):
    account_id = 42
    response = (
        _blob(3, _v2_record("ranked-south-test-uuid", 400, account_id, 12, 1))
        + _blob(3, _v2_record("friend-south-test-uuid", 300, account_id, 2, 2))
        + _blob(3, _v2_record("friend-east-test-uuid", 200, account_id, 1, 2))
        + _blob(3, _v2_record("activity-south-test-uuid", 100, account_id, 2, 4))
    )
    payload = {
        "schema": "majsoul-reviewer-capture-v1",
        "frames": [
            _frame("sent", 2, ".lq.Lobby.fetchNextGameRecordList", b""),
            _frame("received", 2, "", response),
        ],
    }
    path = tmp_path / "typed-capture.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = read_paipu_import(path)

    assert result.available_records == 4
    assert result.skipped_non_hanchan == 2
    assert [record["record_type"] for record in result.records] == [
        "ranked",
        "friend",
    ]


def test_reads_safe_windows_export(tmp_path):
    path = tmp_path / "export.json"
    path.write_text(
        json.dumps(
            {
                "schema": "batchmortal-majsoul-links-v1",
                "account_id": 42,
                "records": [
                    {"uuid": "260901-test-uuid", "paipu_url": "https://game.maj-soul.com/1/?paipu=one"},
                    {"uuid": "260902-test-uuid"},
                ],
            }
        ),
        encoding="utf-8",
    )

    result = read_paipu_import(path)

    assert result.account_id == 42
    assert result.urls[0].endswith("paipu=one")
    assert result.urls[1].endswith(f"260902-test-uuid_a{acc2match(42)}")
    assert result.records[0]["paipu_url"].endswith("paipu=one")
    assert not result.contains_login_frames


def test_safe_export_skips_east_games(tmp_path):
    path = tmp_path / "mixed-export.json"
    path.write_text(
        json.dumps(
            {
                "schema": "batchmortal-majsoul-links-v1",
                "records": [
                    {
                        "uuid": "260902-east-test-uuid",
                        "mode_id": 11,
                        "paipu_url": "https://game.maj-soul.com/1/?paipu=east",
                    },
                    {
                        "uuid": "260901-south-test-uuid",
                        "mode_id": 12,
                        "paipu_url": "https://game.maj-soul.com/1/?paipu=south",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    result = read_paipu_import(path)

    assert result.urls == ["https://game.maj-soul.com/1/?paipu=south"]
    assert result.skipped_non_hanchan == 1


def test_extracts_v2_records_and_flags_login_frames(tmp_path):
    account_id = 42
    response = (
        _uint(2, 0)
        + _blob(3, _v2_record("260902-newer-test-uuid", 200, account_id))
        + _blob(3, _v2_record("260901-older-test-uuid", 100, account_id))
    )
    payload = {
        "schema": "majsoul-reviewer-capture-v1",
        "frames": [
            _frame("sent", 1, ".lq.Lobby.oauth2Login", _blob(2, b"secret-token")),
            _frame("sent", 2, ".lq.Lobby.fetchNextGameRecordList", b""),
            _frame("received", 2, "", response),
        ],
    }
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = read_paipu_import(path, limit=1)

    assert result.account_id == account_id
    assert result.available_records == 2
    assert result.contains_login_frames
    assert result.urls == [
        f"https://game.maj-soul.com/1/?paipu=260902-newer-test-uuid_a{acc2match(account_id)}"
    ]
    assert result.records[0]["start_time"] == 200
    assert result.records[0]["placement"] == 1
    assert result.records[0]["final_score"] == 64600
    assert result.records[0]["pt_delta"] == 165
    assert result.records[0]["player_level"] == 10401
    assert result.records[0]["player_level_score"] == 1465


def test_extracts_signed_v2_pt_and_final_score(tmp_path):
    account_id = 42
    record = (
        _blob(2, b"260902-signed-test-uuid")
        + _uint(3, 200)
        + _uint(4, 250)
        + _uint(6, 12)
        + _blob(
            7,
            _v2_player(
                account_id,
                placement=4,
                pt_delta=-205,
                final_score=-400,
            ),
        )
    )
    response = _uint(2, 0) + _blob(3, record)
    payload = {
        "schema": "majsoul-reviewer-capture-v1",
        "frames": [
            _frame("sent", 2, ".lq.Lobby.fetchNextGameRecordList", b""),
            _frame("received", 2, "", response),
        ],
    }
    path = tmp_path / "signed-capture.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = read_paipu_import(path)

    assert result.records[0]["end_time"] == 250
    assert result.records[0]["placement"] == 4
    assert result.records[0]["pt_delta"] == -205
    assert result.records[0]["final_score"] == -400


def test_filters_non_hanchan_before_applying_limit(tmp_path):
    account_id = 42
    response = (
        _uint(2, 0)
        + _blob(3, _v2_record("260902-east-test-uuid", 300, account_id, mode_id=11))
        + _blob(3, _v2_record("260901-south-test-uuid", 200, account_id, mode_id=12))
    )
    payload = {
        "schema": "majsoul-reviewer-capture-v1",
        "frames": [
            _frame("sent", 2, ".lq.Lobby.fetchNextGameRecordList", b""),
            _frame("received", 2, "", response),
        ],
    }
    path = tmp_path / "mixed-capture.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = read_paipu_import(path, limit=1)

    assert result.available_records == 2
    assert result.skipped_non_hanchan == 1
    assert result.urls == [
        f"https://game.maj-soul.com/1/?paipu=260901-south-test-uuid_a{acc2match(account_id)}"
    ]


def test_safe_export_can_import_more_than_one_hundred_records(tmp_path):
    records = [
        {
            "uuid": f"260902-all-history-{index:04d}",
            "mode_id": 12,
            "start_time": 2_000_000_000 - index,
            "paipu_url": (
                "https://game.maj-soul.com/1/?paipu="
                f"260902-all-history-{index:04d}_a123456"
            ),
        }
        for index in range(150)
    ]
    path = tmp_path / "all-history.json"
    path.write_text(
        json.dumps(
            {
                "schema": "batchmortal-majsoul-links-v1",
                "scope": "all",
                "account_id": 42,
                "count": len(records),
                "records": records,
            }
        ),
        encoding="utf-8",
    )

    complete = read_paipu_import(path)
    limited = read_paipu_import(path, limit=100)

    assert len(complete.records) == 150
    assert len(complete.urls) == 150
    assert len(limited.records) == 100


def test_parses_koromo_player_records_with_pt_and_viewpoint():
    account_id = 12345678
    records = [
        {
            "uuid": "260902-koromo-record-test",
            "modeId": 12,
            "startTime": 200,
            "endTime": 300,
            "players": [
                {"accountId": 1, "nickname": "一位", "score": 51000, "gradingScore": 120},
                {"accountId": account_id, "nickname": "目标", "level": 10401, "score": 32000, "gradingScore": 47},
                {"accountId": 2, "nickname": "三位", "score": 18000, "gradingScore": -20},
                {"accountId": 3, "nickname": "四位", "score": -1000, "gradingScore": -147},
            ],
        }
    ]

    result = parse_koromo_records(records, account_id)

    assert result.account_id == account_id
    assert result.records[0]["nickname"] == "目标"
    assert result.records[0]["placement"] == 2
    assert result.records[0]["final_score"] == 32000
    assert result.records[0]["pt_delta"] == 47
    assert result.records[0]["player_level"] == 10401
    assert result.urls[0].endswith(
        f"260902-koromo-record-test_a{acc2match(account_id)}"
    )


def test_raw_koromo_record_list_infers_shared_player(tmp_path):
    account_id = 42
    payload = [
        {
            "uuid": f"26090{day}-koromo-shared-player",
            "modeId": 9,
            "startTime": day,
            "players": [
                {"accountId": day, "score": 40000},
                {"accountId": account_id, "score": 30000},
                {"accountId": day + 10, "score": 20000},
                {"accountId": day + 20, "score": 10000},
            ],
        }
        for day in (1, 2)
    ]
    path = tmp_path / "koromo-records.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    result = read_paipu_import(path)

    assert result.account_id == account_id
    assert len(result.urls) == 2
