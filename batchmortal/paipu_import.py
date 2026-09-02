from __future__ import annotations

import base64
import binascii
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from batchmortal.api import acc2match


SAFE_EXPORT_SCHEMA = "batchmortal-majsoul-links-v1"
CAPTURE_SCHEMA = "majsoul-reviewer-capture-v1"
SENSITIVE_METHODS = {
    "login",
    "emailLogin",
    "oauth2Auth",
    "oauth2Check",
    "oauth2Login",
    "signup",
}
# Standard four-player ranked hanchan modes. Mortal rejects tonpuusen and sanma.
# Copper South=2, Silver South=5, Gold South=9, Jade South=12, Throne South=16.
MORTAL_HANCHAN_MODE_IDS = frozenset({2, 5, 9, 12, 16})
V2_RECORD_TYPES = {
    1: "ranked",
    2: "friend",
    3: "match",
    4: "activity",
}


def normalize_majsoul_paipu_input(
    value: str, account_id: int | None = None
) -> tuple[str, str]:
    """Return a reviewable Mahjong Soul URL and a stable per-seat identifier."""
    text = str(value or "").strip()
    if not text:
        raise ValueError("Empty Mahjong Soul paipu URL")

    if text.startswith("paipu="):
        paipu_value = text.partition("=")[2].strip()
        url = f"https://game.maj-soul.com/1/?paipu={paipu_value}"
    elif "://" not in text:
        paipu_value = text
        url = f"https://game.maj-soul.com/1/?paipu={paipu_value}"
    else:
        parsed = urlparse(text)
        host = (parsed.hostname or "").lower()
        allowed_host = (
            host == "game.maj-soul.com"
            or host == "mahjongsoul.game.yo-star.com"
            or host.endswith(".mahjongsoul.com")
        )
        if parsed.scheme not in {"http", "https"} or not allowed_host:
            raise ValueError(f"Unsupported Mahjong Soul paipu host: {host or text}")
        paipu_values = parse_qs(parsed.query).get("paipu", [])
        if not paipu_values:
            raise ValueError(f"Missing paipu parameter: {text}")
        paipu_value = paipu_values[0].strip()
        url = text

    if len(paipu_value) < 8 or any(
        character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_"
        for character in paipu_value
    ):
        raise ValueError(f"Invalid Mahjong Soul paipu token: {paipu_value}")
    if account_id and not re.search(r"_a\d+$", paipu_value):
        paipu_value = f"{paipu_value}_a{acc2match(account_id)}"
        url = f"https://game.maj-soul.com/1/?paipu={paipu_value}"
    return url, paipu_value


@dataclass(frozen=True)
class PaipuImport:
    urls: list[str]
    records: list[dict] = field(default_factory=list)
    account_id: int | None = None
    source_format: str = "text"
    contains_login_frames: bool = False
    available_records: int = 0
    skipped_non_hanchan: int = 0


def _read_varint(data: bytes, position: int) -> tuple[int, int]:
    value = 0
    shift = 0
    for _ in range(10):
        if position >= len(data):
            break
        current = data[position]
        position += 1
        value |= (current & 0x7F) << shift
        if not current & 0x80:
            return value, position
        shift += 7
    raise ValueError("Invalid protobuf varint in capture file")


def _read_fields(data: bytes) -> list[tuple[int, int, int | bytes]]:
    fields: list[tuple[int, int, int | bytes]] = []
    position = 0
    while position < len(data):
        tag, position = _read_varint(data, position)
        field_number = tag >> 3
        wire_type = tag & 7
        if not field_number:
            raise ValueError("Invalid protobuf field in capture file")
        if wire_type == 0:
            value, position = _read_varint(data, position)
            fields.append((field_number, wire_type, value))
        elif wire_type == 2:
            length, position = _read_varint(data, position)
            end = position + length
            if end > len(data):
                raise ValueError("Truncated protobuf field in capture file")
            fields.append((field_number, wire_type, data[position:end]))
            position = end
        elif wire_type == 1:
            position += 8
        elif wire_type == 5:
            position += 4
        else:
            raise ValueError(f"Unsupported protobuf wire type: {wire_type}")
        if position > len(data):
            raise ValueError("Truncated protobuf value in capture file")
    return fields


def _first_field(
    fields: list[tuple[int, int, int | bytes]], field_number: int, wire_type: int
) -> int | bytes | None:
    return next(
        (
            value
            for current_number, current_wire, value in fields
            if current_number == field_number and current_wire == wire_type
        ),
        None,
    )


