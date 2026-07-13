import csv
import html
import json
import logging
import math
import os
import urllib.parse
from datetime import datetime
from statistics import median
from string import Template

import openpyxl


def _safe_nickname(nickname: str) -> str:
    return "".join(
        c if c.isalnum() or c in ("_", "-", "\u4e00", "\u9fa5") else "_"
        for c in nickname
    )


def _parse_time(value) -> float:
    text = str(value or "").strip()
    if not text:
        return 0.0
    try:
        if text.endswith("Z"):
            return datetime.fromisoformat(text[:-1]).timestamp()
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").timestamp()
    except Exception:
        try:
            return datetime.fromisoformat(text).timestamp()
        except Exception:
            return 0.0


def read_results(
    nickname: str,
    output_format: str = "xlsx",
    output_root: str | None = None,
) -> list[dict]:
    safe_nick = _safe_nickname(nickname)
    if output_root is None:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        output_root = os.path.join(base_dir, "results", "majsoul", safe_nick)
    filepath = os.path.join(output_root, f"results.{output_format}")

    if not os.path.exists(filepath):
        logging.warning("No results found for %s at %s", nickname, filepath)
        return []

    records = []
    if output_format == "csv":
        with open(filepath, "r", encoding="utf-8") as f:
            records.extend(csv.DictReader(f))
    elif output_format == "xlsx":
        wb = openpyxl.load_workbook(filepath, read_only=True, data_only=True)
        try:
            ws = wb.active
            rows = iter(ws.iter_rows(values_only=True))
            first_row = next(rows, None)
            if first_row is not None:
                headers = [str(value) if value is not None else "" for value in first_row]
                for row in rows:
                    records.append(
                        {
                            header: row[index] if index < len(row) and row[index] is not None else ""
                            for index, header in enumerate(headers)
                            if header
                        }
                    )
        finally:
            wb.close()
    else:
        raise ValueError(f"Unsupported output format: {output_format}")

    records.sort(key=lambda row: _parse_time(row.get("startTime") or row.get("timestamp")))
    return records


def _to_float(value, *, percent: bool = False) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip()
    if not text:
        return None
    if percent:
        text = text.removesuffix("%").strip()
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _to_int(value) -> int | None:
    number = _to_float(value)
    if number is None or not number.is_integer():
        return None
    return int(number)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def rolling_average(values: list[float | None], window: int) -> list[float | None]:
    """Return a full-window rolling mean, tolerating sparse optional values."""
    if window <= 0:
        raise ValueError("Rolling window must be positive.")

    minimum_count = max(1, math.ceil(window * 0.6))
    result = []
    for index in range(len(values)):
        if index + 1 < window:
            result.append(None)
            continue
        current = [value for value in values[index - window + 1 : index + 1] if value is not None]
        result.append(_mean(current) if len(current) >= minimum_count else None)
    return result


def _rolling_weighted_ai(points: list[dict], window: int) -> list[float | None]:
    minimum_count = max(1, math.ceil(window * 0.6))
    result = []
    for index in range(len(points)):
        if index + 1 < window:
            result.append(None)
            continue
        current = points[index - window + 1 : index + 1]
        weighted = [
            (point["aiNumerator"], point["aiDenominator"])
            for point in current
            if point["aiNumerator"] is not None
            and point["aiDenominator"] is not None
            and point["aiDenominator"] > 0
        ]
        if len(weighted) >= minimum_count:
            numerator = sum(pair[0] for pair in weighted)
            denominator = sum(pair[1] for pair in weighted)
            result.append(numerator / denominator * 100 if denominator else None)
            continue
        rates = [point["aiRate"] for point in current if point["aiRate"] is not None]
        result.append(_mean(rates) if len(rates) >= minimum_count else None)
    return result


def _aggregate_rate(
    points: list[dict],
    *,
    rate_key: str,
    numerator_key: str,
    denominator_key: str,
) -> tuple[float | None, int | None, bool]:
    weighted = [
        (point[numerator_key], point[denominator_key])
        for point in points
        if point[numerator_key] is not None
        and point[denominator_key] is not None
        and point[denominator_key] > 0
    ]
    if weighted:
        numerator = sum(pair[0] for pair in weighted)
        denominator = sum(pair[1] for pair in weighted)
        return (numerator / denominator * 100 if denominator else None, denominator, True)

    rates = [point[rate_key] for point in points if point[rate_key] is not None]
    return _mean(rates), None, False


def _infer_source(record: dict, mode: str) -> str:
    source = str(record.get("source") or "").strip().lower()
    if source in ("majsoul", "tenhou"):
        return source
    if "p-" in mode.lower():
        return "tenhou"
    if mode.isdigit():
        return "majsoul"
    return ""


