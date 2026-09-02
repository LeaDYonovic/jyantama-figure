import requests
import urllib.parse
import logging
import time
import os
from datetime import datetime, timezone

BASE_URL = 'https://5-data.amae-koromo.com/api/v2/pl4'
OFFSET_2 = [1117113, 1358437]
XOR_CODE_2 = 86216345
REQUEST_HEADERS = {"Accept": "application/json"}
SESSION = requests.Session()


def configure_access_token(token: str | None = None) -> bool:
    """Configure the optional amae-koromo bearer token without logging it."""
    raw_value = os.environ.get("AMAE_KOROMO_TOKEN", "") if token is None else token
    resolved = raw_value.strip()
    if resolved:
        SESSION.headers["Authorization"] = f"Bearer {resolved}"
        return True

    SESSION.headers.pop("Authorization", None)
    return False


configure_access_token()


def _raise_api_error(response, context: str):
    body = (response.text or "").strip()
    if response.status_code == 429 and "x-cap-token-required" in body:
        raise RuntimeError(
            "雀魂牌谱屋当前要求访问令牌。请向牌谱屋维护者申请许可后，"
            "在桌面版中填写令牌，或设置 AMAE_KOROMO_TOKEN 环境变量。"
        )
    try:
        response.raise_for_status()
    except requests.RequestException as exc:
        raise RuntimeError(f"{context}: {exc}") from exc

def acc2match(account_id: int) -> int:
    """
    Convert an account_id into the match_id embedded at the end of a Mahjong Soul paipu URL.
    """
    return ((7 * account_id + OFFSET_2[0]) ^ XOR_CODE_2) + OFFSET_2[1]

def search_player(nickname: str) -> int:
    """
    Search for a player by nickname and return their account_id.
    """
    url = f"{BASE_URL}/search_player/{urllib.parse.quote(nickname)}?limit=20&tag=all"
    try:
        res = SESSION.get(url, timeout=15, headers=REQUEST_HEADERS)
        _raise_api_error(res, f"搜索玩家 '{nickname}' 失败")
        data = res.json()
    except Exception as e:
        raise RuntimeError(f"API request failed while searching for '{nickname}': {e}")
    
    if not isinstance(data, list) or len(data) == 0:
        raise ValueError(f"Player not found: '{nickname}'. The search returned an empty result.")
        
    player = data[0]
    if "id" not in player:
        raise ValueError("Unexpected API response structure: missing 'id' field.")
        
    logging.info(f"[API] Found player: '{player['nickname']}' (account_id={player['id']})")
    return player["id"]

def get_player_nickname_by_id(account_id: int) -> str | None:
    """
    Fetch a player's nickname using their account_id.
    """
    end_ms = int(time.time() * 1000)
    start_ms = 1262304000000
    
    url = f"{BASE_URL}/player_stats/{account_id}/{start_ms}/{end_ms}?mode=16.12.9.15.11.8"
    try:
        res = SESSION.get(url, timeout=15, headers=REQUEST_HEADERS)
        if res.status_code == 404:
            return None
        _raise_api_error(res, f"查询玩家 ID {account_id} 失败")
        data = res.json()
        return data.get("nickname")
    except Exception as e:
        logging.warning(f"[API] Failed to fetch nickname for account_id={account_id}: {e}")
        return None

def get_player_records(account_id: int, limit: int, mode: int) -> list:
    """
    Fetch a player's recent game records for the given mode.
    """
    end_ms = int(time.time() * 1000)
    start_ms = 1262304000000
    
    url = f"{BASE_URL}/player_records/{account_id}/{end_ms}/{start_ms}?limit={limit}&mode={mode}&descending=true"
    try:
        res = SESSION.get(url, timeout=15, headers=REQUEST_HEADERS)
        _raise_api_error(res, f"获取 mode={mode} 对局失败")
        data = res.json()
    except Exception as e:
        raise RuntimeError(f"API request failed while fetching records (mode={mode}): {e}")
        
    if not isinstance(data, list):
        raise ValueError("Unexpected response format for player_records: not a list.")
        
    logging.info(f"[API] Fetched {len(data)} records for mode={mode}")
    return data

def format_timestamp(ts: int) -> str:
    if not ts:
        return ""
    if ts > 1e11:
        ts = ts / 1000.0
    return datetime.fromtimestamp(ts, timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S")

def build_paipu_urls(records: list, account_id: int) -> list:
    """
    Build a list of paipu URLs from game records.
    """
    match_id = 'a' + str(acc2match(account_id))
    results = []
    for rec in records:
        uuid_str = rec.get("uuid")
        if not uuid_str:
            continue
            
        start_time = format_timestamp(rec.get("startTime", 0))
        end_time = format_timestamp(rec.get("endTime", 0))

        players = rec.get("players") or []
        target_player = next(
            (
                player
                for player in players
                if int(player.get("accountId", -1)) == int(account_id)
            ),
            {},
        )
        placement = ""
        if target_player and players:
            ordered_players = sorted(
                enumerate(players),
                key=lambda pair: (-float(pair[1].get("score", 0) or 0), pair[0]),
            )
            for rank_index, (_, player) in enumerate(ordered_players, start=1):
                if int(player.get("accountId", -1)) == int(account_id):
                    placement = rank_index
                    break

        results.append({
            "uuid": uuid_str,
            "matchId": match_id,
            "paipuUrl": f"https://game.maj-soul.com/1/?paipu={uuid_str}_{match_id}",
            "startTime": start_time,
            "endTime": end_time,
            "placement": placement,
            "finalScore": target_player.get("score", ""),
            "ptDelta": target_player.get("gradingScore", ""),
            "playerLevel": target_player.get("level", ""),
        })
    return results
