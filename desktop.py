from __future__ import annotations

import csv
import json
import math
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import uuid
import webbrowser
from datetime import datetime
from pathlib import Path
from statistics import mean
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import matplotlib

matplotlib.use("TkAgg")
matplotlib.rcParams["font.sans-serif"] = [
    "Microsoft YaHei",
    "SimHei",
    "Arial Unicode MS",
    "DejaVu Sans",
]
matplotlib.rcParams["axes.unicode_minus"] = False

from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
from matplotlib.figure import Figure

from batchmortal.results import (
    ResultWriter,
    backfill_result_metadata,
    get_processed_uuids,
    read_result_rows,
)
from batchmortal.paipu_import import (
    PaipuImport,
    normalize_majsoul_paipu_input,
    read_paipu_import,
)
from batchmortal.koromo_browser import (
    KOROMO_SOUTH_MODES,
    KoromoDownloadCancelled,
    build_koromo_player_url,
    wait_for_koromo_bridge_export,
    write_koromo_export,
)
from batchmortal.player_library import (
    assign_player_id,
    discover_player_ids,
    load_player_import,
    merge_player_imports,
    player_result_path,
    save_player_import,
)


APP_NAME = "雀魂 Mortal 牌谱分析器"
APP_VERSION = "0.9.0"
PROJECT_ROOT = Path(__file__).resolve().parent
LOCAL_APPDATA = Path(os.environ.get("LOCALAPPDATA", PROJECT_ROOT))
APP_DATA_ROOT = LOCAL_APPDATA / "MajsoulMortalDesktop"
RESULTS_ROOT = APP_DATA_ROOT / "results"
KOROMO_IMPORTS_ROOT = APP_DATA_ROOT / "imports"
PLAYER_LIBRARY_ROOT = APP_DATA_ROOT / "players"
SETTINGS_PATH = APP_DATA_ROOT / "settings.json"
DIRECT_RESULTS_PATH = RESULTS_ROOT / "majsoul" / "直接牌谱" / "results.xlsx"
RANKED_ROOM_LABELS = {
    2: "铜之间·南",
    5: "银之间·南",
    9: "金之间·南",
    12: "玉之间·南",
    16: "王座之间·南",
}
ANALYSIS_MODE_OPTIONS = ("仅段位场", "仅友人场", "全部可分析")
VIEW_MODE_OPTIONS = (
    "全部模式",
    "仅段位场",
    "仅友人场",
    *RANKED_ROOM_LABELS.values(),
    "未知/旧导入",
)


def _record_type(record: dict) -> str:
    value = str(
        record.get("recordType") or record.get("record_type") or "unknown"
    ).strip().lower()
    return value if value in {"ranked", "friend", "match", "activity"} else "unknown"


def _record_mode_id(record: dict) -> int | None:
    value = _first_value(record, "mode", "room", "mode_id", "modeId")
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return int(parsed) if parsed.is_integer() else None


def _record_mode_label(record: dict) -> str:
    record_type = _record_type(record)
    mode_id = _record_mode_id(record)
    if record_type == "ranked":
        return RANKED_ROOM_LABELS.get(mode_id, f"段位场（{mode_id or '未知'}）")
    if record_type == "friend":
        return "友人场·南" if mode_id == 2 else "友人场"
    if record_type == "match":
        return "赛事场"
    if record_type == "activity":
        return "活动场"
    raw_mode = _first_value(record, "mode", "room", "mode_id", "modeId")
    return f"未知（{raw_mode}）" if str(raw_mode).strip() else "未知/旧导入"


def _matches_mode_filter(record: dict, label: str) -> bool:
    record_type = _record_type(record)
    if label == "全部模式":
        return True
    if label == "仅段位场":
        return record_type == "ranked"
    if label == "仅友人场":
        return record_type == "friend"
    if label == "未知/旧导入":
        return record_type == "unknown"
    room_id = next(
        (mode_id for mode_id, room_label in RANKED_ROOM_LABELS.items() if room_label == label),
        None,
    )
    return record_type == "ranked" and _record_mode_id(record) == room_id


def _matches_analysis_mode(record: dict, label: str) -> bool:
    if label == "仅段位场":
        return _record_type(record) == "ranked"
    if label == "仅友人场":
        return _record_type(record) == "friend"
    return True


def _classify_direct_urls(
    urls: list[str], account_id: int | None, processed_uuids: set[str]
) -> tuple[list[str], list[str]]:
    completed: list[str] = []
    pending: list[str] = []
    for url in urls:
        try:
            _, direct_id = normalize_majsoul_paipu_input(url, account_id=account_id)
        except ValueError:
            pending.append(url)
            continue
        if direct_id in processed_uuids:
            completed.append(url)
        else:
            pending.append(url)
    return completed, pending


def _imported_metadata_by_uuid(
    records: list[dict], account_id: int | None
) -> dict[str, dict]:
    """Build the safe metadata map used to update existing result rows."""
    metadata_by_uuid: dict[str, dict] = {}
    for record in records:
        raw_url = str(record.get("paipu_url") or record.get("paipuUrl") or "").strip()
        if not raw_url:
            continue
        try:
            normalized_url, direct_id = normalize_majsoul_paipu_input(
                raw_url,
                account_id=account_id,
            )
        except ValueError:
            continue
        metadata_by_uuid[direct_id] = {
            "accountId": account_id or "",
            "mode": record.get("mode_id", record.get("modeId", "")),
            "recordType": record.get("record_type", record.get("recordType", "unknown")),
            "paipuUrl": normalized_url,
            "startTime": record.get("start_time", record.get("startTime", "")),
            "endTime": record.get("end_time", record.get("endTime", "")),
            "placement": record.get("placement", ""),
            "finalScore": record.get("final_score", record.get("finalScore", "")),
            "ptDelta": record.get("pt_delta", record.get("ptDelta", "")),
            "playerLevel": record.get("player_level", record.get("playerLevel", "")),
            "playerLevelScore": record.get(
                "player_level_score",
                record.get("playerLevelScore", ""),
            ),
        }
    return metadata_by_uuid


