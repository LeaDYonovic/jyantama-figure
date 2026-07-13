from batchmortal.tenhou import (
    build_tenhou_paipu_urls,
    decode_tenhou_viewpoint,
    format_tenhou_timestamp,
    normalize_tenhou_modes,
    parse_tenhou_log_url,
    tenhou_record_mode,
)


PLAYER = "ププリン"


def make_record(
    log_id: str,
    *,
    starttime: int,
    playernum: int = 4,
    playlength: int = 2,
    url_host: str = "tenhou.net",
) -> dict:
    record = {
        "starttime": starttime,
        "during": 32,
        "playernum": playernum,
        "playlength": playlength,
        "player1": PLAYER,
        "player2": "Tatsuno7",
        "player3": "Navitas",
        "tw": "198",
        "url": f"http://{url_host}/0/?log={log_id}",
    }
    if playernum == 4:
        record["player4"] = "tca00"
    return record


def test_decode_nodocchi_viewpoint_from_real_example():
    # Nodocchi returns 198 for this game. ププリン is player1 (first place),
    # and the site's two-bit permutation decoding resolves that to Tenhou seat 2.
    assert decode_tenhou_viewpoint("198", 1) == 2


def test_build_real_example_paipu_url():
    record = make_record(
        "2026071212gm-00a9-0000-2dab9d24",
        starttime=1783828560,
    )

    assert build_tenhou_paipu_urls([record], PLAYER) == [
        {
            "source": "tenhou",
            "mode": "4p-south",
            "uuid": "2026071212gm-00a9-0000-2dab9d24",
            "paipuUrl": "http://tenhou.net/0/?log=2026071212gm-00a9-0000-2dab9d24&tw=2",
            "startTime": format_tenhou_timestamp(1783828560),
            "endTime": format_tenhou_timestamp(1783828560 + 32 * 60),
        }
    ]


def test_mode_filter_and_limit_are_applied_per_actual_mode():
    records = [
        make_record("2026071213gm-00a9-0000-aaaaaaaa", starttime=300, playlength=2),
        make_record("2026071212gm-00a9-0000-bbbbbbbb", starttime=200, playlength=2),
        make_record("2026071211gm-00a9-0000-cccccccc", starttime=100, playlength=1),
    ]

    items = build_tenhou_paipu_urls(records, PLAYER, modes="4p", limit=1)

    assert [item["uuid"] for item in items] == [
        "2026071213gm-00a9-0000-aaaaaaaa",
        "2026071211gm-00a9-0000-cccccccc",
    ]
    assert [item["mode"] for item in items] == ["4p-south", "4p-east"]


def test_non_tenhou_urls_are_not_mistaken_for_tenhou_records():
    record = make_record(
        "2026071212gm-00a9-0000-2dab9d24",
        starttime=1783828560,
        url_host="game.maj-soul.com",
    )

    assert build_tenhou_paipu_urls([record], PLAYER) == []
    assert parse_tenhou_log_url(record["url"]) is None


def test_records_without_url_or_viewpoint_are_skipped():
    no_url = make_record("2026071212gm-00a9-0000-2dab9d24", starttime=200)
    no_url.pop("url")
    no_viewpoint = make_record("2026071213gm-00a9-0000-aaaaaaaa", starttime=300)
    no_viewpoint.pop("tw")

    assert build_tenhou_paipu_urls([no_url, no_viewpoint], PLAYER) == []


def test_tenhou_mode_aliases_and_labels():
    assert normalize_tenhou_modes("四南,3p-east") == ("4p-south", "3p-east")
    assert normalize_tenhou_modes("*") == ("all",)
    assert tenhou_record_mode({"playernum": 3, "playlength": 1}) == "3p-east"
