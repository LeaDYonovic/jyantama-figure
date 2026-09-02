import json

from batchmortal.paipu_import import PaipuImport
from batchmortal.player_library import (
    discover_local_import_files,
    discover_player_ids,
    load_player_import,
    merge_player_imports,
    player_result_path,
    save_player_import,
)
from main import resolve_target_name


def _import(account_id, records):
    return PaipuImport(
        urls=[record["paipu_url"] for record in records],
        records=records,
        account_id=account_id,
        available_records=len(records),
    )


def test_player_imports_merge_by_uuid_and_keep_richer_metadata(tmp_path):
    old = _import(
        17017362,
        [
            {
                "uuid": "260901-player-library-test",
                "paipu_url": "https://game.maj-soul.com/1/?paipu=260901-player-library-test_a1",
                "start_time": 100,
                "pt_delta": "",
            }
        ],
    )
    new = _import(
        17017362,
        [
            {
                "uuid": "260901-player-library-test",
                "paipu_url": "https://game.maj-soul.com/1/?paipu=260901-player-library-test_a1",
                "start_time": 100,
                "pt_delta": -12,
            },
            {
                "uuid": "260902-player-library-test",
                "paipu_url": "https://game.maj-soul.com/1/?paipu=260902-player-library-test_a1",
                "start_time": 200,
            },
        ],
    )

    merged = merge_player_imports(old, new)

    assert len(merged.records) == 2
    assert merged.records[0]["start_time"] == 200
    assert merged.records[1]["pt_delta"] == -12


def test_player_library_round_trip_and_discovery(tmp_path):
    library_root = tmp_path / "players"
    results_root = tmp_path / "results"
    imported = _import(
        17017362,
        [
            {
                "uuid": "260901-player-library-roundtrip",
                "paipu_url": "https://game.maj-soul.com/1/?paipu=260901-player-library-roundtrip_a1",
                "start_time": 100,
                "mode_id": 12,
                "record_type": "ranked",
            }
        ],
    )

    path = save_player_import(library_root, imported)
    loaded = load_player_import(library_root, 17017362)

    assert path.name == "paipu.json"
    assert loaded is not None
    assert loaded.account_id == 17017362
    assert loaded.urls == imported.urls
    assert discover_player_ids(library_root, results_root) == [17017362]
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["source"] == "local-player-library"


def test_results_only_player_is_discovered(tmp_path):
    result = player_result_path(tmp_path / "results", 42)
    result.parent.mkdir(parents=True)
    result.write_bytes(b"placeholder")

    assert discover_player_ids(tmp_path / "players", tmp_path / "results") == [42]


def test_local_discovery_only_uses_known_export_names(tmp_path):
    app_data = tmp_path / "app-data"
    downloads = tmp_path / "Downloads"
    imports = app_data / "imports"
    imports.mkdir(parents=True)
    downloads.mkdir()
    saved = imports / "koromo-42-20260902.json"
    exported = downloads / "majsoul-all-100-for-windows-2026-09-02.json"
    old_capture = downloads / "majsoul-replay-capture-2026-09-01.json"
    unrelated = downloads / "bank-export.json"
    for path in (saved, exported, old_capture, unrelated):
        path.write_text("{}", encoding="utf-8")

    found = discover_local_import_files(app_data, downloads)

    assert set(found) == {saved.resolve(), exported.resolve(), old_capture.resolve()}


def test_direct_account_id_uses_separate_output_name():
    assert resolve_target_name(None, ["one"], 17017362) == "玩家_17017362"
    assert resolve_target_name(None, ["one"], None) == "直接牌谱"
