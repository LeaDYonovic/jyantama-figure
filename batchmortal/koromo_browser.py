from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Callable
from urllib.parse import urlencode

from batchmortal.paipu_import import SAFE_EXPORT_SCHEMA, PaipuImport


KOROMO_BASE_URL = "https://amae-koromo.sapk.ch"
KOROMO_SOUTH_MODES = (9, 12, 16)
BRIDGE_FILE_PREFIX = "koromo-bridge-"


class KoromoDownloadCancelled(RuntimeError):
    pass


def build_koromo_player_url(
    account_id: int,
    modes: tuple[int, ...] = KOROMO_SOUTH_MODES,
    limit: int | None = 100,
    bridge_token: str | None = None,
) -> str:
    """Build the public player page used by the companion userscript."""
    normalized_id = int(account_id)
    if normalized_id <= 0:
        raise ValueError("牌谱屋玩家 ID 必须是正整数")
    normalized_modes = tuple(dict.fromkeys(int(mode) for mode in modes))
    if not normalized_modes or any(mode not in KOROMO_SOUTH_MODES for mode in normalized_modes):
        raise ValueError("牌谱屋桌面导入仅支持金之间、玉之间和王座之间的四人南场")
    path = f"/player/{normalized_id}/" + ".".join(str(mode) for mode in normalized_modes)
    if limit is None:
        url = KOROMO_BASE_URL + path
    else:
        normalized_limit = int(limit)
        if normalized_limit <= 0 or normalized_limit > 5000:
            raise ValueError("下载数量应为 1 到 5000，或选择全部历史")
        url = f"{KOROMO_BASE_URL}{path}?{urlencode({'limit': normalized_limit})}"
    if bridge_token:
        token = str(bridge_token).strip().lower()
        if not token or any(character not in "0123456789abcdef" for character in token):
            raise ValueError("无效的本机导入标识")
        url += f"#batchmortal={token}"
    return url


def _matching_bridge_files(download_dir: Path, bridge_token: str, since: float) -> list[Path]:
    candidates = []
    for path in download_dir.glob(f"{BRIDGE_FILE_PREFIX}{bridge_token}*.json"):
        try:
            if path.is_file() and path.stat().st_mtime >= since - 2:
                candidates.append(path)
        except OSError:
            continue
    return sorted(candidates, key=lambda path: path.stat().st_mtime, reverse=True)


def wait_for_koromo_bridge_export(
    download_dir: str | Path,
    bridge_token: str,
    *,
    since: float,
    progress: Callable[[str], None] | None = None,
    cancel_event: threading.Event | None = None,
    timeout: float = 420.0,
) -> Path:
    """Wait for the companion userscript to download one complete safe JSON."""
    folder = Path(download_dir)
    cancel = cancel_event or threading.Event()
    notify = progress or (lambda _message: None)
    started_at = time.monotonic()
    last_notice = started_at
    last_candidate: Path | None = None
    last_size = -1
    stable_checks = 0

    notify("已在默认浏览器打开牌谱屋；等待 0.8.0 用户脚本下载安全 JSON…")
    while time.monotonic() - started_at < timeout:
        if cancel.is_set():
            raise KoromoDownloadCancelled("已取消从牌谱屋导入")
        candidates = _matching_bridge_files(folder, bridge_token, since)
        candidate = candidates[0] if candidates else None
        if candidate is not None:
            try:
                size = candidate.stat().st_size
            except OSError:
                size = -1
            if candidate == last_candidate and size > 0 and size == last_size:
                stable_checks += 1
            else:
                last_candidate = candidate
                last_size = size
                stable_checks = 0
            if stable_checks >= 2:
                try:
                    payload = json.loads(candidate.read_text(encoding="utf-8-sig"))
                except (OSError, UnicodeError, json.JSONDecodeError):
                    stable_checks = 0
                else:
                    if (
                        isinstance(payload, dict)
                        and payload.get("schema") == SAFE_EXPORT_SCHEMA
                        and isinstance(payload.get("records"), list)
                    ):
                        return candidate

        now = time.monotonic()
        if now - last_notice >= 15:
            notify(
                "仍在等待下载；请保留牌谱屋页面，并确认 Tampermonkey 的 0.8.0 脚本已启用"
            )
            last_notice = now
        time.sleep(0.5)

    raise TimeoutError(
        "未在“下载”文件夹收到牌谱屋 JSON。请安装或更新项目附带的 0.8.0 "
        "Tampermonkey 脚本，再重试；也可以在网页下载后用“导入牌谱 JSON / TXT”手动选择"
    )


def write_koromo_export(
    path: str | Path,
    imported: PaipuImport,
    *,
    modes: tuple[int, ...],
    requested_limit: int | None,
) -> Path:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": SAFE_EXPORT_SCHEMA,
        "source": KOROMO_BASE_URL,
        "account_id": imported.account_id,
        "scope": "all" if requested_limit is None else f"latest-{requested_limit}",
        "modes": list(modes),
        "count": len(imported.records),
        "records": imported.records,
    }
    output_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return output_path