def _rating_axis_bounds(ratings: list[float]) -> tuple[int, int]:
    minimum = min(ratings)
    maximum = max(ratings)
    if minimum >= 80:
        lower = 80
    elif minimum >= 60:
        lower = 60
    elif minimum >= 40:
        lower = 40
    else:
        lower = 0
    upper = 100 if maximum <= 100 else int(math.ceil(maximum / 10) * 10)
    return lower, upper


def _rate_axis_min(values: list[float]) -> int:
    minimum = min(values)
    if minimum >= 60:
        return 60
    if minimum >= 40:
        return 40
    return 0


def _histogram(values: list[float], lower: int, upper: int, bins: int = 10) -> list[dict]:
    width = (upper - lower) / bins
    counts = [0] * bins
    for value in values:
        index = min(bins - 1, max(0, int((value - lower) / width)))
        counts[index] += 1
    return [
        {
            "label": f"{lower + index * width:.0f}–{lower + (index + 1) * width:.0f}",
            "count": count,
        }
        for index, count in enumerate(counts)
    ]


def prepare_dashboard_data(records: list[dict], plot_limit: int | None = None) -> dict | None:
    """Normalize result rows into a single, tested dashboard data contract."""
    selected = records
    if plot_limit is not None and plot_limit > 0 and len(selected) > plot_limit:
        selected = selected[-plot_limit:]

    points = []
    for record in selected:
        rating = _to_float(record.get("rating"))
        if rating is None:
            continue

        ai_numerator = _to_int(record.get("aiConsistencyNumerator"))
        ai_denominator = _to_int(record.get("aiConsistencyDenominator"))
        ai_rate = _to_float(record.get("aiConsistencyRate"), percent=True)
        if ai_numerator is not None and ai_denominator is not None and ai_denominator > 0:
            ai_rate = ai_numerator / ai_denominator * 100

        bad_denominator = _to_int(record.get("badMoveDenominator"))
        bad_count_5 = _to_int(record.get("badMoveCount5"))
        bad_count_10 = _to_int(record.get("badMoveCount10"))
        bad_rate_5 = _to_float(record.get("badMoveRate5"), percent=True)
        bad_rate_10 = _to_float(record.get("badMoveRate10"), percent=True)
        if bad_denominator is not None and bad_denominator > 0:
            if bad_count_5 is not None:
                bad_rate_5 = bad_count_5 / bad_denominator * 100
            if bad_count_10 is not None:
                bad_rate_10 = bad_count_10 / bad_denominator * 100

        mode = str(record.get("mode") or "—")
        started_at = str(record.get("startTime") or record.get("timestamp") or "")
        points.append(
            {
                "index": len(points) + 1,
                "label": f"#{len(points) + 1}",
                "startedAt": started_at,
                "rating": rating,
                "aiRate": ai_rate,
                "aiNumerator": ai_numerator,
                "aiDenominator": ai_denominator,
                "badRate5": bad_rate_5,
                "badCount5": bad_count_5,
                "badRate10": bad_rate_10,
                "badCount10": bad_count_10,
                "badDenominator": bad_denominator,
                "source": _infer_source(record, mode),
                "mode": mode,
                "modelTag": str(record.get("modelTag") or ""),
                "uuid": str(record.get("uuid") or ""),
                "resultUrl": str(record.get("resultUrl") or ""),
                "paipuUrl": str(record.get("paipuUrl") or ""),
            }
        )

    if not points:
        return None

    ratings = [point["rating"] for point in points]
    total_games = len(points)
    trend_window = 10 if total_games >= 10 else (5 if total_games >= 8 else None)
    rating_rolling = (
        rolling_average([point["rating"] for point in points], trend_window)
        if trend_window
        else [None] * total_games
    )
    ai_rolling = _rolling_weighted_ai(points, trend_window) if trend_window else [None] * total_games

    comparison_window = min(20, total_games // 2) if total_games >= 10 else 0
    recent_window = comparison_window or min(20, total_games)
    recent_average = _mean(ratings[-recent_window:])
    previous_average = (
        _mean(ratings[-comparison_window * 2 : -comparison_window])
        if comparison_window
        else None
    )
    comparison_delta = (
        recent_average - previous_average
        if recent_average is not None and previous_average is not None
        else None
    )

    ai_rate, ai_denominator, ai_weighted = _aggregate_rate(
        points,
        rate_key="aiRate",
        numerator_key="aiNumerator",
        denominator_key="aiDenominator",
    )
    bad_rate_5, bad_denominator_5, bad_weighted_5 = _aggregate_rate(
        points,
        rate_key="badRate5",
        numerator_key="badCount5",
        denominator_key="badDenominator",
    )
    bad_rate_10, bad_denominator_10, bad_weighted_10 = _aggregate_rate(
        points,
        rate_key="badRate10",
        numerator_key="badCount10",
        denominator_key="badDenominator",
    )

    rating_axis_min, rating_axis_max = _rating_axis_bounds(ratings)
    ai_values = [point["aiRate"] for point in points if point["aiRate"] is not None]
    ai_axis_min = _rate_axis_min(ai_values) if ai_values else 0
    histogram = (
        _histogram(ratings, rating_axis_min, rating_axis_max)
        if total_games >= 8
        else []
    )
    worst_games = sorted(points, key=lambda point: (point["rating"], point["index"]))[:5]
    highlight_count = min(5, max(1, math.ceil(total_games * 0.05)))
    highlighted = {
        point["index"]
        for point in sorted(points, key=lambda point: (point["rating"], point["index"]))[
            :highlight_count
        ]
    }
    for point in points:
        point["isLow"] = point["index"] in highlighted

    dates = [point["startedAt"] for point in points if point["startedAt"]]
    sources = sorted({point["source"] for point in points if point["source"]})
    modes = sorted({point["mode"] for point in points if point["mode"] and point["mode"] != "—"})
    model_tags = sorted({point["modelTag"] for point in points if point["modelTag"]})

    return {
        "points": points,
        "totalGames": total_games,
        "trendWindow": trend_window,
        "ratingRolling": rating_rolling,
        "aiRolling": ai_rolling,
        "ratingMedian": median(ratings),
        "recentWindow": recent_window,
        "recentAverage": recent_average,
        "comparisonWindow": comparison_window,
        "comparisonDelta": comparison_delta,
        "aiRate": ai_rate,
        "aiDenominator": ai_denominator,
        "aiWeighted": ai_weighted,
        "badRate5": bad_rate_5,
        "badRate10": bad_rate_10,
        "badDenominator": bad_denominator_5 or bad_denominator_10,
        "badWeighted": bad_weighted_5 or bad_weighted_10,
        "ratingAxisMin": rating_axis_min,
        "ratingAxisMax": rating_axis_max,
        "aiAxisMin": ai_axis_min,
        "histogram": histogram,
        "worstGames": worst_games,
        "dateStart": dates[0] if dates else "",
        "dateEnd": dates[-1] if dates else "",
        "sources": sources,
        "modes": modes,
        "modelTags": model_tags,
    }


def _format_number(value: float | None, digits: int = 1, suffix: str = "") -> str:
    return "—" if value is None else f"{value:.{digits}f}{suffix}"


def _display_source(sources: list[str]) -> str:
    labels = {"majsoul": "雀魂", "tenhou": "天凤"}
    return " / ".join(labels.get(source, source) for source in sources) or "数据源未标注"


def _date_range(started_at: str, ended_at: str) -> str:
    start = started_at[:10] if started_at else ""
    end = ended_at[:10] if ended_at else ""
    if not start:
        return "日期未标注"
    return start if start == end or not end else f"{start} — {end}"


def _safe_external_url(value: str) -> str | None:
    try:
        parsed = urllib.parse.urlsplit(value)
    except (TypeError, ValueError):
        return None
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return value


def _worst_game_rows(data: dict) -> str:
    rows = []
    for point in data["worstGames"]:
        link = _safe_external_url(point["resultUrl"]) or _safe_external_url(point["paipuUrl"])
        link_html = (
            f'<a href="{html.escape(link, quote=True)}" target="_blank" rel="noopener">打开检讨</a>'
            if link
            else '<span class="muted">无链接</span>'
        )
        rows.append(
            "<tr>"
            f'<td class="mono">#{point["index"]}</td>'
            f'<td>{html.escape(point["startedAt"] or "—")}</td>'
            f'<td><span class="mode-tag">{html.escape(point["mode"])}</span></td>'
            f'<td class="metric strong">{point["rating"]:.2f}</td>'
            f'<td class="metric">{_format_number(point["aiRate"], 1, "%")}</td>'
            f'<td class="metric">{_format_number(point["badRate5"], 1, "%")}</td>'
            f"<td>{link_html}</td>"
            "</tr>"
        )
    return "".join(rows)


def _safe_json(value) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")


# Chart map: KPI scorecard for status; scatter + rolling line for ordered trends;
# histogram for distribution; exact linked table for actionable low-rating review.
REPORT_TEMPLATE = Template(r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>$page_title</title>
    <script src="https://cdn.jsdelivr.net/npm/echarts@5.5.0/dist/echarts.min.js"></script>
    <style>
        :root {
            --canvas: #f3f5f8;
            --surface: #ffffff;
            --ink: #172033;
            --muted: #687386;
            --subtle: #97a1b2;
            --line: #e4e8ef;
            --indigo: #3f51c6;
            --indigo-dark: #28388f;
            --indigo-soft: #e4e8fb;
            --amber: #b57d24;
            --amber-soft: #f7ecd8;
            --font: Inter, "SF Pro Display", "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
            --mono: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
        }
        * { box-sizing: border-box; }
        body {
            margin: 0;
            background: var(--canvas);
            color: var(--ink);
            font-family: var(--font);
            -webkit-font-smoothing: antialiased;
        }
        #main {
            width: min(1380px, calc(100% - 40px));
            margin: 28px auto;
            padding: 42px 44px 34px;
            background: var(--surface);
            border: 1px solid var(--line);
        }
        .report-header {
            display: flex;
            justify-content: space-between;
            align-items: flex-end;
            gap: 32px;
            padding-bottom: 28px;
            border-bottom: 1px solid var(--line);
        }
        .eyebrow {
            margin: 0 0 10px;
            color: var(--indigo-dark);
            font-size: 12px;
            font-weight: 700;
            letter-spacing: .16em;
            text-transform: uppercase;
        }
        h1 { margin: 0; font-size: clamp(30px, 4vw, 46px); line-height: 1.08; letter-spacing: -.04em; }
        .subtitle { margin: 12px 0 0; color: var(--muted); font-size: 14px; line-height: 1.7; }
        .report-mark { color: var(--subtle); font-family: var(--mono); font-size: 12px; white-space: nowrap; }
        .kpi-grid {
            display: grid;
            grid-template-columns: repeat(5, minmax(0, 1fr));
            border-bottom: 1px solid var(--line);
        }
        .kpi { min-height: 136px; padding: 28px 22px 24px; border-right: 1px solid var(--line); }
        .kpi:first-child { padding-left: 0; }
        .kpi:last-child { border-right: 0; padding-right: 0; }
        .kpi-label { color: var(--muted); font-size: 12px; font-weight: 650; letter-spacing: .04em; }
        .kpi-value { margin-top: 10px; font-family: var(--mono); font-size: 29px; font-weight: 650; letter-spacing: -.04em; }
        .kpi-note { margin-top: 8px; color: var(--subtle); font-size: 12px; line-height: 1.45; }
        .delta-up { color: var(--indigo-dark); }
        .delta-down { color: var(--amber); }
        .chart-section { padding: 34px 0 28px; border-bottom: 1px solid var(--line); }
        .section-head { display: flex; justify-content: space-between; align-items: flex-start; gap: 24px; margin-bottom: 18px; }
        h2 { margin: 0; font-size: 19px; letter-spacing: -.015em; }
        .chart { width: 100%; height: 390px; }
        .secondary-grid { display: grid; grid-template-columns: minmax(0, 2fr) minmax(320px, 1fr); gap: 38px; }
        .secondary-grid .chart-section { min-width: 0; }
        .secondary-grid .chart { height: 310px; }
        .review-section { padding-top: 34px; }
        .table-wrap { overflow-x: auto; border-top: 1px solid var(--line); }
        table { width: 100%; border-collapse: collapse; font-size: 13px; }
        th { padding: 13px 14px; color: var(--muted); font-size: 11px; font-weight: 700; letter-spacing: .05em; text-align: left; white-space: nowrap; }
        td { padding: 14px; border-top: 1px solid var(--line); white-space: nowrap; }
        th:first-child, td:first-child { padding-left: 0; }
        th:last-child, td:last-child { padding-right: 0; text-align: right; }
        .metric, .mono { font-family: var(--mono); font-variant-numeric: tabular-nums; }
        .strong { color: var(--amber); font-weight: 700; }
        .muted { color: var(--subtle); }
        .mode-tag { display: inline-flex; padding: 4px 8px; color: var(--indigo-dark); background: var(--indigo-soft); font-family: var(--mono); font-size: 11px; }
        a { color: var(--indigo-dark); font-weight: 650; text-decoration: none; }
        a:hover { text-decoration: underline; }
        footer { display: flex; justify-content: space-between; gap: 24px; margin-top: 34px; color: var(--subtle); font-size: 11px; line-height: 1.6; }
        .chart-error { display: grid; place-items: center; height: 100%; color: var(--muted); background: #fafbfc; }
        @media (max-width: 980px) {
            #main { width: 100%; margin: 0; padding: 28px 24px; border: 0; }
            .report-header { align-items: flex-start; }
            .report-mark { display: none; }
            .kpi-grid { grid-template-columns: repeat(2, minmax(0, 1fr)); }
            .kpi, .kpi:first-child, .kpi:last-child { padding: 22px 16px; border-right: 0; border-bottom: 1px solid var(--line); }
            .secondary-grid { grid-template-columns: 1fr; gap: 0; }
            .section-head { flex-direction: column; gap: 8px; }
            footer { flex-direction: column; }
        }
        @media (max-width: 560px) {
            .kpi-grid { grid-template-columns: 1fr; }
            .kpi-value { font-size: 25px; }
            .chart { height: 340px; }
        }
    </style>
</head>
<body>
<main id="main">
    <header class="report-header">
        <div>
            <p class="eyebrow">Mortal analysis report</p>
            <h1>$nickname</h1>
            <p class="subtitle">$metadata</p>
        </div>
        <div class="report-mark">BATCH MORTAL · REVIEW</div>
    </header>

    <section class="kpi-grid" aria-label="关键指标">
        <article class="kpi">
            <div class="kpi-label">半庄数</div>
            <div class="kpi-value">$total_games</div>
            <div class="kpi-note">四麻东南战</div>
        </article>
        <article class="kpi">
            <div class="kpi-label">近$recent_window半庄 Rating</div>
            <div class="kpi-value">$recent_average</div>
            <div class="kpi-note">$comparison_note</div>
        </article>
        <article class="kpi">
            <div class="kpi-label">Rating 中位数</div>
            <div class="kpi-value">$rating_median</div>
            <div class="kpi-note">全样本</div>
        </article>
        <article class="kpi">
            <div class="kpi-label">$ai_label</div>
            <div class="kpi-value">$ai_rate</div>
            <div class="kpi-note">$ai_note</div>
        </article>
        <article class="kpi">
            <div class="kpi-label">5% 恶手率</div>
            <div class="kpi-value">$bad_rate_5</div>
            <div class="kpi-note">$bad_move_note</div>
        </article>
    </section>

    <section class="chart-section">
        <div class="section-head">
            <div>
                <h2>Rating 推移</h2>
            </div>
        </div>
        <div id="rating-chart" class="chart" role="img" aria-label="Rating 推移"></div>
    </section>

    <div class="secondary-grid">
        $ai_section
        $distribution_section
    </div>

    <section class="review-section">
        <div class="section-head">
            <div>
                <h2>检讨候选</h2>
            </div>
        </div>
        <div class="table-wrap">
            <table>
                <thead><tr><th>序号</th><th>开局时间</th><th>模式</th><th>Rating</th><th>AI 一致率</th><th>5% 恶手率</th><th>操作</th></tr></thead>
                <tbody>$worst_rows</tbody>
            </table>
        </div>
    </section>

    <footer>
        <span>Rating 方差较大，仅用于筛选何切检讨，不代表牌力。</span>
        <span>生成自 Batch Mortal · 数据截至 $date_end</span>
    </footer>
</main>

<script>
const report = $payload;
const colors = {
    ink: '#172033', muted: '#687386', subtle: '#97a1b2', line: '#e4e8ef',
    indigo: '#3f51c6', indigoDark: '#28388f', indigoSoft: '#cbd3f5',
    amber: '#b57d24', amberSoft: '#f7ecd8'
};
const charts = [];

function escapeHtml(value) {
    const map = {'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#039;'};
    return String(value == null ? '' : value).replace(/[&<>"']/g, function(char) { return map[char]; });
}
function formatNumber(value, digits, suffix) {
    return value == null ? '—' : Number(value).toFixed(digits) + (suffix || '');
}
function axisTooltip(params) {
    const item = params.find(function(param) { return param.dataIndex != null; });
    if (!item) return '';
    const point = report.points[item.dataIndex];
    let content = '<div style="min-width:220px">';
    content += '<div style="font-weight:700;color:' + colors.ink + ';margin-bottom:9px">' + escapeHtml(point.label) + ' · ' + escapeHtml(point.startedAt || '时间未标注') + '</div>';
    content += '<div style="display:flex;justify-content:space-between;gap:24px"><span style="color:' + colors.muted + '">Rating</span><b>' + formatNumber(point.rating, 2) + '</b></div>';
    content += '<div style="display:flex;justify-content:space-between;gap:24px;margin-top:5px"><span style="color:' + colors.muted + '">AI 一致率</span><b>' + formatNumber(point.aiRate, 1, '%') + '</b></div>';
    if (point.aiDenominator != null) {
        content += '<div style="color:' + colors.subtle + ';font-size:11px;text-align:right">' + escapeHtml(point.aiNumerator) + ' / ' + escapeHtml(point.aiDenominator) + ' 次决策</div>';
    }
    content += '<div style="display:flex;justify-content:space-between;gap:24px;margin-top:5px"><span style="color:' + colors.muted + '">5% 恶手率</span><b>' + formatNumber(point.badRate5, 1, '%') + '</b></div>';
    content += '<div style="margin-top:8px;color:' + colors.subtle + ';font-size:11px">模式 ' + escapeHtml(point.mode) + '</div></div>';
    return content;
}
function xAxisConfig() {
    const step = Math.max(1, Math.ceil(report.totalGames / 12));
    return {
        type: 'category',
        boundaryGap: false,
        data: report.points.map(function(point) { return point.label; }),
        axisLine: { lineStyle: { color: colors.line } },
        axisTick: { show: false },
        axisLabel: {
            color: colors.subtle,
            fontSize: 11,
            interval: function(index) { return index === report.totalGames - 1 || index % step === 0; }
        }
    };
}
function zoomConfig() {
    if (report.totalGames <= 50) return [];
    return [
        { type: 'inside', start: 0, end: 100 },
        {
            type: 'slider', start: 0, end: 100, height: 18, bottom: 8,
            borderColor: 'transparent', backgroundColor: '#f2f4f8', fillerColor: '#e4e8fb',
            handleStyle: { color: colors.indigo, borderColor: colors.indigo },
            textStyle: { color: colors.subtle }
        }
    ];
}
function bindReviewLink(chart) {
    chart.on('click', function(params) {
        if (params.dataIndex == null) return;
        const point = report.points[params.dataIndex];
        const target = point.resultUrl || point.paipuUrl;
        if (/^https?:\/\//i.test(target)) window.open(target, '_blank', 'noopener');
    });
}
function renderRatingChart() {
    const chart = echarts.init(document.getElementById('rating-chart'));
    chart.group = 'batchmortal-trends';
    const series = [
        {
            name: '单半庄 Rating', type: 'scatter',
            data: report.points.map(function(point) {
                return {
                    value: point.rating,
                    symbol: point.isLow ? 'diamond' : 'circle',
                    symbolSize: point.isLow ? 11 : 7,
                    itemStyle: {
                        color: point.isLow ? colors.amberSoft : '#ffffff',
                        borderColor: point.isLow ? colors.amber : colors.indigo,
                        borderWidth: point.isLow ? 2 : 1.5,
                        opacity: point.isLow ? 1 : .72
                    }
                };
            }),
            z: 3,
            markLine: {
                silent: true, symbol: 'none',
                lineStyle: { color: colors.subtle, type: 'dashed', width: 1 },
                label: { color: colors.muted, formatter: '中位数 ' + Number(report.ratingMedian).toFixed(1), position: 'insideEndTop' },
                data: [{ yAxis: report.ratingMedian }]
            }
        }
    ];
    if (report.trendWindow) {
        series.push({
            name: report.trendWindow + '半庄移动平均', type: 'line', data: report.ratingRolling,
            symbol: 'none', connectNulls: false, smooth: false,
            lineStyle: { color: colors.indigoDark, width: 2.5 }, z: 4
        });
    }
    chart.setOption({
        animation: false,
        textStyle: { fontFamily: 'Inter, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif' },
        tooltip: { trigger: 'axis', confine: true, backgroundColor: '#ffffff', borderColor: colors.line, borderWidth: 1, padding: 12, formatter: axisTooltip },
        legend: { top: 0, right: 0, itemWidth: 18, itemHeight: 8, textStyle: { color: colors.muted, fontSize: 11 } },
        grid: { left: 54, right: 24, top: 42, bottom: report.totalGames > 50 ? 64 : 42 },
        xAxis: xAxisConfig(),
        yAxis: {
            type: 'value', min: report.ratingAxisMin, max: report.ratingAxisMax,
            axisLine: { show: true, lineStyle: { color: colors.line } }, axisTick: { show: false },
            axisLabel: { color: colors.subtle, fontSize: 11 },
            splitLine: { lineStyle: { color: colors.line, type: 'dashed' } }
        },
        dataZoom: zoomConfig(),
        series: series
    });
    bindReviewLink(chart);
    charts.push(chart);
}
function renderAiChart() {
    const element = document.getElementById('ai-chart');
    if (!element) return;
    const chart = echarts.init(element);
    chart.group = 'batchmortal-trends';
    const series = [{
        name: '单半庄一致率', type: 'scatter',
        data: report.points.map(function(point) { return point.aiRate; }),
        symbolSize: 6,
        itemStyle: { color: '#ffffff', borderColor: colors.indigo, borderWidth: 1.5, opacity: .72 },
        markLine: {
            silent: true, symbol: 'none',
            lineStyle: { color: colors.subtle, type: 'dashed', width: 1 },
            label: { color: colors.muted, formatter: '总体 ' + formatNumber(report.aiRate, 1, '%'), position: 'insideEndTop' },
            data: report.aiRate == null ? [] : [{ yAxis: report.aiRate }]
        }
    }];
    if (report.trendWindow) {
        series.push({
            name: report.trendWindow + '半庄移动平均', type: 'line', data: report.aiRolling,
            symbol: 'none', smooth: false, connectNulls: false,
            lineStyle: { color: colors.indigoDark, width: 2.25 }
        });
    }
    chart.setOption({
        animation: false,
        tooltip: { trigger: 'axis', confine: true, backgroundColor: '#ffffff', borderColor: colors.line, borderWidth: 1, padding: 12, formatter: axisTooltip },
        legend: { top: 0, right: 0, itemWidth: 18, itemHeight: 8, textStyle: { color: colors.muted, fontSize: 11 } },
        grid: { left: 48, right: 18, top: 42, bottom: 38 },
        xAxis: xAxisConfig(),
        yAxis: {
            type: 'value', min: report.aiAxisMin, max: 100,
            axisLine: { show: true, lineStyle: { color: colors.line } }, axisTick: { show: false },
            axisLabel: { color: colors.subtle, fontSize: 11, formatter: '{value}%' },
            splitLine: { lineStyle: { color: colors.line, type: 'dashed' } }
        },
        dataZoom: report.totalGames > 50 ? [{ type: 'inside', start: 0, end: 100 }] : [],
        series: series
    });
    bindReviewLink(chart);
    charts.push(chart);
}
function renderDistributionChart() {
    const element = document.getElementById('distribution-chart');
    if (!element) return;
    const chart = echarts.init(element);
    chart.setOption({
        animation: false,
        tooltip: { trigger: 'axis', axisPointer: { type: 'shadow' }, backgroundColor: '#ffffff', borderColor: colors.line, borderWidth: 1 },
        grid: { left: 40, right: 12, top: 18, bottom: 52 },
        xAxis: {
            type: 'category', data: report.histogram.map(function(bin) { return bin.label; }),
            axisLine: { lineStyle: { color: colors.line } }, axisTick: { show: false },
            axisLabel: { color: colors.subtle, fontSize: 10, rotate: 35 }
        },
        yAxis: {
            type: 'value', minInterval: 1,
            axisLine: { show: false }, axisTick: { show: false },
            axisLabel: { color: colors.subtle, fontSize: 10 },
            splitLine: { lineStyle: { color: colors.line, type: 'dashed' } }
        },
        series: [{
            name: '对局数', type: 'bar', data: report.histogram.map(function(bin) { return bin.count; }),
            barMaxWidth: 28,
            itemStyle: { color: colors.indigoSoft, borderColor: colors.indigo, borderWidth: 1 },
            label: { show: true, position: 'top', color: colors.muted, fontSize: 10 }
        }]
    });
    charts.push(chart);
}
function renderAll() {
    if (typeof echarts === 'undefined') {
        document.querySelectorAll('.chart').forEach(function(element) {
            element.innerHTML = '<div class="chart-error">图表资源加载失败；关键指标和检讨表仍可使用。</div>';
        });
        window.__BATCHMORTAL_READY__ = true;
        return;
    }
    renderRatingChart();
    renderAiChart();
    renderDistributionChart();
    echarts.connect('batchmortal-trends');
    window.addEventListener('resize', function() { charts.forEach(function(chart) { chart.resize(); }); });
    window.__BATCHMORTAL_READY__ = true;
}
renderAll();
</script>
</body>
</html>
""")


def generate_html(
    nickname: str,
    output_path: str,
    format_type: str = "xlsx",
    plot_limit: int | None = None,
    results_root: str | None = None,
) -> str | None:
    data = prepare_dashboard_data(
        read_results(nickname, format_type, output_root=results_root),
        plot_limit=plot_limit,
    )
    if not data:
        logging.warning("No valid rating data found to plot.")
        return None

    source_label = _display_source(data["sources"])
    mode_label = ", ".join(data["modes"]) or "模式未标注"
    model_label = ", ".join(data["modelTags"]) or "模型未标注"
    metadata = " · ".join(
        [
            _date_range(data["dateStart"], data["dateEnd"]),
            source_label,
            mode_label,
            f"Mortal {model_label}",
        ]
    )

    delta = data["comparisonDelta"]
    if delta is None:
        comparison_note = "至少 10 半庄后显示前后期比较"
    else:
        delta_class = "delta-up" if delta >= 0 else "delta-down"
        arrow = "↑" if delta >= 0 else "↓"
        comparison_note = (
            f'<span class="{delta_class}">{arrow} 较前{data["comparisonWindow"]}半庄 '
            f'{abs(delta):.2f}</span>'
        )

    ai_label = "加权 AI 一致率" if data["aiWeighted"] else "平均 AI 一致率"
    if data["aiRate"] is None:
        ai_note = "当前数据未包含有效一致率"
    elif data["aiDenominator"]:
        ai_note = f'{data["aiDenominator"]} 次决策样本'
    else:
        ai_note = "按有值半庄简单平均"

    if data["badRate5"] is None:
        bad_move_note = "未采集；开启 analyze_bad_move_rate 后显示"
    else:
        strict_note = f'10% 阈值 {_format_number(data["badRate10"], 1, "%")}'
        sample_note = f' · {data["badDenominator"]} 次决策' if data["badDenominator"] else ""
        bad_move_note = strict_note + sample_note

    ai_section = ""
    if data["aiRate"] is not None:
        ai_section = """
        <section class="chart-section">
            <div class="section-head">
                <div><h2>AI 一致率推移</h2></div>
            </div>
            <div id="ai-chart" class="chart" role="img" aria-label="AI 一致率推移"></div>
        </section>"""

    distribution_section = ""
    if data["histogram"]:
        distribution_section = """
        <section class="chart-section">
            <div class="section-head">
                <div><h2>Rating 分布</h2></div>
            </div>
            <div id="distribution-chart" class="chart" role="img" aria-label="Rating 分布"></div>
        </section>"""

    rendered = REPORT_TEMPLATE.substitute(
        page_title=html.escape(f"{nickname} · Mortal 分析报告"),
        nickname=html.escape(nickname),
        metadata=html.escape(metadata),
        total_games=str(data["totalGames"]),
        recent_window=str(data["recentWindow"]),
        recent_average=_format_number(data["recentAverage"], 2),
        comparison_note=comparison_note,
        rating_median=_format_number(data["ratingMedian"], 2),
        ai_label=ai_label,
        ai_rate=_format_number(data["aiRate"], 1, "%"),
        ai_note=html.escape(ai_note),
        bad_rate_5=_format_number(data["badRate5"], 1, "%"),
        bad_move_note=html.escape(bad_move_note),
        ai_section=ai_section,
        distribution_section=distribution_section,
        worst_rows=_worst_game_rows(data),
        date_end=html.escape(data["dateEnd"][:10] or "未知日期"),
        payload=_safe_json(data),
    )
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(rendered)
    return output_path


def save_png(html_path: str, png_path: str):
    from seleniumbase import SB

    abs_html = os.path.abspath(html_path)
    file_url = "file:///" + urllib.parse.quote(abs_html.replace("\\", "/"))

    with SB(uc=True, headless=True) as sb:
        sb.set_window_size(1500, 1100)
        sb.open(file_url)
        sb.wait_for_ready_state_complete()
        sb.wait_for_element_visible("#main")
        sb.sleep(1.0)
        report_height = sb.execute_script(
            "return Math.ceil(document.getElementById('main').getBoundingClientRect().height);"
        )
        sb.set_window_size(1500, min(16000, int(report_height) + 240))
        sb.sleep(0.5)
        sb.save_screenshot(png_path, selector="#main")


def plot_results(
    nickname: str,
    plot_mode: str,
    output_format: str = "xlsx",
    plot_limit: int | None = None,
    output_root: str | None = None,
):
    if plot_mode in ["none", None]:
        return

    safe_nick = _safe_nickname(nickname)
    if output_root is None:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        output_root = os.path.join(base_dir, "results", "majsoul", safe_nick)
    os.makedirs(output_root, exist_ok=True)

    html_path = os.path.join(output_root, f"report_{safe_nick}.html")
    png_path = os.path.join(output_root, f"report_{safe_nick}.png")

    logging.info(
        "Generating charts for %s (Mode: %s, Limit: %s)...",
        nickname,
        plot_mode,
        plot_limit or "all",
    )
    result = generate_html(
        nickname,
        html_path,
        output_format,
        plot_limit=plot_limit,
        results_root=output_root,
    )
    if not result:
        logging.warning("Skipping chart generation.")
        return

    if plot_mode in ["png", "both"]:
        try:
            save_png(html_path, png_path)
            logging.info("Saved PNG chart to: %s", png_path)
        except Exception as exc:
            logging.error("Failed to render PNG chart: %s", exc)

    if plot_mode in ["html", "both"]:
        logging.info("Saved HTML chart to: %s", html_path)
    elif plot_mode == "png":
        try:
            os.remove(html_path)
        except OSError:
            pass


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate Mortal Analysis Chart")
    parser.add_argument("nickname", help="Player nickname")
    parser.add_argument(
        "--plot-limit",
        "--plot_limit",
        type=int,
        default=None,
        help="Only use the latest N records for chart (default: all)",
        dest="plot_limit",
    )
    args = parser.parse_args()

    plot_results(args.nickname, "both", "xlsx", plot_limit=args.plot_limit)