def _running_analysis_pids() -> list[int]:
    if sys.platform != "win32":
        return []
    try:
        completed = subprocess.run(
            [
                "tasklist",
                "/FI",
                "IMAGENAME eq batchmortal-cli.exe",
                "/FO",
                "CSV",
                "/NH",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        pids = []
        for row in csv.reader(completed.stdout.splitlines()):
            if len(row) >= 2 and row[0].lower() == "batchmortal-cli.exe":
                try:
                    pids.append(int(row[1]))
                except ValueError:
                    continue
        return pids
    except OSError:
        return []

def _set_windows_dpi_awareness():
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def _to_float(value) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace("%", "")
    if not text or text.upper() == "ERROR":
        return None
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


def _first_value(row: dict, *keys: str):
    for key in keys:
        value = row.get(key)
        if value is not None and str(value).strip() != "":
            return value
    return ""


def _parse_datetime(value) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        timestamp = float(text)
        if math.isfinite(timestamp) and timestamp > 0:
            if timestamp > 1e11:
                timestamp /= 1000
            return datetime.fromtimestamp(timestamp)
    except ValueError:
        pass
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    for parser in (
        lambda: datetime.fromisoformat(text),
        lambda: datetime.strptime(text, "%Y-%m-%d %H:%M:%S"),
        lambda: datetime.strptime(text, "%Y/%m/%d %H:%M:%S"),
    ):
        try:
            parsed = parser()
            return parsed.replace(tzinfo=None) if parsed.tzinfo else parsed
        except ValueError:
            continue
    return None


def _moving_average(values: list[float | None], window: int = 10) -> list[float | None]:
    result: list[float | None] = []
    for index in range(len(values)):
        if index + 1 < window:
            result.append(None)
            continue
        sample = values[index + 1 - window : index + 1]
        if any(value is None for value in sample):
            result.append(None)
        else:
            result.append(mean(value for value in sample if value is not None))
    return result


def _row_percentage(row: dict, rate_key: str, count_key: str) -> float | None:
    count = _to_float(row.get(count_key))
    denominator = _to_float(row.get("badMoveDenominator"))
    if count is not None and denominator is not None and denominator > 0:
        return count / denominator * 100
    return _to_float(row.get(rate_key))


def _aggregate_percentage(
    rows: list[dict], rate_key: str, numerator_key: str, denominator_key: str
) -> float | None:
    weighted = []
    for row in rows:
        numerator = _to_float(row.get(numerator_key))
        denominator = _to_float(row.get(denominator_key))
        if numerator is not None and denominator is not None and denominator > 0:
            weighted.append((numerator, denominator))
    if weighted:
        denominator = sum(pair[1] for pair in weighted)
        return sum(pair[0] for pair in weighted) / denominator * 100
    values = [
        value
        for value in (_to_float(row.get(rate_key)) for row in rows)
        if value is not None
    ]
    return mean(values) if values else None


def _quantile(values: list[float], probability: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("quantile requires at least one value")
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _valid_result_rows(rows: list[dict]) -> list[dict]:
    return [
        row
        for row in rows
        if _to_float(_first_value(row, "rating", "Rating")) is not None
    ]


def _long_sessions(rows: list[dict]) -> list[tuple[datetime, datetime, int]]:
    times = sorted(
        parsed
        for parsed in (_parse_datetime(_first_value(row, "startTime", "start_time", "date")) for row in rows)
        if parsed is not None
    )
    if not times:
        return []

    sessions: list[list[datetime]] = [[times[0]]]
    for current in times[1:]:
        gap_minutes = (current - sessions[-1][-1]).total_seconds() / 60
        if gap_minutes <= 90:
            sessions[-1].append(current)
        else:
            sessions.append([current])

    return [
        (session[0], session[-1], len(session))
        for session in sessions
        if len(session) >= 2 and (session[-1] - session[0]).total_seconds() >= 4 * 3600
    ]


def _safe_result_url(row: dict) -> str:
    return str(_first_value(row, "resultUrl", "result_url", "paipuUrl", "mjai_url"))


class MortalDesktopApp:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title(f"{APP_NAME} {APP_VERSION}")
        self.root.geometry("1380x900")
        self.root.minsize(1100, 720)

        APP_DATA_ROOT.mkdir(parents=True, exist_ok=True)
        RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
        PLAYER_LIBRARY_ROOT.mkdir(parents=True, exist_ok=True)

        self.message_queue: queue.Queue = queue.Queue()
        self.process: subprocess.Popen | None = None
        self.stop_file_path: Path | None = None
        self.stop_requested = False
        self.koromo_download_running = False
        self.koromo_cancel_event: threading.Event | None = None
        self.direct_urls: list[str] = []
        self.direct_records: list[dict] = []
        self.direct_account_id: int | None = None
        self.direct_source_name = ""
        self.direct_skipped_non_hanchan = 0
        self.direct_file_path: Path | None = None
        self.rows: list[dict] = []
        self.current_result_path: Path | None = None
        self.player_label_to_id: dict[str, int | None] = {}
        self.settings = self._load_settings()

        self._configure_style()
        self._build_variables()
        self._build_menu()
        self._build_layout()
        self._draw_empty_chart()
        self._refresh_player_selector(load_saved=True)
        self._poll_messages()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_style(self):
        style = ttk.Style(self.root)
        available = style.theme_names()
        if "vista" in available:
            style.theme_use("vista")
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 17, "bold"))
        style.configure("Subtitle.TLabel", foreground="#5f6b7a")
        style.configure("CardValue.TLabel", font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("CardTitle.TLabel", foreground="#667085")
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("Treeview", rowheight=27)

    def _build_variables(self):
        self.player_selector_var = tk.StringVar(value="尚无本地玩家")
        self.direct_count_var = tk.StringVar(value="尚未导入牌谱")
        self.limit_var = tk.StringVar(value=str(self.settings.get("limit", 10)))
        self.model_var = tk.StringVar(value=self.settings.get("model", "4.1b"))
        self.badmove_var = tk.BooleanVar(value=self.settings.get("badmove", True))
        self.headless_var = tk.BooleanVar(value=self.settings.get("headless", False))
        saved_analysis_mode = self.settings.get("analysis_mode", "仅段位场")
        if saved_analysis_mode not in ANALYSIS_MODE_OPTIONS:
            saved_analysis_mode = "仅段位场"
        self.analysis_mode_var = tk.StringVar(value=saved_analysis_mode)
        self.view_limit_var = tk.StringVar(value="最近10场")
        self.view_mode_var = tk.StringVar(value="全部模式")
        self.status_var = tk.StringVar(value="就绪")

        self.card_count_var = tk.StringVar(value="—")
        self.card_rating_var = tk.StringVar(value="—")
        self.card_ai_var = tk.StringVar(value="—")
        self.card_pt_var = tk.StringVar(value="—")
        self.card_session_var = tk.StringVar(value="—")

    def _build_menu(self):
        menu = tk.Menu(self.root)
        file_menu = tk.Menu(menu, tearoff=False)
        file_menu.add_command(label="导入本地牌谱数据…", command=self.import_paipu_file)
        file_menu.add_command(label="从牌谱屋按 ID 导入…", command=self.open_koromo_import_dialog)
        file_menu.add_separator()
        file_menu.add_command(
            label="导入已有分析结果到玩家…",
            command=self.import_results_to_player,
        )
        file_menu.add_command(label="打开分析结果 CSV / XLSX…", command=self.import_results)
        file_menu.add_command(label="导出当前图表…", command=self.export_chart)
        file_menu.add_separator()
        file_menu.add_command(label="打开结果目录", command=self.open_results_folder)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self._on_close)
        menu.add_cascade(label="文件", menu=file_menu)

        help_menu = tk.Menu(menu, tearoff=False)
        help_menu.add_command(
            label="Mortal Rating 说明",
            command=lambda: webbrowser.open("https://mjai.ekyu.moe/zh-cn.html"),
        )
        help_menu.add_separator()
        help_menu.add_command(label="关于", command=self.show_about)
        menu.add_cascade(label="帮助", menu=help_menu)
        self.root.config(menu=menu)

    def _build_layout(self):
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(0, weight=1)

        self._build_sidebar(outer)
        self._build_main_panel(outer)

    def _build_sidebar(self, parent):
        sidebar = ttk.Frame(parent, width=300)
        sidebar.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        sidebar.grid_propagate(False)

        ttk.Label(sidebar, text="赛后牌谱分析", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            sidebar,
            text="批量调用 Mortal 4.1，汇总 Rating、\n一致率、恶手率和段位 PT。",
            style="Subtitle.TLabel",
            justify=tk.LEFT,
        ).pack(anchor="w", pady=(4, 14))

        target = ttk.LabelFrame(sidebar, text="导入牌谱", padding=10)
        target.pack(fill=tk.X, pady=(0, 10))
        ttk.Label(target, text="当前玩家").pack(anchor="w")
        self.player_selector = ttk.Combobox(
            target,
            textvariable=self.player_selector_var,
            state="readonly",
        )
        self.player_selector.pack(fill=tk.X, pady=(2, 7))
        self.player_selector.bind("<<ComboboxSelected>>", self._on_player_selected)
        self.import_file_button = ttk.Button(
            target,
            text="导入本地牌谱数据…",
            style="Accent.TButton",
            command=self.import_paipu_file,
        )
        self.import_file_button.pack(fill=tk.X, ipady=4)
        self.koromo_import_button = ttk.Button(
            target,
            text="从牌谱屋按玩家 ID 导入…",
            command=self.open_koromo_import_dialog,
        )
        self.koromo_import_button.pack(fill=tk.X, pady=(7, 0))
        ttk.Label(
            target,
            text="可导入网页脚本 JSON，或查询牌谱屋公开段位场",
            style="Subtitle.TLabel",
        ).pack(anchor="w", pady=(5, 0))
        ttk.Label(
            target,
            textvariable=self.direct_count_var,
            style="Subtitle.TLabel",
            wraplength=260,
        ).pack(anchor="w", pady=(2, 7))
        ttk.Label(target, text="本次分析模式").pack(anchor="w")
        analysis_mode_combo = ttk.Combobox(
            target,
            textvariable=self.analysis_mode_var,
            values=ANALYSIS_MODE_OPTIONS,
            state="readonly",
        )
        analysis_mode_combo.pack(fill=tk.X, pady=(2, 7))
        analysis_mode_combo.bind(
            "<<ComboboxSelected>>",
            lambda _event: self._refresh_direct_preflight(),
        )
        self.refresh_pt_button = ttk.Button(
            target,
            text="仅补全 PT（不调用 Mortal）",
            command=self.refresh_pt_only,
        )
        self.refresh_pt_button.pack(fill=tk.X, pady=(0, 7))
        ttk.Button(
            target,
            text="手动粘贴牌谱链接…",
            command=self.open_direct_urls_dialog,
        ).pack(fill=tk.X)

        row = ttk.Frame(target)
        row.pack(fill=tk.X, pady=(7, 0))
        left = ttk.Frame(row)
        right = ttk.Frame(row)
        left.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 5))
        right.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(5, 0))
        ttk.Label(left, text="本次最多新增分析").pack(anchor="w")
        ttk.Combobox(
            left,
            textvariable=self.limit_var,
            values=["10", "20", "50", "100"],
            state="readonly",
            width=8,
        ).pack(fill=tk.X)
        ttk.Label(right, text="Mortal 模型").pack(anchor="w")
        ttk.Combobox(
            right,
            textvariable=self.model_var,
            values=["4.1a", "4.1b", "4.1c", "3.0"],
            state="readonly",
            width=8,
        ).pack(fill=tk.X)

        options = ttk.LabelFrame(sidebar, text="分析选项", padding=10)
        options.pack(fill=tk.X, pady=(0, 10))
        ttk.Checkbutton(
            options,
            text="统计 5% / 10% 恶手率",
            variable=self.badmove_var,
        ).pack(anchor="w")
        ttk.Checkbutton(
            options,
            text="后台运行 Chrome（无法手动完成人机验证）",
            variable=self.headless_var,
        ).pack(anchor="w", pady=(4, 0))

        self.start_button = ttk.Button(
            sidebar,
            text="开始分析",
            style="Accent.TButton",
            command=self.start_analysis,
        )
        self.start_button.pack(fill=tk.X, ipady=5)
        self.stop_button = ttk.Button(
            sidebar, text="停止", command=self.stop_analysis, state=tk.DISABLED
        )
        self.stop_button.pack(fill=tk.X, pady=(7, 0))
        ttk.Button(sidebar, text="打开已有分析结果…", command=self.import_results).pack(
            fill=tk.X, pady=(7, 0)
        )
        ttk.Button(
            sidebar,
            text="导入已有结果到玩家…",
            command=self.import_results_to_player,
        ).pack(fill=tk.X, pady=(7, 0))

        ttk.Separator(sidebar).pack(fill=tk.X, pady=12)
        ttk.Label(sidebar, textvariable=self.status_var, wraplength=280).pack(anchor="w")
        self.progress = ttk.Progressbar(sidebar, mode="indeterminate")
        self.progress.pack(fill=tk.X, pady=(6, 0))

    def _build_main_panel(self, parent):
        main = ttk.Frame(parent)
        main.grid(row=0, column=1, sticky="nsew")
        main.rowconfigure(1, weight=1)
        main.columnconfigure(0, weight=1)

        cards = ttk.Frame(main)
        cards.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        for index in range(5):
            cards.columnconfigure(index, weight=1)
        card_specs = [
            ("有效半庄", self.card_count_var),
            ("平均 Rating", self.card_rating_var),
            ("AI 一致率", self.card_ai_var),
            ("累计 PT", self.card_pt_var),
            ("≥4h 连打", self.card_session_var),
        ]
        for index, (title, variable) in enumerate(card_specs):
            frame = ttk.LabelFrame(cards, padding=(12, 8))
            frame.grid(row=0, column=index, sticky="ew", padx=(0 if index == 0 else 5, 0))
            ttk.Label(frame, text=title, style="CardTitle.TLabel").pack(anchor="w")
            ttk.Label(frame, textvariable=variable, style="CardValue.TLabel").pack(anchor="w")

        self.tabs = ttk.Notebook(main)
        self.tabs.grid(row=1, column=0, sticky="nsew")

        chart_tab = ttk.Frame(self.tabs)
        table_tab = ttk.Frame(self.tabs)
        log_tab = ttk.Frame(self.tabs)
        self.tabs.add(chart_tab, text="趋势图")
        self.tabs.add(table_tab, text="牌谱明细")
        self.tabs.add(log_tab, text="运行日志")

        toolbar_row = ttk.Frame(chart_tab, padding=(8, 6))
        toolbar_row.pack(fill=tk.X)
        ttk.Label(toolbar_row, text="显示范围：").pack(side=tk.LEFT)
        view_combo = ttk.Combobox(
            toolbar_row,
            textvariable=self.view_limit_var,
            values=["最近10场", "最近20场", "最近50场", "全部"],
            state="readonly",
            width=11,
        )
        view_combo.pack(side=tk.LEFT)
        view_combo.bind("<<ComboboxSelected>>", lambda _event: self.redraw())
        ttk.Label(toolbar_row, text="模式：").pack(side=tk.LEFT, padx=(12, 0))
        mode_combo = ttk.Combobox(
            toolbar_row,
            textvariable=self.view_mode_var,
            values=VIEW_MODE_OPTIONS,
            state="readonly",
            width=13,
        )
        mode_combo.pack(side=tk.LEFT)
        mode_combo.bind("<<ComboboxSelected>>", lambda _event: self.redraw())
        ttk.Button(toolbar_row, text="刷新", command=self.redraw).pack(side=tk.LEFT, padx=6)
        ttk.Button(toolbar_row, text="导出 PNG", command=self.export_chart).pack(side=tk.LEFT)

        self.figure = Figure(figsize=(10, 7), dpi=100, constrained_layout=True)
        self.canvas = FigureCanvasTkAgg(self.figure, master=chart_tab)
        self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
        nav = NavigationToolbar2Tk(self.canvas, chart_tab, pack_toolbar=False)
        nav.update()
        nav.pack(fill=tk.X)

        table_tab.rowconfigure(0, weight=1)
        table_tab.columnconfigure(0, weight=1)
        columns = ("index", "time", "room", "placement", "score", "rating", "ai", "pt")
        self.table = ttk.Treeview(table_tab, columns=columns, show="headings")
        headings = {
            "index": "序号",
            "time": "开局时间",
            "room": "模式",
            "placement": "顺位",
            "score": "终局点数",
            "rating": "Rating",
            "ai": "一致率",
            "pt": "PT",
        }
        widths = {"index": 60, "time": 175, "room": 115, "placement": 70, "score": 90, "rating": 90, "ai": 90, "pt": 90}
        for column in columns:
            self.table.heading(column, text=headings[column])
            self.table.column(column, width=widths[column], anchor=tk.CENTER)
        self.table.grid(row=0, column=0, sticky="nsew")
        table_scroll = ttk.Scrollbar(table_tab, orient=tk.VERTICAL, command=self.table.yview)
        table_scroll.grid(row=0, column=1, sticky="ns")
        self.table.configure(yscrollcommand=table_scroll.set)
        self.table.bind("<Double-1>", self._open_selected_result)

        self.log_text = tk.Text(
            log_tab,
            wrap=tk.WORD,
            bg="#101828",
            fg="#e4e7ec",
            insertbackground="white",
            font=("Cascadia Mono", 10),
        )
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.configure(state=tk.DISABLED)

    def _draw_empty_chart(self):
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        ax.axis("off")
        ax.text(
            0.5,
            0.55,
            "尚未加载分析结果",
            ha="center",
            va="center",
            fontsize=18,
            color="#475467",
        )
        ax.text(
            0.5,
            0.46,
            "先在左侧导入牌谱 JSON / TXT，再开始分析",
            ha="center",
            va="center",
            fontsize=11,
            color="#98a2b3",
        )
        self.canvas.draw_idle()

    def _load_settings(self) -> dict:
        try:
            return json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _save_settings(self):
        APP_DATA_ROOT.mkdir(parents=True, exist_ok=True)
        payload = {
            "limit": int(self.limit_var.get() or 10),
            "model": self.model_var.get(),
            "badmove": self.badmove_var.get(),
            "headless": self.headless_var.get(),
            "analysis_mode": self.analysis_mode_var.get(),
            "koromo_player_id": str(self.settings.get("koromo_player_id", "")),
            "active_player_id": self.direct_account_id,
        }
        SETTINGS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _active_result_path(self) -> Path:
        if self.direct_account_id:
            return player_result_path(RESULTS_ROOT, self.direct_account_id)
        return DIRECT_RESULTS_PATH

    @staticmethod
    def _player_label(account_id: int, count: int | None) -> str:
        suffix = f"{count} 局牌谱" if count is not None else "仅有分析结果"
        return f"玩家 ID {account_id} · {suffix}"

    def _refresh_player_selector(
        self,
        *,
        select_account_id: int | None = None,
        load_saved: bool = False,
    ):
        ids = discover_player_ids(PLAYER_LIBRARY_ROOT, RESULTS_ROOT)
        labels: list[str] = []
        mapping: dict[str, int | None] = {}
        for account_id in ids:
            imported = None
            try:
                imported = load_player_import(PLAYER_LIBRARY_ROOT, account_id)
            except (OSError, UnicodeError, ValueError) as exc:
                self._log(f"[玩家库] 无法读取玩家 {account_id} 的牌谱库：{exc}")
            label = self._player_label(
                account_id,
                len(imported.urls) if imported is not None else None,
            )
            labels.append(label)
            mapping[label] = account_id

        if DIRECT_RESULTS_PATH.is_file():
            legacy_label = "未分组 / 旧数据"
            labels.append(legacy_label)
            mapping[legacy_label] = None

        if not labels:
            labels = ["尚无本地玩家"]
            mapping[labels[0]] = None

        self.player_label_to_id = mapping
        self.player_selector.configure(values=labels)

        desired_id = select_account_id
        if load_saved and desired_id is None:
            saved = self.settings.get("active_player_id")
            try:
                desired_id = int(saved) if saved else None
            except (TypeError, ValueError):
                desired_id = None
        desired_label = next(
            (label for label, account_id in mapping.items() if account_id == desired_id),
            labels[0],
        )
        self.player_selector_var.set(desired_label)
        if load_saved and desired_label != "尚无本地玩家":
            self._load_player_selection(mapping[desired_label])

    def _on_player_selected(self, _event=None):
        if self.process is not None or self.koromo_download_running:
            messagebox.showinfo(
                "任务正在运行",
                "请先等待当前任务结束，再切换玩家。",
                parent=self.root,
            )
            self._refresh_player_selector(select_account_id=self.direct_account_id)
            return
        label = self.player_selector_var.get()
        if label == "尚无本地玩家":
            return
        self._load_player_selection(self.player_label_to_id.get(label))
        self._save_settings()

    def _clear_result_view(self):
        self.rows = []
        self.current_result_path = None
        for variable in (
            self.card_count_var,
            self.card_rating_var,
            self.card_ai_var,
            self.card_pt_var,
            self.card_session_var,
        ):
            variable.set("—")
        for item in self.table.get_children():
            self.table.delete(item)
        self._draw_empty_chart()

    def _load_player_selection(self, account_id: int | None):
        self.direct_urls = []
        self.direct_records = []
        self.direct_account_id = account_id
        self.direct_skipped_non_hanchan = 0
        if account_id is None:
            self.direct_source_name = "未分组 / 旧数据"
            self.direct_count_var.set("未分组旧结果仅供查看；导入带 ID 的牌谱后可归档")
        else:
            imported = load_player_import(PLAYER_LIBRARY_ROOT, account_id)
            if imported is not None:
                self._apply_paipu_import(
                    imported,
                    f"本地玩家库 · ID {account_id}",
                    persist=False,
                    refresh_selector=False,
                )
            else:
                self.direct_source_name = f"玩家 ID {account_id}"
                self.direct_count_var.set("此玩家目前只有分析结果，没有本地牌谱库")

        result_path = self._active_result_path()
        if result_path.is_file():
            self.load_result_file(result_path)
            self.tabs.select(0)
            self.status_var.set(
                f"正在查看玩家 ID {account_id}"
                if account_id is not None
                else "正在查看未分组旧数据"
            )
        else:
            self._clear_result_view()
            if account_id is not None and self.direct_urls:
                self._refresh_direct_preflight()

    def _store_player_import(self, imported: PaipuImport) -> tuple[PaipuImport, Path]:
        if imported.account_id is None:
            raise ValueError("导入数据没有玩家 ID")
        existing = load_player_import(PLAYER_LIBRARY_ROOT, imported.account_id)
        merged = merge_player_imports(existing, imported)
        path = save_player_import(PLAYER_LIBRARY_ROOT, merged)
        return merged, path

    def _adopt_legacy_results_for_player(self, imported: PaipuImport) -> int:
        if imported.account_id is None or not DIRECT_RESULTS_PATH.is_file():
            return 0
        target = player_result_path(RESULTS_ROOT, imported.account_id)
        try:
            imported_ids = set(
                _imported_metadata_by_uuid(imported.records, imported.account_id)
            )
            existing_ids = {
                str(row.get("uuid") or "").strip()
                for row in read_result_rows(str(target), "xlsx")
            }
            rows = [
                row
                for row in read_result_rows(str(DIRECT_RESULTS_PATH), "xlsx")
                if str(row.get("uuid") or "").strip() in imported_ids
                and str(row.get("uuid") or "").strip() not in existing_ids
            ]
            if not rows:
                return 0
            with ResultWriter(str(target), output_format="xlsx", flush_every=20) as writer:
                for row in rows:
                    writer.write_row(
                        {
                            **row,
                            "nickname": f"玩家_{imported.account_id}",
                            "accountId": imported.account_id,
                        }
                    )
            self._log(
                f"[玩家库] 已从未分组旧结果复制 {len(rows)} 局到玩家 ID "
                f"{imported.account_id}；旧文件仍保留。"
            )
            return len(rows)
        except Exception as exc:
            self._log(f"[玩家库] 复用未分组旧结果失败，将保持原文件不变：{exc}")
            return 0

    def _backfill_imported_result_metadata(self) -> int:
        result_path = self._active_result_path()
        if not self.direct_records or not result_path.is_file():
            return 0
        try:
            changed_rows = backfill_result_metadata(
                str(result_path),
                "xlsx",
                _imported_metadata_by_uuid(
                    self.direct_records,
                    self.direct_account_id,
                ),
            )
            if changed_rows:
                self.load_result_file(result_path)
                self._log(
                    f"[桌面版] 已按牌谱 UUID 为 {changed_rows} 条已有结果补全类型/PT 等元数据。"
                )
            return changed_rows
        except Exception as exc:
            self._log(f"[桌面版] 导入成功，但自动补全已有结果元数据失败：{exc}")
            return 0

    def import_paipu_file(self):
        if self.koromo_download_running:
            messagebox.showinfo(
                "牌谱屋正在导入",
                "请等待当前牌谱屋下载完成。",
                parent=self.root,
            )
            return
        filename = filedialog.askopenfilename(
            parent=self.root,
            title="导入雀魂牌谱",
            filetypes=[
                ("雀魂脚本导出文件", "*.json *.txt"),
                ("JSON 文件", "*.json"),
                ("链接清单", "*.txt"),
                ("所有文件", "*.*"),
            ],
        )
        if not filename:
            return
        try:
            imported = read_paipu_import(filename)
        except (OSError, UnicodeError, ValueError) as exc:
            messagebox.showerror("无法导入牌谱", str(exc), parent=self.root)
            return

        if imported.account_id is None:
            entered_id = simpledialog.askstring(
                "归入玩家",
                "这个文件没有可识别的玩家 ID。\n\n"
                "请输入要归入的雀魂玩家 ID；点击取消则作为未分组临时数据使用。",
                parent=self.root,
            )
            if entered_id:
                try:
                    imported = assign_player_id(imported, entered_id)
                except ValueError as exc:
                    messagebox.showerror("玩家 ID 无效", str(exc), parent=self.root)
                    return

        self._apply_paipu_import(imported, Path(filename).name, persist=True)
        if imported.contains_login_frames:
            messagebox.showwarning(
                "旧抓包含登录帧",
                "已仅在本机内存中提取最近牌谱，登录帧不会写入分析结果。\n\n"
                "这个旧 JSON 可能包含 OAuth/access token，请勿分享，用完后建议删除。",
                parent=self.root,
            )

    def _apply_paipu_import(
        self,
        imported: PaipuImport,
        source_name: str,
        *,
        persist: bool = False,
        refresh_selector: bool = True,
    ):
        library_path = None
        if persist and imported.account_id is not None:
            imported, library_path = self._store_player_import(imported)
            self._adopt_legacy_results_for_player(imported)
        self.direct_urls = imported.urls
        self.direct_records = imported.records
        self.direct_account_id = imported.account_id
        self.direct_source_name = source_name
        self.direct_skipped_non_hanchan = imported.skipped_non_hanchan
        imported_types = {
            _record_type(record)
            for record in self.direct_records
            if _record_type(record) != "unknown"
        }
        if imported_types == {"ranked"}:
            self.analysis_mode_var.set("仅段位场")
        elif imported_types == {"friend"}:
            self.analysis_mode_var.set("仅友人场")
        elif not imported_types:
            self.analysis_mode_var.set("全部可分析")
        self._backfill_imported_result_metadata()
        result_path = self._active_result_path()
        if result_path.is_file():
            self.load_result_file(result_path)
        elif self.current_result_path != result_path:
            self._clear_result_view()
        self._refresh_direct_preflight()
        if refresh_selector:
            self._refresh_player_selector(select_account_id=imported.account_id)
        if library_path is not None:
            self._log(
                f"[玩家库] 玩家 ID {imported.account_id} 已保存，共 "
                f"{len(imported.urls)} 局：{library_path}"
            )
        self._save_settings()

    def open_koromo_import_dialog(self):
        if self.process is not None or self.koromo_download_running:
            messagebox.showinfo(
                "任务正在运行",
                "请先等待当前导入或分析任务结束。",
                parent=self.root,
            )
            return

        dialog = tk.Toplevel(self.root)
        dialog.title("从雀魂牌谱屋导入")
        dialog.geometry("520x390")
        dialog.minsize(480, 360)
        dialog.transient(self.root)
        dialog.grab_set()

        body = ttk.Frame(dialog, padding=16)
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=1)
        ttk.Label(
            body,
            text="输入牌谱屋玩家 ID，下载该玩家公开的四人南场牌谱。",
            font=("Microsoft YaHei UI", 11, "bold"),
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            body,
            text="需要安装本项目 0.8.0 用户脚本。程序会用默认浏览器打开牌谱屋，并自动接收安全 JSON。",
            style="Subtitle.TLabel",
            wraplength=470,
        ).grid(row=1, column=0, sticky="w", pady=(5, 14))

        ttk.Label(body, text="牌谱屋玩家 ID").grid(row=2, column=0, sticky="w")
        player_id_var = tk.StringVar(
            value=str(self.settings.get("koromo_player_id", ""))
        )
        player_entry = ttk.Entry(body, textvariable=player_id_var)
        player_entry.grid(row=3, column=0, sticky="ew", pady=(3, 10))

        ttk.Label(body, text="房间").grid(row=4, column=0, sticky="w")
        room_labels = {
            "金/玉/王座之间·南（推荐）": KOROMO_SOUTH_MODES,
            "王座之间·南": (16,),
            "玉之间·南": (12,),
            "金之间·南": (9,),
        }
        room_var = tk.StringVar(value=next(iter(room_labels)))
        ttk.Combobox(
            body,
            textvariable=room_var,
            values=tuple(room_labels),
            state="readonly",
        ).grid(row=5, column=0, sticky="ew", pady=(3, 10))

        ttk.Label(body, text="下载范围").grid(row=6, column=0, sticky="w")
        range_labels = {
            "最近 100 局": 100,
            "最近 300 局": 300,
            "最近 500 局": 500,
            "最近 1000 局": 1000,
            "全部公开历史": None,
        }
        range_var = tk.StringVar(value="最近 100 局")
        ttk.Combobox(
            body,
            textvariable=range_var,
            values=tuple(range_labels),
            state="readonly",
        ).grid(row=7, column=0, sticky="ew", pady=(3, 8))
        ttk.Label(
            body,
            text="牌谱屋只收录金之间及以上公开段位场，数据可能有延迟；友人场请继续用网页脚本导出。",
            style="Subtitle.TLabel",
            wraplength=470,
        ).grid(row=8, column=0, sticky="w")

        buttons = ttk.Frame(body)
        buttons.grid(row=9, column=0, sticky="ew", pady=(18, 0))

        def begin_import():
            value = player_id_var.get().strip()
            if not value.isdigit() or int(value) <= 0:
                messagebox.showwarning(
                    "玩家 ID 无效",
                    "请输入牌谱屋玩家页面网址中的数字 ID。",
                    parent=dialog,
                )
                return
            account_id = int(value)
            modes = room_labels[room_var.get()]
            limit = range_labels[range_var.get()]
            self.settings["koromo_player_id"] = value
            dialog.destroy()
            self._start_koromo_import(account_id, modes, limit)

        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side=tk.RIGHT)
        ttk.Button(
            buttons,
            text="打开牌谱屋并导入",
            style="Accent.TButton",
            command=begin_import,
        ).pack(side=tk.RIGHT, padx=(0, 8))
        dialog.bind("<Return>", lambda _event: begin_import())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        player_entry.focus_set()

    def _start_koromo_import(
        self,
        account_id: int,
        modes: tuple[int, ...],
        limit: int | None,
    ):
        self.koromo_download_running = True
        self.koromo_cancel_event = threading.Event()
        self.player_selector.configure(state=tk.DISABLED)
        self.koromo_import_button.configure(state=tk.DISABLED)
        self.import_file_button.configure(state=tk.DISABLED)
        self.start_button.configure(state=tk.DISABLED)
        self.refresh_pt_button.configure(state=tk.DISABLED)
        self.stop_button.configure(text="取消导入", state=tk.NORMAL)
        self.progress.start(12)
        self.status_var.set("正在从牌谱屋下载公开牌谱…")
        self.tabs.select(2)
        self._log("=" * 72)
        range_text = "全部公开历史" if limit is None else f"最近 {limit} 局"
        self._log(
            f"[牌谱屋] 玩家 ID {account_id}；房间 {','.join(map(str, modes))}；"
            f"范围 {range_text}。"
        )
        self._log("[牌谱屋] 将打开默认浏览器；验证由牌谱屋页面正常完成。")

        bridge_token = uuid.uuid4().hex
        opened_at = time.time()
        download_dir = Path.home() / "Downloads"
        player_url = build_koromo_player_url(
            account_id,
            modes=modes,
            limit=limit,
            bridge_token=bridge_token,
        )
        webbrowser.open(player_url)

        def worker():
            try:
                downloaded_path = wait_for_koromo_bridge_export(
                    download_dir,
                    bridge_token,
                    since=opened_at,
                    progress=lambda message: self.message_queue.put(
                        ("koromo_progress", message)
                    ),
                    cancel_event=self.koromo_cancel_event,
                )
                imported = read_paipu_import(downloaded_path)
                if imported.account_id != account_id:
                    raise ValueError("下载文件中的玩家 ID 与本次查询不一致")
                timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                path = KOROMO_IMPORTS_ROOT / f"koromo-{account_id}-{timestamp}.json"
                write_koromo_export(
                    path,
                    imported,
                    modes=modes,
                    requested_limit=limit,
                )
                self.message_queue.put(("koromo_done", (imported, path)))
            except KoromoDownloadCancelled as exc:
                self.message_queue.put(("koromo_cancelled", str(exc)))
            except Exception as exc:
                self.message_queue.put(("koromo_error", str(exc)))

        threading.Thread(target=worker, daemon=True).start()

    def _finish_koromo_import_ui(self):
        self.koromo_download_running = False
        self.koromo_cancel_event = None
        self.player_selector.configure(state="readonly")
        self.progress.stop()
        self.koromo_import_button.configure(state=tk.NORMAL)
        self.import_file_button.configure(state=tk.NORMAL)
        self.start_button.configure(state=tk.NORMAL)
        self.refresh_pt_button.configure(state=tk.NORMAL)
        self.stop_button.configure(text="停止", state=tk.DISABLED)

    def open_direct_urls_dialog(self):
        dialog = tk.Toplevel(self.root)
        dialog.title("直接分析雀魂牌谱")
        dialog.geometry("720x460")
        dialog.minsize(560, 360)
        dialog.transient(self.root)
        dialog.grab_set()

        body = ttk.Frame(dialog, padding=14)
        body.pack(fill=tk.BOTH, expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(2, weight=1)
        ttk.Label(
            body,
            text="每行粘贴一个雀魂分享链接。脚本生成的 JSON 请在主界面直接导入。",
        ).grid(row=0, column=0, sticky="w")
        ttk.Label(
            body,
            text="示例：https://game.maj-soul.com/1/?paipu=……",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(3, 8))

        editor = tk.Text(
            body,
            wrap=tk.NONE,
            height=10,
            font=("Cascadia Mono", 10),
        )
        editor.grid(row=2, column=0, sticky="nsew")
        if self.direct_urls:
            editor.insert("1.0", "\n".join(self.direct_urls))

        buttons = ttk.Frame(body)
        buttons.grid(row=3, column=0, sticky="ew", pady=(10, 0))

        def save_urls():
            values = []
            for line in editor.get("1.0", tk.END).splitlines():
                value = line.strip()
                if value and value not in values:
                    values.append(value)
            if not values:
                messagebox.showwarning("没有牌谱链接", "请至少粘贴一个雀魂牌谱链接。", parent=dialog)
                return
            self.direct_urls = values
            self.direct_records = [
                {"uuid": "", "paipu_url": value} for value in values
            ]
            self.direct_account_id = None
            self.direct_source_name = "手动链接"
            self.direct_skipped_non_hanchan = 0
            self.analysis_mode_var.set("全部可分析")
            self._refresh_direct_preflight()
            dialog.destroy()

        ttk.Button(buttons, text="取消", command=dialog.destroy).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="使用这些链接", command=save_urls).pack(
            side=tk.RIGHT, padx=(0, 8)
        )
        dialog.bind("<Control-Return>", lambda _event: save_urls())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        editor.focus_set()

    def refresh_pt_only(self):
        if self.process is not None or _running_analysis_pids():
            messagebox.showwarning(
                "分析仍在运行",
                "请先等待当前分析完成或点击“停止”，再补全 PT，以免同时写入结果文件。",
                parent=self.root,
            )
            return
        if not self.direct_records:
            messagebox.showwarning(
                "尚未导入牌谱",
                "请先导入 Tampermonkey 新版脚本下载的 JSON 文件。",
                parent=self.root,
            )
            return

        metadata_by_uuid = _imported_metadata_by_uuid(
            self.direct_records,
            self.direct_account_id,
        )
        with_pt = {
            direct_id: metadata
            for direct_id, metadata in metadata_by_uuid.items()
            if metadata.get("ptDelta") not in (None, "")
        }
        if not with_pt:
            messagebox.showwarning(
                "导入文件没有 PT",
                "这个文件没有包含本人 PT。请使用新版 Tampermonkey 脚本重新导出 JSON。",
                parent=self.root,
            )
            return

        result_path = (
            self.current_result_path
            if self.current_result_path is not None and self.current_result_path.is_file()
            else self._active_result_path()
        )
        if not result_path.is_file():
            messagebox.showwarning(
                "没有分析结果",
                "尚未找到可以补全的结果文件。",
                parent=self.root,
            )
            return

        output_format = result_path.suffix.lower().lstrip(".")
        try:
            rows = read_result_rows(str(result_path), output_format)
            missing_pt_uuids = {
                str(row.get("uuid") or "").strip()
                for row in rows
                if str(row.get("uuid") or "").strip()
                and row.get("ptDelta") in (None, "")
            }
            matched_pt = len(missing_pt_uuids.intersection(with_pt))
            changed_rows = backfill_result_metadata(
                str(result_path),
                output_format,
                metadata_by_uuid,
            )
            self.load_result_file(result_path)
            self.tabs.select(0)
        except Exception as exc:
            messagebox.showerror("补全 PT 失败", str(exc), parent=self.root)
            return

        self.status_var.set(f"已补全 {matched_pt} 场 PT，图表已刷新")
        self._log(
            f"[桌面版] 仅补全 PT：匹配 {matched_pt} 场，"
            f"更新 {changed_rows} 行；未调用 Mortal。"
        )
        if matched_pt:
            messagebox.showinfo(
                "PT 补全完成",
                f"已补全 {matched_pt} 场 PT，并立即重新计算累计 PT。\n\n"
                "Rating、AI 一致率和恶手率没有被修改。",
                parent=self.root,
            )
        else:
            messagebox.showinfo(
                "没有可补全的 PT",
                "当前导入 JSON 与结果文件没有匹配的空白 PT，或这些 PT 已经补全。",
                parent=self.root,
            )

    def _log(self, message: str):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, message.rstrip() + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _filtered_direct_input(self) -> tuple[list[str], list[dict]]:
        records_by_url = {
            str(record.get("paipu_url") or record.get("paipuUrl") or "").strip(): record
            for record in self.direct_records
        }
        selected_urls: list[str] = []
        selected_records: list[dict] = []
        for url in self.direct_urls:
            record = records_by_url.get(url, {"uuid": "", "paipu_url": url})
            if not _matches_analysis_mode(record, self.analysis_mode_var.get()):
                continue
            selected_urls.append(url)
            selected_records.append(record)
        return selected_urls, selected_records

    def _direct_preflight(self) -> tuple[list[str], list[str]]:
        processed = set()
        result_path = self._active_result_path()
        if result_path.is_file():
            try:
                processed = get_processed_uuids(str(result_path), "xlsx")
            except Exception as exc:
                self._log(f"[桌面版] 无法读取断点记录，将由分析核心再次校验：{exc}")
        selected_urls, _ = self._filtered_direct_input()
        return _classify_direct_urls(
            selected_urls,
            self.direct_account_id,
            processed,
        )

    def _refresh_direct_preflight(self, *, update_status: bool = True) -> tuple[list[str], list[str]]:
        completed, pending = self._direct_preflight()
        skipped_text = (
            f" · 非半庄已排除 {self.direct_skipped_non_hanchan}"
            if self.direct_skipped_non_hanchan
            else ""
        )
        source_text = f" · {self.direct_source_name}" if self.direct_source_name else ""
        selected_urls, _ = self._filtered_direct_input()
        filtered_out = len(self.direct_urls) - len(selected_urls)
        filter_text = f" · 模式外 {filtered_out}" if filtered_out else ""
        summary = (
            f"当前模式 {len(selected_urls)} · 已完成 {len(completed)} · 待分析 {len(pending)}"
            f"{filter_text}{skipped_text}{source_text}"
        )
        self.direct_count_var.set(summary)
        if update_status:
            if pending:
                self.status_var.set(
                    f"断点续跑已就绪：将跳过 {len(completed)} 局，只分析剩余 {len(pending)} 局"
                )
            else:
                if selected_urls:
                    self.status_var.set(f"当前模式已全部完成，共 {len(completed)} 局")
                else:
                    self.status_var.set("当前筛选没有可分析牌谱；旧导出请选择“全部可分析”")
        return completed, pending

    def _analysis_command(self) -> list[str]:
        if getattr(sys, "frozen", False):
            cli_path = Path(sys.executable).with_name("batchmortal-cli.exe")
            if not cli_path.exists():
                raise FileNotFoundError(f"缺少分析核心：{cli_path}")
            command = [str(cli_path)]
        else:
            command = [sys.executable, str(PROJECT_ROOT / "main.py")]

        command.extend(
            [
                "--mode",
                "mj",
                "--modes",
                "12",
                "--limit",
                str(int(self.limit_var.get())),
                "--model-tag",
                self.model_var.get(),
                "--review-language",
                "zh-CN",
                "--review-ui",
                "killerducky",
                "--output",
                "xlsx",
                "--plot",
                "none",
            ]
        )
        if self.direct_file_path is None:
            raise RuntimeError("牌谱输入文件尚未准备。")
        command.extend(["--paipu-file", str(self.direct_file_path)])
        if self.badmove_var.get():
            command.append("--badmove")
        if self.headless_var.get():
            command.append("--headless")
        return command

    def start_analysis(self):
        if self.process is not None or self.koromo_download_running:
            return
        if not self.direct_urls:
            messagebox.showwarning(
                "尚未导入牌谱",
                "请先点击“导入牌谱 JSON / TXT…”，选择网页脚本下载的文件。",
            )
            return
        stale_pids = _running_analysis_pids()
        if stale_pids:
            self.status_var.set("检测到其他分析核心仍在运行")
            messagebox.showwarning(
                "已有分析正在运行",
                "检测到另一个 batchmortal-cli 分析核心仍在后台运行。\n\n"
                f"进程 ID：{', '.join(str(pid) for pid in stale_pids)}\n\n"
                "请先在原窗口停止，或在任务管理器中结束它，再开始本次分析。",
                parent=self.root,
            )
            return
        try:
            limit = int(self.limit_var.get())
            if limit <= 0 or limit > 100:
                raise ValueError
        except ValueError:
            messagebox.showwarning("对局数无效", "对局数应为 1 到 100 之间的整数。")
            return

        try:
            completed_urls, pending_urls = self._refresh_direct_preflight(update_status=False)
            filtered_urls, filtered_records = self._filtered_direct_input()
            selected_urls = pending_urls[:limit]
            if not selected_urls:
                if not filtered_urls:
                    self.status_var.set("当前模式没有可分析牌谱")
                    messagebox.showinfo(
                        "当前模式没有牌谱",
                        "请切换“本次分析模式”。如果文件来自旧版脚本且无法区分类型，请选择“全部可分析”。",
                        parent=self.root,
                    )
                    return
                self.status_var.set(f"这批牌谱已全部完成，共 {len(completed_urls)} 局")
                messagebox.showinfo(
                    "无需重复分析",
                    f"导入的牌谱中已有 {len(completed_urls)} 局成功结果，没有待分析对局。",
                    parent=self.root,
                )
                self._reload_latest_result()
                return
            records_by_url = {
                str(record.get("paipu_url") or record.get("paipuUrl") or "").strip(): record
                for record in filtered_records
            }
            selected_records = [
                records_by_url.get(url, {"uuid": "", "paipu_url": url})
                for url in selected_urls
            ]
            APP_DATA_ROOT.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                suffix=".json",
                prefix="direct-paipu-",
                dir=APP_DATA_ROOT,
                delete=False,
            ) as handle:
                json.dump(
                    {
                        "schema": "batchmortal-majsoul-links-v1",
                        "version": APP_VERSION,
                        "account_id": self.direct_account_id,
                        "count": len(selected_records),
                        "records": selected_records,
                    },
                    handle,
                    ensure_ascii=False,
                    indent=2,
                )
            self.direct_file_path = Path(handle.name)
            self.stop_file_path = APP_DATA_ROOT / f"stop-{uuid.uuid4().hex}.flag"
            command = self._analysis_command()
            self._save_settings()
        except Exception as exc:
            self._cleanup_direct_input()
            messagebox.showerror("无法启动", str(exc))
            return

        self._log("=" * 72)
        self._log(
            f"断点续跑：已完成并跳过 {len(completed_urls)} 局，"
            f"剩余 {len(pending_urls)} 局，本次分析 {len(selected_urls)} 局。"
        )
        self._log("直接使用已导入牌谱；无需访问牌谱屋。")
        if self.headless_var.get():
            self._log("[验证] Chrome 在后台运行；若 Turnstile 要求人工操作，本次会暂停并提示重试。")
        else:
            self._log("[验证] 请保留 Chrome 窗口；出现人机验证时手动完成，程序会自动继续。")
        self.tabs.select(2)
        self.status_var.set("正在分析牌谱…")
        self.progress.start(12)
        self.start_button.configure(state=tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL)
        self.refresh_pt_button.configure(state=tk.DISABLED)
        self.player_selector.configure(state=tk.DISABLED)

        env = os.environ.copy()
        env["BATCHMORTAL_RESULTS_ROOT"] = str(RESULTS_ROOT)
        env["BATCHMORTAL_STOP_FILE"] = str(self.stop_file_path)
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

        try:
            self.process = subprocess.Popen(
                command,
                cwd=str(PROJECT_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                creationflags=creationflags,
            )
        except Exception as exc:
            self.process = None
            self._cleanup_direct_input()
            self._analysis_finished(False, str(exc))
            return

        def read_output(process: subprocess.Popen):
            assert process.stdout is not None
            for line in process.stdout:
                self.message_queue.put(("log", line.rstrip()))
            return_code = process.wait()
            self.message_queue.put(("process_done", return_code))

        threading.Thread(target=read_output, args=(self.process,), daemon=True).start()

    def stop_analysis(self):
        if getattr(self, "koromo_download_running", False):
            if not messagebox.askyesno(
                "取消导入",
                "确定取消本次牌谱屋导入吗？已经下载的文件不会删除。",
                parent=self.root,
            ):
                return
            if self.koromo_cancel_event is not None:
                self.koromo_cancel_event.set()
            self.stop_button.configure(state=tk.DISABLED)
            self.status_var.set("正在取消牌谱屋导入…")
            return
        process = self.process
        if process is None or self.stop_requested:
            return
        if not messagebox.askyesno("停止分析", "确定停止当前批量分析吗？已写入的结果会保留。"):
            return
        try:
            if self.stop_file_path is None:
                raise RuntimeError("停止信号文件尚未准备。")
            self.stop_file_path.touch(exist_ok=True)
            self.stop_requested = True
            self.stop_button.configure(state=tk.DISABLED)
            self._log("[桌面版] 已请求安全停止；正在保存结果并关闭浏览器。")
            self.status_var.set("正在安全停止（最多等待 15 秒）…")
            process_id = process.pid
            self.root.after(15_000, lambda: self._force_stop_process_tree(process_id))
        except Exception as exc:
            messagebox.showerror("停止失败", str(exc))

    def _force_stop_process_tree(self, process_id: int):
        process = self.process
        if process is None or process.pid != process_id or process.poll() is not None:
            return
        self._log("[桌面版] 安全停止超时，正在清理分析器及浏览器进程…")
        try:
            if sys.platform == "win32":
                subprocess.run(
                    ["taskkill", "/PID", str(process_id), "/T", "/F"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
            else:
                process.kill()
        except Exception as exc:
            self._log(f"[桌面版] 强制停止失败：{exc}")

    def _poll_messages(self):
        try:
            while True:
                kind, payload = self.message_queue.get_nowait()
                if kind == "log":
                    self._log(payload)
                elif kind == "koromo_progress":
                    self.status_var.set(payload)
                    self._log(f"[牌谱屋] {payload}")
                elif kind == "koromo_done":
                    imported, path = payload
                    self._finish_koromo_import_ui()
                    self._apply_paipu_import(imported, path.name, persist=True)
                    self._save_settings()
                    self._log(f"[牌谱屋] 安全牌谱文件已保存：{path}")
                    messagebox.showinfo(
                        "牌谱屋导入完成",
                        f"已导入 {len(imported.urls)} 局公开四人南场牌谱。\n\n"
                        f"安全副本已保存到：\n{path}\n\n"
                        "已分析的牌谱会自动跳过，可直接点击“开始分析”。",
                        parent=self.root,
                    )
                elif kind == "koromo_cancelled":
                    self._finish_koromo_import_ui()
                    self.status_var.set("牌谱屋导入已取消")
                    self._log(f"[牌谱屋] {payload}")
                elif kind == "koromo_error":
                    self._finish_koromo_import_ui()
                    self.status_var.set("牌谱屋导入失败")
                    self._log(f"[牌谱屋] 导入失败：{payload}")
                    messagebox.showerror(
                        "牌谱屋导入失败",
                        payload,
                        parent=self.root,
                    )
                elif kind == "process_done":
                    success = payload == 0
                    was_stopped = self.stop_requested
                    self.process = None
                    self._cleanup_direct_input()
                    self.stop_requested = False
                    if was_stopped:
                        self._analysis_stopped(payload)
                    else:
                        self._analysis_finished(success, f"分析核心退出码：{payload}")
        except queue.Empty:
            pass
        self.root.after(120, self._poll_messages)

    def _cleanup_direct_input(self):
        path = self.direct_file_path
        self.direct_file_path = None
        stop_path = self.stop_file_path
        self.stop_file_path = None
        for cleanup_path in (path, stop_path):
            if cleanup_path is None:
                continue
            try:
                cleanup_path.unlink(missing_ok=True)
            except OSError:
                pass

    def _reload_latest_result(self):
        active = self._active_result_path()
        if active.is_file():
            try:
                self.load_result_file(active)
                self.tabs.select(0)
            except Exception as exc:
                self._log(f"[桌面版] 读取结果失败：{exc}")

    def _analysis_finished(self, success: bool, detail: str):
        self.progress.stop()
        self.start_button.configure(state=tk.NORMAL)
        self.stop_button.configure(state=tk.DISABLED)
        self.refresh_pt_button.configure(state=tk.NORMAL)
        self.player_selector.configure(state="readonly")
        self._reload_latest_result()
        if self.direct_urls:
            self._refresh_direct_preflight(update_status=False)
        if success:
            self.status_var.set("分析完成")
            self._log("[桌面版] 分析完成，图表已刷新。")
        else:
            self.status_var.set("分析未完成，请查看日志")
            self._log(f"[桌面版] {detail}")
            messagebox.showwarning("分析未完成", "请查看“运行日志”标签中的错误信息。")

    def _analysis_stopped(self, exit_code: int):
        self.progress.stop()
        self.start_button.configure(state=tk.NORMAL)
        self.stop_button.configure(state=tk.DISABLED)
        self.refresh_pt_button.configure(state=tk.NORMAL)
        self.player_selector.configure(state="readonly")
        self._reload_latest_result()
        if self.direct_urls:
            self._refresh_direct_preflight(update_status=False)
        self.status_var.set("已停止；完成结果已保存")
        self._log(f"[桌面版] 分析已停止（退出码 {exit_code}）；完成结果已保存，未完成对局下次会继续。")

    def import_results(self):
        path = filedialog.askopenfilename(
            title="导入 Batch Mortal 结果",
            filetypes=[("分析结果", "*.xlsx *.csv"), ("Excel", "*.xlsx"), ("CSV", "*.csv")],
        )
        if not path:
            return
        try:
            self.load_result_file(Path(path))
            self.tabs.select(0)
            self.status_var.set(f"已导入：{Path(path).name}")
        except Exception as exc:
            messagebox.showerror("导入失败", str(exc))

    def import_results_to_player(self):
        path_text = filedialog.askopenfilename(
            parent=self.root,
            title="选择要归档的分析结果",
            filetypes=[
                ("分析结果", "*.xlsx *.csv"),
                ("Excel", "*.xlsx"),
                ("CSV", "*.csv"),
            ],
        )
        if not path_text:
            return
        source_path = Path(path_text)
        output_format = source_path.suffix.lower().lstrip(".")
        try:
            rows = read_result_rows(str(source_path), output_format)
        except Exception as exc:
            messagebox.showerror("无法读取结果", str(exc), parent=self.root)
            return
        if not rows:
            messagebox.showwarning("没有结果", "文件中没有可归档的数据。", parent=self.root)
            return

        inferred_ids = {
            str(row.get("accountId") or "").strip()
            for row in rows
            if str(row.get("accountId") or "").strip().isdigit()
        }
        default_id = (
            next(iter(inferred_ids))
            if len(inferred_ids) == 1
            else (str(self.direct_account_id) if self.direct_account_id else "")
        )
        entered_id = simpledialog.askstring(
            "归入玩家",
            f"将 {len(rows)} 条分析结果归入哪个雀魂玩家 ID？",
            initialvalue=default_id,
            parent=self.root,
        )
        if not entered_id:
            return
        try:
            account_id = int(entered_id.strip())
            if account_id <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("玩家 ID 无效", "玩家 ID 必须是正整数。", parent=self.root)
            return

        target_path = player_result_path(RESULTS_ROOT, account_id)
        try:
            if source_path.resolve() != target_path.resolve():
                existing_ids = {
                    str(row.get("uuid") or "").strip()
                    for row in read_result_rows(str(target_path), "xlsx")
                    if str(row.get("uuid") or "").strip()
                }
                new_rows = [
                    row
                    for row in rows
                    if not str(row.get("uuid") or "").strip()
                    or str(row.get("uuid") or "").strip() not in existing_ids
                ]
                if new_rows:
                    with ResultWriter(
                        str(target_path),
                        output_format="xlsx",
                        flush_every=20,
                    ) as writer:
                        for row in new_rows:
                            writer.write_row(
                                {
                                    **row,
                                    "nickname": f"玩家_{account_id}",
                                    "accountId": account_id,
                                }
                            )
            else:
                new_rows = []
            self.direct_account_id = account_id
            self._refresh_player_selector(select_account_id=account_id)
            self._load_player_selection(account_id)
            self._save_settings()
        except Exception as exc:
            messagebox.showerror("归档失败", str(exc), parent=self.root)
            return

        added = len(new_rows)
        self.status_var.set(f"玩家 ID {account_id}：已归档 {added} 条新结果")
        messagebox.showinfo(
            "归档完成",
            f"玩家 ID {account_id} 已新增 {added} 条结果。\n\n"
            "源文件没有移动或删除；以后可从左侧“当前玩家”切换查看。",
            parent=self.root,
        )

    def load_result_file(self, path: Path):
        suffix = path.suffix.lower()
        if suffix == ".csv":
            with path.open("r", encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
        elif suffix == ".xlsx":
            rows = read_result_rows(str(path), "xlsx")
        else:
            raise ValueError("只支持 CSV 或 XLSX 结果文件。")
        if not rows:
            raise ValueError("结果文件中没有可显示的数据。")

        self.rows = rows
        self.current_result_path = path
        self._refresh_table()
        if hasattr(self, "card_count_var"):
            self._refresh_stats()
        self.redraw()

    def _view_rows(self, *, valid_only: bool = False) -> list[dict]:
        source_rows = _valid_result_rows(self.rows) if valid_only else self.rows
        mode_filter = (
            self.view_mode_var.get()
            if hasattr(self, "view_mode_var")
            else "全部模式"
        )
        source_rows = [
            row
            for row in source_rows
            if _matches_mode_filter(row, mode_filter)
        ]
        ordered = sorted(
            source_rows,
            key=lambda row: _parse_datetime(
                _first_value(row, "startTime", "start_time", "date")
            )
            or datetime.min,
        )
        label = self.view_limit_var.get()
        if label == "全部":
            return ordered
        amount = int("".join(character for character in label if character.isdigit()) or 10)
        return ordered[-amount:]

    def _refresh_stats(self):
        valid_rows = self._view_rows(valid_only=True)
        ratings = [
            value
            for value in (
                _to_float(_first_value(row, "rating", "Rating")) for row in valid_rows
            )
            if value is not None
        ]
        ai_rate = _aggregate_percentage(
            valid_rows,
            "aiConsistencyRate",
            "aiConsistencyNumerator",
            "aiConsistencyDenominator",
        )
        pt_values = [
            value
            for value in (
                _to_float(_first_value(row, "ptDelta", "gradingScore", "pt", "PT"))
                for row in valid_rows
            )
            if value is not None
        ]
        sessions = _long_sessions(valid_rows)

        self.card_count_var.set(str(len(valid_rows)))
        self.card_rating_var.set(f"{mean(ratings):.2f}" if ratings else "—")
        self.card_ai_var.set(f"{ai_rate:.1f}%" if ai_rate is not None else "—")
        self.card_pt_var.set(f"{sum(pt_values):+.0f}" if pt_values else "—")
        self.card_session_var.set(str(len(sessions)))
        if sessions:
            start, end, games = sessions[-1]
            self.status_var.set(
                f"检测到 {len(sessions)} 段≥4小时连续对局；最近一段 {start:%m-%d %H:%M}–{end:%m-%d %H:%M}（{games}场）"
            )

    def _refresh_table(self):
        for item in self.table.get_children():
            self.table.delete(item)
        ordered = list(reversed(self._view_rows()))
        for display_index, row in enumerate(ordered, start=1):
            values = (
                display_index,
                _first_value(row, "startTime", "start_time", "date"),
                _record_mode_label(row),
                _first_value(row, "placement", "rank"),
                _first_value(row, "finalScore", "score"),
                _first_value(row, "rating", "Rating"),
                _first_value(row, "aiConsistencyRate", "ai_consistency", "一致率"),
                _first_value(row, "ptDelta", "gradingScore", "pt", "PT"),
            )
            self.table.insert("", tk.END, iid=str(display_index - 1), values=values)

    def redraw(self):
        if not self.rows:
            self._draw_empty_chart()
            return
        if hasattr(self, "card_count_var"):
            self._refresh_stats()
        self._refresh_table()
        rows = self._view_rows(valid_only=True)
        if not rows:
            self.figure.clear()
            ax = self.figure.add_subplot(111)
            ax.axis("off")
            ax.text(
                0.5,
                0.53,
                "当前范围没有成功的 Mortal 分析结果",
                ha="center",
                va="center",
                fontsize=16,
                color="#475467",
            )
            ax.text(
                0.5,
                0.45,
                "ERROR 记录仍保留在“牌谱明细”中",
                ha="center",
                va="center",
                color="#98a2b3",
            )
            self.canvas.draw_idle()
            return

        x_values = list(range(1, len(rows) + 1))
        ratings = [_to_float(_first_value(row, "rating", "Rating")) for row in rows]
        ai_rates = []
        for row in rows:
            numerator = _to_float(row.get("aiConsistencyNumerator"))
            denominator = _to_float(row.get("aiConsistencyDenominator"))
            if numerator is not None and denominator is not None and denominator > 0:
                ai_rates.append(numerator / denominator * 100)
            else:
                ai_rates.append(
                    _to_float(
                        _first_value(
                            row,
                            "aiConsistencyRate",
                            "ai_consistency",
                            "一致率",
                        )
                    )
                )
        bad_rates_5 = [
            _row_percentage(row, "badMoveRate5", "badMoveCount5") for row in rows
        ]
        bad_rates_10 = [
            _row_percentage(row, "badMoveRate10", "badMoveCount10") for row in rows
        ]
        pt_values = [
            _to_float(_first_value(row, "ptDelta", "gradingScore", "pt", "PT"))
            for row in rows
        ]

        self.figure.clear()
        grid = self.figure.add_gridspec(3, 1, height_ratios=[2.25, 0.95, 1.25])
        ax_rating = self.figure.add_subplot(grid[0, 0])
        ax_ai = ax_rating.twinx()
        ax_bad = self.figure.add_subplot(grid[1, 0], sharex=ax_rating)
        ax_pt = self.figure.add_subplot(grid[2, 0], sharex=ax_rating)
        ax_cumulative = ax_pt.twinx()

        rating_x = [x for x, value in zip(x_values, ratings) if value is not None]
        rating_y = [value for value in ratings if value is not None]
        if rating_y:
            rating_mean = mean(rating_y)
            dense_lower = _quantile(rating_y, 0.25)
            dense_upper = _quantile(rating_y, 0.75)
            ax_rating.axhspan(
                dense_lower,
                dense_upper,
                color="#fef0c7",
                alpha=0.45,
                label=f"Rating 中间50% {dense_lower:.1f}–{dense_upper:.1f}",
            )
            ax_rating.plot(
                rating_x,
                rating_y,
                color="#d92d20",
                marker="o",
                markersize=4,
                linewidth=1.4,
                label="Rating",
            )
            ax_rating.axhline(
                rating_mean,
                color="#7f1d1d",
                linestyle=":",
                linewidth=1.0,
                label=f"平均 Rating {rating_mean:.2f}",
            )
            if len(rating_y) >= 10 and all(value is not None for value in ratings):
                moving = _moving_average(ratings, 10)
                moving_x = [x for x, value in zip(x_values, moving) if value is not None]
                moving_y = [value for value in moving if value is not None]
                ax_rating.plot(
                    moving_x,
                    moving_y,
                    color="#f79009",
                    linewidth=2.2,
                    label="Rating 10场均线",
                )
            lower = max(50, math.floor((min(rating_y) - 3) / 5) * 5)
            upper = min(100, math.ceil((max(rating_y) + 3) / 5) * 5)
            if upper <= lower:
                upper = lower + 10
            ax_rating.set_ylim(lower, upper)
        ax_rating.set_ylabel("Rating", color="#b42318")

        ai_x = [x for x, value in zip(x_values, ai_rates) if value is not None]
        ai_y = [value for value in ai_rates if value is not None]
        if ai_y:
            ax_ai.plot(
                ai_x,
                ai_y,
                color="#175cd3",
                marker="x",
                markersize=5,
                linewidth=1.2,
                label="AI 一致率",
            )
            ai_mean = _aggregate_percentage(
                rows,
                "aiConsistencyRate",
                "aiConsistencyNumerator",
                "aiConsistencyDenominator",
            )
            if ai_mean is not None:
                ax_ai.axhline(
                    ai_mean,
                    color="#175cd3",
                    linestyle=":",
                    linewidth=1.0,
                    alpha=0.8,
                    label=f"加权一致率 {ai_mean:.1f}%",
                )
            ai_lower = max(0, math.floor((min(ai_y) - 5) / 10) * 10)
            ai_upper = min(100, max(80, math.ceil((max(ai_y) + 5) / 10) * 10))
            if ai_upper <= ai_lower:
                ai_upper = min(100, ai_lower + 20)
            ax_ai.set_ylim(ai_lower, ai_upper)
        ax_ai.set_ylabel("AI 一致率 (%)", color="#175cd3")

        title_name = str(_first_value(rows[-1], "nickname") or "玩家")
        dates = [
            _parse_datetime(_first_value(row, "startTime", "start_time", "date"))
            for row in rows
        ]
        valid_dates = [value for value in dates if value is not None]
        model_tags = list(
            dict.fromkeys(
                str(_first_value(row, "modelTag") or "").strip()
                for row in rows
                if str(_first_value(row, "modelTag") or "").strip()
            )
        )
        subtitle_parts = [f"{len(rows)} 场有效半庄"]
        if valid_dates:
            subtitle_parts.append(f"{min(valid_dates):%Y-%m-%d} 至 {max(valid_dates):%Y-%m-%d}")
        if model_tags:
            subtitle_parts.append("Mortal " + ", ".join(model_tags))
        displayed_rows = self._view_rows()
        removed_count = len(displayed_rows) - len(rows)
        if removed_count:
            subtitle_parts.append(f"已排除 {removed_count} 条 ERROR")
        view_mode_label = (
            self.view_mode_var.get()
            if hasattr(self, "view_mode_var")
            else "全部模式"
        )
        if view_mode_label != "全部模式":
            subtitle_parts.append(view_mode_label)
        ax_rating.set_title(
            f"{title_name} · Mortal 牌谱分析\n" + " · ".join(subtitle_parts),
            fontsize=14,
            weight="bold",
            pad=40 if valid_dates else 10,
        )
        ax_rating.grid(True, alpha=0.22)

        if valid_dates:
            first_position_by_date = {}
            for position, parsed in zip(x_values, dates):
                if parsed is not None:
                    first_position_by_date.setdefault(parsed.date(), position)
            date_ticks = list(first_position_by_date.items())
            if len(date_ticks) > 10:
                selected_indexes = sorted(
                    {
                        round(index * (len(date_ticks) - 1) / 9)
                        for index in range(10)
                    }
                )
                date_ticks = [date_ticks[index] for index in selected_indexes]
            date_axis = ax_rating.secondary_xaxis("top")
            date_axis.set_xticks([position for _, position in date_ticks])
            date_axis.set_xticklabels(
                [value.strftime("%m-%d") for value, _ in date_ticks],
                rotation=35,
                ha="left",
                fontsize=7,
            )
            date_axis.set_xlabel("对局日期", fontsize=8)

        rating_handles, rating_labels = ax_rating.get_legend_handles_labels()
        ai_handles, ai_labels = ax_ai.get_legend_handles_labels()
        if rating_handles or ai_handles:
            ax_rating.legend(
                rating_handles + ai_handles,
                rating_labels + ai_labels,
                loc="upper left",
                ncol=3,
                frameon=False,
            )

        label_step = max(1, math.ceil(len(rows) / 40))
        for offset, (x_value, rating) in enumerate(zip(x_values, ratings)):
            if rating is None or offset % label_step:
                continue
            ax_rating.annotate(
                f"{rating:.1f}",
                (x_value, rating),
                xytext=(0, 6 if offset % 2 == 0 else -11),
                textcoords="offset points",
                ha="center",
                fontsize=6.5,
                color="#b42318",
            )

        bad_10_x = [x for x, value in zip(x_values, bad_rates_10) if value is not None]
        bad_10_y = [value for value in bad_rates_10 if value is not None]
        bad_5_x = [x for x, value in zip(x_values, bad_rates_5) if value is not None]
        bad_5_y = [value for value in bad_rates_5 if value is not None]
        if bad_10_y or bad_5_y:
            if bad_10_y:
                bad_10_mean = _aggregate_percentage(
                    rows, "badMoveRate10", "badMoveCount10", "badMoveDenominator"
                )
                ax_bad.plot(
                    bad_10_x,
                    bad_10_y,
                    color="#6941c6",
                    marker="o",
                    markersize=3,
                    linewidth=1.15,
                    label=(
                        f"10% 恶手率（总体 {bad_10_mean:.1f}%）"
                        if bad_10_mean is not None
                        else "10% 恶手率"
                    ),
                )
                if bad_10_mean is not None:
                    ax_bad.axhline(
                        bad_10_mean,
                        color="#6941c6",
                        linestyle=":",
                        linewidth=0.9,
                        alpha=0.75,
                    )
            if bad_5_y:
                bad_5_mean = _aggregate_percentage(
                    rows, "badMoveRate5", "badMoveCount5", "badMoveDenominator"
                )
                ax_bad.plot(
                    bad_5_x,
                    bad_5_y,
                    color="#f79009",
                    marker="s",
                    markersize=3,
                    linewidth=1.1,
                    label=(
                        f"5% 严重恶手率（总体 {bad_5_mean:.1f}%）"
                        if bad_5_mean is not None
                        else "5% 严重恶手率"
                    ),
                )
                if bad_5_mean is not None:
                    ax_bad.axhline(
                        bad_5_mean,
                        color="#f79009",
                        linestyle=":",
                        linewidth=0.9,
                        alpha=0.75,
                    )
            maximum_bad_rate = max(bad_10_y + bad_5_y)
            bad_upper = max(5, math.ceil(maximum_bad_rate * 1.18 / 5) * 5)
            ax_bad.set_ylim(0, bad_upper)
            ax_bad.legend(loc="upper left", ncol=2, frameon=False, fontsize=8)
        else:
            ax_bad.text(
                0.5,
                0.5,
                "未采集恶手率；分析时勾选“统计 5% / 10% 恶手率”",
                transform=ax_bad.transAxes,
                ha="center",
                va="center",
                color="#667085",
                fontsize=8,
            )
        ax_bad.set_ylabel("恶手率 (%)")
        ax_bad.grid(True, axis="y", alpha=0.2)

        valid_pt = [value for value in pt_values if value is not None]
        if valid_pt:
            bar_values = [value if value is not None else 0 for value in pt_values]
            colors = [
                "#98a2b3"
                if source_value is None
                else ("#3b5bdb" if value >= 0 else "#d95d5d")
                for source_value, value in zip(pt_values, bar_values)
            ]
            ax_pt.bar(
                x_values,
                bar_values,
                color=colors,
                alpha=0.86,
                width=0.72,
                label="单场 PT",
            )
            pt_min = min(valid_pt)
            pt_max = max(valid_pt)
            pt_span = max(1, pt_max - pt_min)
            ax_pt.set_ylim(
                min(0, pt_min) - max(12, pt_span * 0.12),
                max(0, pt_max) + max(12, pt_span * 0.12),
            )
            ax_pt.axhline(0, color="#98a2b3", linewidth=0.8)
            cumulative = []
            running = 0.0
            for source_value, value in zip(pt_values, bar_values):
                if source_value is not None:
                    running += value
                cumulative.append(running)
            ax_cumulative.plot(
                x_values,
                cumulative,
                color="#15803d",
                marker="o",
                markersize=3,
                linewidth=1.7,
                label="累计 PT",
            )
            ax_cumulative.annotate(
                f"最终累计 PT {cumulative[-1]:+.0f}",
                (x_values[-1], cumulative[-1]),
                xytext=(-6, 8),
                textcoords="offset points",
                ha="right",
                color="#146c43",
                fontsize=8,
                weight="bold",
            )
            ax_pt.set_ylabel("单场 PT")
            ax_cumulative.set_ylabel("累计 PT", color="#146c43")
            pt_handles, pt_labels = ax_pt.get_legend_handles_labels()
            cumulative_handles, cumulative_labels = ax_cumulative.get_legend_handles_labels()
            ax_pt.legend(
                pt_handles + cumulative_handles,
                pt_labels + cumulative_labels,
                loc="upper right",
                ncol=2,
                frameon=False,
            )
            for offset, (x_value, value) in enumerate(zip(x_values, pt_values)):
                if value is None or offset % label_step:
                    continue
                ax_pt.annotate(
                    f"{value:+.0f}",
                    (x_value, value),
                    xytext=(0, 4 if value >= 0 else -10),
                    textcoords="offset points",
                    ha="center",
                    fontsize=6.3,
                    color="#344054",
                )
            missing_pt = sum(value is None for value in pt_values)
            if missing_pt:
                ax_pt.text(
                    0.995,
                    0.04,
                    f"{missing_pt} 场缺少 PT，累计线按已知数据计算",
                    transform=ax_pt.transAxes,
                    ha="right",
                    va="bottom",
                    fontsize=7,
                    color="#667085",
                )
        else:
            ax_pt.text(
                0.5,
                0.5,
                "旧结果未包含 PT；重新查询后会自动记录",
                transform=ax_pt.transAxes,
                ha="center",
                va="center",
                color="#667085",
            )
            ax_cumulative.set_yticks([])
        ax_pt.set_xlabel("半庄序号（由远到近）")
        ax_pt.grid(True, axis="y", alpha=0.2)
        tick_step = max(1, math.ceil(len(rows) / 12))
        ticks = list(range(1, len(rows) + 1, tick_step))
        if ticks[-1] != len(rows):
            ticks.append(len(rows))
        ax_pt.set_xticks(ticks)
        ax_rating.tick_params(axis="x", labelbottom=False)
        ax_bad.tick_params(axis="x", labelbottom=False)
        for axis in (ax_rating, ax_bad, ax_pt):
            axis.set_xlim(0.35, len(rows) + 0.65)
        self.canvas.draw_idle()

    def export_chart(self):
        if not self.rows:
            messagebox.showinfo("没有图表", "请先运行分析或导入结果文件。")
            return
        default_name = "mortal_report.png"
        if self.current_result_path:
            default_name = f"{self.current_result_path.parent.name}_mortal_report.png"
        path = filedialog.asksaveasfilename(
            title="导出当前图表",
            defaultextension=".png",
            initialfile=default_name,
            filetypes=[("PNG 图片", "*.png")],
        )
        if not path:
            return
        self.figure.savefig(path, dpi=170, bbox_inches="tight", facecolor="white")
        self.status_var.set(f"图表已导出：{Path(path).name}")

    def _open_selected_result(self, _event=None):
        selection = self.table.selection()
        if not selection:
            return
        display_index = int(selection[0])
        ordered = list(reversed(self._view_rows()))
        if display_index >= len(ordered):
            return
        url = _safe_result_url(ordered[display_index])
        if url:
            webbrowser.open(url)

    @staticmethod
    def open_results_folder():
        RESULTS_ROOT.mkdir(parents=True, exist_ok=True)
        if sys.platform == "win32":
            os.startfile(RESULTS_ROOT)  # type: ignore[attr-defined]
        else:
            webbrowser.open(RESULTS_ROOT.as_uri())

    @staticmethod
    def show_about():
        messagebox.showinfo(
            "关于",
            f"{APP_NAME} {APP_VERSION}\n\n"
            "用于四人半庄的赛后牌谱复盘与趋势统计。\n"
            "分析核心基于 Batch Mortal 与 mjai-reviewer。\n\n"
            "Rating 与一致率只用于筛选复盘局，不构成作弊结论。",
        )

    def _on_close(self):
        if self.koromo_download_running:
            if not messagebox.askyesno(
                "退出",
                "牌谱屋仍在下载。退出会取消本次导入，是否继续？",
                parent=self.root,
            ):
                return
            if self.koromo_cancel_event is not None:
                self.koromo_cancel_event.set()
        if self.process is not None:
            if not messagebox.askyesno("退出", "分析仍在运行。退出会停止当前任务，是否继续？"):
                return
            try:
                process_id = self.process.pid
                if sys.platform == "win32":
                    subprocess.run(
                        ["taskkill", "/PID", str(process_id), "/T", "/F"],
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                        creationflags=subprocess.CREATE_NO_WINDOW,
                    )
                else:
                    self.process.kill()
            except Exception:
                pass
        self._cleanup_direct_input()
        try:
            self._save_settings()
        except Exception:
            pass
        self.root.destroy()


def main():
    _set_windows_dpi_awareness()
    root = tk.Tk()
    app = MortalDesktopApp(root)
    if "--smoke-test" in sys.argv:
        root.update_idletasks()
        root.update()
        root.destroy()
        print("desktop smoke test: OK")
        return
    root.mainloop()


if __name__ == "__main__":
    main()