def _byte_fields(
    fields: list[tuple[int, int, int | bytes]], field_number: int
) -> list[bytes]:
    return [
        value
        for current_number, wire_type, value in fields
        if current_number == field_number and wire_type == 2 and isinstance(value, bytes)
    ]


def _decode_text(value: int | bytes | None) -> str:
    if not isinstance(value, bytes):
        return ""
    return value.decode("utf-8", errors="strict")


def _decode_wrapper(data: bytes) -> tuple[str, bytes]:
    fields = _read_fields(data)
    name = _decode_text(_first_field(fields, 1, 2))
    payload = _first_field(fields, 2, 2)
    return name, payload if isinstance(payload, bytes) else b""


def _short_method(name: str) -> str:
    return name.rsplit(".", 1)[-1]


def _looks_like_uuid(value: str) -> bool:
    return len(value) >= 16 and all(character.isalnum() or character == "-" for character in value)


def _integer_field(
    fields: list[tuple[int, int, int | bytes]], field_number: int
) -> int:
    value = _first_field(fields, field_number, 0)
    return value if isinstance(value, int) else 0


def _signed_integer_field(
    fields: list[tuple[int, int, int | bytes]], field_number: int
) -> int:
    """Decode protobuf int32 values that arrive sign-extended as uint64 varints."""
    value = _integer_field(fields, field_number)
    return value - (1 << 64) if value >= (1 << 63) else value


def _decode_v2_record(data: bytes) -> dict | None:
    fields = _read_fields(data)
    uuid = _decode_text(_first_field(fields, 2, 2))
    if not _looks_like_uuid(uuid):
        return None
    players = []
    for player_data in _byte_fields(fields, 7):
        player_fields = _read_fields(player_data)
        account_id = _first_field(player_fields, 2, 0)
        if isinstance(account_id, int) and account_id > 0:
            level_fields = []
            level_data = _first_field(player_fields, 4, 2)
            if isinstance(level_data, bytes):
                level_fields = _read_fields(level_data)
            players.append(
                {
                    "account_id": account_id,
                    "placement": _integer_field(player_fields, 1),
                    "pt_delta": _signed_integer_field(player_fields, 7),
                    "final_score": _signed_integer_field(player_fields, 8),
                    "player_level": _integer_field(level_fields, 1),
                    "player_level_score": _integer_field(level_fields, 2),
                }
            )
    start_time = _first_field(fields, 3, 0)
    end_time = _first_field(fields, 4, 0)
    mode_id = _first_field(fields, 6, 0)
    record_tag = _first_field(fields, 5, 0)
    return {
        "uuid": uuid,
        "start_time": start_time if isinstance(start_time, int) else 0,
        "end_time": end_time if isinstance(end_time, int) else 0,
        "mode_id": mode_id if isinstance(mode_id, int) else 0,
        "record_type": V2_RECORD_TYPES.get(record_tag, "unknown"),
        "account_ids": [player["account_id"] for player in players],
        "players": players,
    }


def _decode_legacy_record(data: bytes) -> dict | None:
    fields = _read_fields(data)
    uuid = _decode_text(_first_field(fields, 1, 2))
    if not _looks_like_uuid(uuid):
        return None
    account_ids = []
    for player_data in _byte_fields(fields, 11):
        player_fields = _read_fields(player_data)
        account_id = _first_field(player_fields, 1, 0)
        if isinstance(account_id, int) and account_id > 0:
            account_ids.append(account_id)
    start_time = _first_field(fields, 2, 0)
    return {
        "uuid": uuid,
        "start_time": start_time if isinstance(start_time, int) else 0,
        "record_type": "unknown",
        "account_ids": account_ids,
    }


def _infer_account_id(records: list[dict]) -> int | None:
    if not records:
        return None
    counts: Counter[int] = Counter()
    for record in records:
        counts.update(set(record.get("account_ids") or []))
    if not counts:
        return None
    account_id, frequency = counts.most_common(1)[0]
    return account_id if frequency == len(records) else None


def _record_url(uuid: str, account_id: int | None) -> str:
    suffix = f"_a{acc2match(account_id)}" if account_id else ""
    return f"https://game.maj-soul.com/1/?paipu={uuid}{suffix}"


def _first_record_value(record: dict, *keys: str):
    for key in keys:
        if key in record and record[key] is not None and str(record[key]).strip() != "":
            return record[key]
    return ""


