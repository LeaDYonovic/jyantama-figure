from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from batchmortal.paipu_import import SAFE_EXPORT_SCHEMA, PaipuImport, read_paipu_import


PLAYER_DIRECTORY_PREFIX = "玩家_"
DOWNLOAD_IMPORT_PATTERNS = (
    "majsoul-*.json",
    "koromo-bridge-*.json",
    "雀魂*.json",
)


def normalize_account_id(value: int | str | None) -> int:
    try:
        account_id = int(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError("玩家 ID 必须是正整数") from exc
    if account_id <= 0:
        raise ValueError("玩家 ID 必须是正整数")
    return account_id


def player_directory_name(account_id: int | str) -> str:
    return f"{PLAYER_DIRECTORY_PREFIX}{normalize_account_id(account_id)}"


def player_library_path(library_root: str | Path, account_id: int | str) -> Path:
    return Path(library_root) / str(normalize_account_id(account_id)) / "paipu.json"


def player_result_path(results_root: str | Path, account_id: int | str) -> Path:
    return (
        Path(results_root)
        / "majsoul"
        / player_directory_name(account_id)
        / "results.xlsx"
    )


def discover_player_ids(
    library_root: str | Path,
    results_root: str | Path | None = None,
) -> list[int]:
    player_ids: set[int] = set()
    root = Path(library_root)
    if root.is_dir():
        for child in root.iterdir():
            if child.is_dir() and child.name.isdigit() and (child / "paipu.json").is_file():
                player_ids.add(int(child.name))

    if results_root is not None:
        result_parent = Path(results_root) / "majsoul"
        if result_parent.is_dir():
            for child in result_parent.iterdir():
                if not child.is_dir() or not child.name.startswith(PLAYER_DIRECTORY_PREFIX):
                    continue
                suffix = child.name[len(PLAYER_DIRECTORY_PREFIX) :]
                if suffix.isdigit() and any(
                    (child / filename).is_file()
                    for filename in ("results.xlsx", "results.csv")
                ):
                    player_ids.add(int(suffix))
    return sorted(player_ids)


def discover_local_import_files(
    app_data_root: str | Path,
    downloads_root: str | Path,
) -> list[Path]:
    """Find files produced by this project without scanning unrelated JSON files."""
    candidates: set[Path] = set()
    imports_root = Path(app_data_root) / "imports"
    if imports_root.is_dir():
        candidates.update(path.resolve() for path in imports_root.glob("*.json") if path.is_file())

    downloads = Path(downloads_root)
    if downloads.is_dir():
        for pattern in DOWNLOAD_IMPORT_PATTERNS:
            candidates.update(path.resolve() for path in downloads.glob(pattern) if path.is_file())
    return sorted(candidates, key=lambda path: (path.stat().st_mtime_ns, str(path)))


def _record_key(record: dict) -> str:
    uuid = str(record.get("uuid") or "").strip()
    if uuid:
        return f"uuid:{uuid}"
    url = str(record.get("paipu_url") or record.get("paipuUrl") or "").strip()
    if url:
        try:
            token = parse_qs(urlparse(url).query).get("paipu", [""])[0]
        except ValueError:
            token = ""
        return f"url:{token or url}"
    return ""


def _merge_record(existing: dict, incoming: dict) -> dict:
    merged = dict(existing)
    for key, value in incoming.items():
        if value is not None and str(value).strip() != "":
            merged[key] = value
    return merged


def merge_player_imports(
    existing: PaipuImport | None,
    incoming: PaipuImport,
    account_id: int | str | None = None,
) -> PaipuImport:
    normalized_id = normalize_account_id(account_id or incoming.account_id)
    if incoming.account_id is not None and normalize_account_id(incoming.account_id) != normalized_id:
        raise ValueError("导入文件中的玩家 ID 与当前玩家不一致")
    if existing is not None and existing.account_id is not None:
        if normalize_account_id(existing.account_id) != normalized_id:
            raise ValueError("本地牌谱库中的玩家 ID 不一致")

    merged_by_key: dict[str, dict] = {}
    for source in ((existing.records if existing else []), incoming.records):
        for record in source:
            if not isinstance(record, dict):
                continue
            key = _record_key(record)
            if not key:
                continue
            merged_by_key[key] = _merge_record(merged_by_key.get(key, {}), record)

    records = sorted(
        merged_by_key.values(),
        key=lambda record: int(
            record.get("start_time") or record.get("startTime") or 0
        ),
        reverse=True,
    )
    if not records:
        raise ValueError("导入数据中没有可保存的四人半庄牌谱")
    urls = [
        str(record.get("paipu_url") or record.get("paipuUrl") or "").strip()
        for record in records
    ]
    urls = [url for url in urls if url]
    return PaipuImport(
        urls=urls,
        records=records,
        account_id=normalized_id,
        source_format=SAFE_EXPORT_SCHEMA,
        contains_login_frames=incoming.contains_login_frames,
        available_records=len(records),
        skipped_non_hanchan=max(
            incoming.skipped_non_hanchan,
            existing.skipped_non_hanchan if existing else 0,
        ),
    )


def load_player_import(
    library_root: str | Path,
    account_id: int | str,
) -> PaipuImport | None:
    path = player_library_path(library_root, account_id)
    return read_paipu_import(path) if path.is_file() else None


def save_player_import(
    library_root: str | Path,
    imported: PaipuImport,
) -> Path:
    account_id = normalize_account_id(imported.account_id)
    path = player_library_path(library_root, account_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": SAFE_EXPORT_SCHEMA,
        "source": "local-player-library",
        "account_id": account_id,
        "scope": "merged-local-history",
        "count": len(imported.records),
        "records": imported.records,
    }
    temporary_path = path.with_suffix(".json.tmp")
    temporary_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary_path, path)
    return path


def assign_player_id(imported: PaipuImport, account_id: int | str) -> PaipuImport:
    """Attach an explicitly supplied ID to an otherwise ungrouped safe import."""
    return replace(imported, account_id=normalize_account_id(account_id))
