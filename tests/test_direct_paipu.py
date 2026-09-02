import json

from main import (
    collect_direct_majsoul_tasks,
    load_direct_paipu_source,
    load_direct_paipu_inputs,
    normalize_majsoul_paipu_input,
)


def test_normalize_direct_majsoul_url():
    source = "https://game.maj-soul.com/1/?paipu=230101-test-log_a123456"

    url, direct_id = normalize_majsoul_paipu_input(source)

    assert url == source
    assert direct_id == "230101-test-log_a123456"


def test_normalize_direct_uuid_adds_account_viewpoint():
    url, direct_id = normalize_majsoul_paipu_input(
        "230101-test-log", account_id=12345678
    )

    assert direct_id.endswith("_a2684071")
    assert url.endswith(direct_id)


def test_normalize_raw_paipu_token_builds_official_url():
    url, direct_id = normalize_majsoul_paipu_input("230101-test-log_a123456")

    assert url == "https://game.maj-soul.com/1/?paipu=230101-test-log_a123456"
    assert direct_id == "230101-test-log_a123456"


def test_load_direct_inputs_reads_file_and_deduplicates(tmp_path):
    input_file = tmp_path / "paipu.txt"
    input_file.write_text(
        "# one per line\nfirst-paipu-token\nsecond-paipu-token\n",
        encoding="utf-8",
    )

    values = load_direct_paipu_inputs(["first-paipu-token"], str(input_file))

    assert values == ["first-paipu-token", "second-paipu-token"]


def test_direct_json_source_is_not_capped_at_one_hundred(tmp_path):
    records = [
        {
            "uuid": f"260902-full-export-{index:04d}",
            "mode_id": 12,
            "paipu_url": (
                "https://game.maj-soul.com/1/?paipu="
                f"260902-full-export-{index:04d}_a123456"
            ),
        }
        for index in range(150)
    ]
    input_file = tmp_path / "all-history.json"
    input_file.write_text(
        json.dumps(
            {
                "schema": "batchmortal-majsoul-links-v1",
                "scope": "all",
                "records": records,
            }
        ),
        encoding="utf-8",
    )

    imported = load_direct_paipu_source([], str(input_file))

    assert len(imported.urls) == 150
    assert len(imported.records) == 150


def test_collect_direct_tasks_does_not_need_player_api(tmp_path):
    inputs = [
        "https://game.maj-soul.com/1/?paipu=230101-first-log_a123456",
        "https://game.maj-soul.com/1/?paipu=230102-second-log_a123456",
    ]

    tasks = collect_direct_majsoul_tasks(
        inputs,
        str(tmp_path),
        {"230101-first-log_a123456"},
    )

    assert len(tasks) == 1
    assert tasks[0]["uuid"] == "230102-second-log_a123456"
    assert tasks[0]["mode"] == "direct"


def test_collect_direct_tasks_can_add_account_viewpoint(tmp_path):
    tasks = collect_direct_majsoul_tasks(
        ["230101-first-log"],
        str(tmp_path),
        set(),
        account_id=12345678,
    )

    assert tasks[0]["paipu_url"].endswith("_a2684071")
    assert tasks[0]["pt_delta"] == ""


def test_collect_direct_tasks_retains_imported_metadata(tmp_path):
    url = "https://game.maj-soul.com/1/?paipu=260901-metadata-test_a123456"
    tasks = collect_direct_majsoul_tasks(
        [url],
        str(tmp_path),
        set(),
        metadata_records=[
            {
                "paipu_url": url,
                "start_time": 1_700_000_000,
                "end_time": 1_700_000_300,
                "mode_id": 12,
                "record_type": "ranked",
                "placement": 3,
                "final_score": 13200,
                "pt_delta": -16,
                "player_level": 10401,
                "player_level_score": 1296,
            }
        ],
    )

    assert tasks[0]["mode"] == 12
    assert tasks[0]["record_type"] == "ranked"
    assert tasks[0]["start_time"]
    assert tasks[0]["placement"] == 3
    assert tasks[0]["final_score"] == 13200
    assert tasks[0]["pt_delta"] == -16
    assert tasks[0]["player_level"] == 10401
    assert tasks[0]["player_level_score"] == 1296