def _safe_integer(value):
    if value is None or value == "":
        return ""
    try:
        return int(value)
    except (TypeError, ValueError):
        return ""


def _normalize_record(record: dict, account_id: int | None) -> dict | None:
    uuid = str(_first_record_value(record, "uuid") or "").strip()
    url = str(_first_record_value(record, "paipu_url", "paipuUrl") or "").strip()
    if not url and _looks_like_uuid(uuid):
        url = _record_url(uuid, account_id)
    if not url:
        return None
    return {
        "uuid": uuid,
        "paipu_url": url,
        "start_time": _safe_integer(_first_record_value(record, "start_time", "startTime")),
        "end_time": _safe_integer(_first_record_value(record, "end_time", "endTime")),
        "mode_id": _safe_integer(_first_record_value(record, "mode_id", "modeId")),
        "record_type": str(
            _first_record_value(record, "record_type", "recordType") or "unknown"
        ).strip().lower(),
        "placement": _safe_integer(_first_record_value(record, "placement", "rank")),
        "final_score": _safe_integer(_first_record_value(record, "final_score", "finalScore")),
        "pt_delta": _safe_integer(_first_record_value(record, "pt_delta", "ptDelta")),
        "player_level": _safe_integer(_first_record_value(record, "player_level", "playerLevel")),
        "player_level_score": _safe_integer(
            _first_record_value(record, "player_level_score", "playerLevelScore")
        ),
    }


def _capture_record(record: dict, account_id: int | None) -> dict:
    player = next(
        (
            current
            for current in record.get("players") or []
            if current.get("account_id") == account_id
        ),
        {},
    )
    return {
        "uuid": record["uuid"],
        "paipu_url": _record_url(record["uuid"], account_id),
        "start_time": record.get("start_time", 0),
        "end_time": record.get("end_time", 0),
        "mode_id": record.get("mode_id", 0),
        "record_type": record.get("record_type", "unknown"),
        "placement": player.get("placement", ""),
        "final_score": player.get("final_score", ""),
        "pt_delta": player.get("pt_delta", ""),
        "player_level": player.get("player_level", ""),
        "player_level_score": player.get("player_level_score", ""),
    }


def _is_mortal_hanchan(record: dict) -> bool:
    try:
        mode_id = int(record.get("mode_id") or record.get("modeId") or 0)
    except (TypeError, ValueError):
        mode_id = 0
    record_type = str(
        record.get("record_type") or record.get("recordType") or "unknown"
    ).strip().lower()
    if record_type == "ranked":
        return mode_id in MORTAL_HANCHAN_MODE_IDS
    if record_type == "friend":
        # Friend-room subtag 2 is four-player South. 1 is East; 11/12 are sanma.
        return mode_id == 2
    if record_type in {"match", "activity"}:
        return False
    # Old exports and plain URL lists have no category metadata. Keep their old
    # behaviour so existing files remain importable, but the desktop labels them
    # as unknown instead of guessing that they were ranked games.
    return mode_id <= 0 or mode_id in MORTAL_HANCHAN_MODE_IDS


def _parse_capture(payload: dict, limit: int | None) -> PaipuImport:
    frames = payload.get("frames")
    if not isinstance(frames, list):
        raise ValueError("Capture JSON is missing the frames array")

    inflight: dict[int, str] = {}
    records_by_uuid: dict[str, dict] = {}
    contains_login_frames = False
    for frame in frames:
        if not isinstance(frame, dict):
            continue
        encoded = frame.get("body_base64")
        if not isinstance(encoded, str) or not encoded:
            continue
        try:
            raw = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error):
            continue
        if len(raw) < 3:
            continue
        message_type = raw[0]
        request_id = raw[1] | (raw[2] << 8)
        if message_type == 2:
            try:
                name, _ = _decode_wrapper(raw[3:])
            except (UnicodeError, ValueError):
                continue
            method = _short_method(name)
            inflight[request_id] = method
            contains_login_frames = contains_login_frames or method in SENSITIVE_METHODS
            continue
        if message_type != 3:
            continue
        method = inflight.pop(request_id, "")
        if method not in {"fetchNextGameRecordList", "fetchGameRecordList"}:
            continue
        try:
            _, response_data = _decode_wrapper(raw[3:])
            response_fields = _read_fields(response_data)
            for record_data in _byte_fields(response_fields, 3):
                record = (
                    _decode_v2_record(record_data)
                    if method == "fetchNextGameRecordList"
                    else _decode_legacy_record(record_data)
                )
                if record:
                    records_by_uuid[record["uuid"]] = record
        except (UnicodeError, ValueError):
            continue

    records = sorted(
        records_by_uuid.values(),
        key=lambda record: int(record.get("start_time") or 0),
        reverse=True,
    )
    account_id = _infer_account_id(records)
    eligible_records = [record for record in records if _is_mortal_hanchan(record)]
    skipped_non_hanchan = len(records) - len(eligible_records)
    selected = eligible_records[:limit] if limit and limit > 0 else eligible_records
    safe_records = [_capture_record(record, account_id) for record in selected]
    urls = [record["paipu_url"] for record in safe_records]
    if not urls:
        raise ValueError("Capture JSON does not contain any four-player hanchan records supported by Mortal")
    return PaipuImport(
        urls=urls,
        records=safe_records,
        account_id=account_id,
        source_format=CAPTURE_SCHEMA,
        contains_login_frames=contains_login_frames,
        available_records=len(records),
        skipped_non_hanchan=skipped_non_hanchan,
    )


