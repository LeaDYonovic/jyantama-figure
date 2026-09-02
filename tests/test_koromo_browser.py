import json
import time

from batchmortal.koromo_browser import (
    build_koromo_player_url,
    wait_for_koromo_bridge_export,
    write_koromo_export,
)
from batchmortal.paipu_import import PaipuImport, SAFE_EXPORT_SCHEMA


def test_builds_public_player_page_url_for_selected_rooms():
    assert build_koromo_player_url(123456, modes=(9, 12, 16), limit=300) == (
        "https://amae-koromo.sapk.ch/player/123456/9.12.16?limit=300"
    )
    assert build_koromo_player_url(123456, modes=(12,), limit=None) == (
        "https://amae-koromo.sapk.ch/player/123456/12"
    )
    assert build_koromo_player_url(
        123456,
        modes=(12,),
        limit=100,
        bridge_token="abcdef12",
    ).endswith("?limit=100#batchmortal=abcdef12")


def test_writes_safe_koromo_export_without_browser_credentials(tmp_path):
    imported = PaipuImport(
        urls=["https://game.maj-soul.com/1/?paipu=test_a123"],
        records=[
            {
                "uuid": "test-record-uuid",
                "paipu_url": "https://game.maj-soul.com/1/?paipu=test_a123",
                "mode_id": 12,
                "record_type": "ranked",
            }
        ],
        account_id=42,
    )
    path = write_koromo_export(
        tmp_path / "safe.json",
        imported,
        modes=(12,),
        requested_limit=100,
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema"] == SAFE_EXPORT_SCHEMA
    assert payload["scope"] == "latest-100"
    serialized = json.dumps(payload).lower()
    assert "authorization" not in serialized
    assert "cookie" not in serialized
    assert "token" not in serialized


def test_waits_for_complete_userscript_download(tmp_path):
    token = "abc123"
    path = tmp_path / f"koromo-bridge-{token}.json"
    path.write_text(
        json.dumps(
            {
                "schema": SAFE_EXPORT_SCHEMA,
                "account_id": 42,
                "records": [{"uuid": "test", "paipu_url": "https://example.test"}],
            }
        ),
        encoding="utf-8",
    )

    found = wait_for_koromo_bridge_export(
        tmp_path,
        token,
        since=time.time() - 1,
        timeout=3,
    )

    assert found == path