def _parse_safe_export(payload: dict, limit: int | None) -> PaipuImport:
    raw_account_id = payload.get("account_id")
    try:
        account_id = int(raw_account_id) if raw_account_id else None
    except (TypeError, ValueError):
        account_id = None
    safe_records: list[dict] = []
    skipped_non_hanchan = 0
    structured_record_count = 0
    records = payload.get("records")
    if isinstance(records, list):
        for record in records:
            if not isinstance(record, dict):
                continue
            structured_record_count += 1
            if not _is_mortal_hanchan(record):
                skipped_non_hanchan += 1
                continue
            normalized = _normalize_record(record, account_id)
            if normalized:
                safe_records.append(normalized)
    urls_value = payload.get("urls")
    if isinstance(urls_value, list):
        safe_records.extend(
            {"uuid": "", "paipu_url": str(value).strip()}
            for value in urls_value
            if str(value).strip()
        )
    deduplicated: list[dict] = []
    seen_urls: set[str] = set()
    for record in safe_records:
        url = str(record.get("paipu_url") or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        deduplicated.append(record)
    if limit and limit > 0:
        deduplicated = deduplicated[:limit]
    if not deduplicated:
        raise ValueError("Windows export JSON does not contain any paipu links")
    candidates = [record["paipu_url"] for record in deduplicated]
    return PaipuImport(
        urls=candidates,
        records=deduplicated,
        account_id=account_id,
        source_format=SAFE_EXPORT_SCHEMA,
        available_records=max(structured_record_count, len(candidates) + skipped_non_hanchan),
        skipped_non_hanchan=skipped_non_hanchan,
    )


def read_paipu_import(path: str | Path, limit: int | None = None) -> PaipuImport:
    file_path = Path(path).expanduser()
    if not file_path.is_file():
        raise ValueError(f"Paipu file not found: {file_path}")
    text = file_path.read_text(encoding="utf-8-sig")
    if file_path.suffix.lower() != ".json":
        urls = [
            line.strip()
            for line in text.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        urls = list(dict.fromkeys(urls))
        if limit and limit > 0:
            urls = urls[:limit]
        if not urls:
            raise ValueError("Paipu text file does not contain any links")
        return PaipuImport(
            urls=urls,
            records=[{"uuid": "", "paipu_url": url} for url in urls],
            source_format="text",
            available_records=len(urls),
        )

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Invalid paipu JSON: {exc}") from exc
    if isinstance(payload, list):
        urls = list(dict.fromkeys(str(value).strip() for value in payload if str(value).strip()))
        if limit and limit > 0:
            urls = urls[:limit]
        return PaipuImport(
            urls=urls,
            records=[{"uuid": "", "paipu_url": url} for url in urls],
            source_format="json-list",
            available_records=len(urls),
        )
    if not isinstance(payload, dict):
        raise ValueError("Unsupported paipu JSON root value")
    schema = str(payload.get("schema") or "")
    if schema == CAPTURE_SCHEMA:
        return _parse_capture(payload, limit)
    if schema == SAFE_EXPORT_SCHEMA or "records" in payload or "urls" in payload:
        return _parse_safe_export(payload, limit)
    raise ValueError(f"Unsupported paipu JSON schema: {schema or '(missing)'}")
