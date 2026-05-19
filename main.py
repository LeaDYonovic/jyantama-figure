import argparse
import logging
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone

from batchmortal.api import build_paipu_urls, get_player_records, search_player, get_player_nickname_by_id
from batchmortal.browser import BrowserAutomator, ReviewSubmissionCoordinator
from batchmortal.results import ResultWriter, parse_metadata, get_processed_uuids, read_result_rows
from batchmortal.visualize import plot_results
from seleniumbase import SB
from batchmortal.config import load_config


def configure_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(message)s",
        datefmt="%H:%M:%S",
        force=True,
    )


def log_line(message=""):
    logging.info(message)


def parse_args():
    pre_parser = argparse.ArgumentParser(add_help=False)
    pre_parser.add_argument("--config", help="Path to config file (yaml or toml)")
    pre_args, _ = pre_parser.parse_known_args()

    config = load_config(pre_args.config)

    parser = argparse.ArgumentParser(
        description="Batch Mortal Analysis Script (Python/SeleniumBase Edition)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )

    # -- General Options --
    parser.add_argument("--config", help="Path to config file (yaml or toml)")
    dry_run_default = config.get("dry_run", False)
    parser.add_argument(
        "--dry-run", "--dry_run",
        action="store_true" if not dry_run_default else "store_false",
        default=dry_run_default,
        help="Only print URLs, skip browser",
        dest="dry_run"
    )

    # -- Target Options --
    target_group = parser.add_argument_group("Target Options")
    target_group.add_argument(
        "-p", "-u", "--player", dest="player", default=config.get("player") or config.get("nickname"), help="Player nickname"
    )
    target_group.add_argument(
        "-a", "--account-id", "--account_id", dest="account_id", type=int, default=config.get("account_id"), help="Directly specify player account ID"
    )

    # -- Analysis Options --
    analysis_group = parser.add_argument_group("Analysis Options")
    analysis_group.add_argument(
        "--limit", type=int, default=config.get("limit", 10), help="Max records per mode"
    )
    analysis_group.add_argument(
        "--modes", default=str(config.get("modes", "9")), help="Comma-separated mode IDs"
    )
    analysis_group.add_argument(
        "--model-tag", "--model_tag", default=config.get("model_tag", "4.1b"), help="Mortal network version", dest="model_tag"
    )
    analysis_group.add_argument(
        "--retry", type=int, default=config.get("retry", 3), help="Retry failed review items this many times"
    )
    analyze_bad_move_rate_default = bool(config.get("analyze_bad_move_rate", False))
    analysis_group.add_argument(
        "--badmove",
        action="store_true",
        default=analyze_bad_move_rate_default,
        help="Analyze bad move rate from the Mortal result page",
        dest="analyze_bad_move_rate"
    )

    # -- Browser / Network Options --
    browser_group = parser.add_argument_group("Browser & Network Options")
    headless_default = config.get("headless", False)
    browser_group.add_argument(
        "--headless",
        action="store_true" if not headless_default else "store_false",
        default=headless_default,
        help="Run browser headlessly",
        dest="headless"
    )
    browser_group.add_argument(
        "--proxy", default=config.get("proxy"), help="Proxy URL (e.g. http://127.0.0.1:7890)"
    )

    # -- Output Options --
    output_group = parser.add_argument_group("Output Options")
    output_group.add_argument(
        "--output", choices=["csv", "xlsx"], default=config.get("output", "xlsx"), help="Output format"
    )
    output_group.add_argument(
        "--plot", choices=["none", "html", "png", "both"], default=config.get("plot", "none"), help="Generate a plot after analysis"
    )
    output_group.add_argument(
        "--plot-limit", "--plot_limit", type=int, default=config.get("plot_limit"), help="Only use the latest N records for chart (default: all)", dest="plot_limit"
    )
    save_screenshot_default = config.get("save_screenshot", False)
    output_group.add_argument(
        "--save-screenshot", "--save_screenshot",
        action="store_true" if not save_screenshot_default else "store_false",
        default=save_screenshot_default,
        help="Save screenshot of the results",
        dest="save_screenshot"
    )

    # -- Advanced Submission Options --
    submit_group = parser.add_argument_group("Advanced Submission Options")
    unsafe_parallel_default = config.get("unsafe_parallel_review", False)
    submit_group.add_argument(
        "--unsafe-parallel-review", "--unsafe_parallel_review",
        action="store_true" if not unsafe_parallel_default else "store_false",
        default=unsafe_parallel_default,
        help="Allow concurrent review submissions",
        dest="unsafe_parallel_review"
    )
    submit_group.add_argument(
        "--submit-interval", "--submit_interval", type=float, default=config.get("submit_interval", 6.0), help="Minimum spacing between controlled submissions in seconds", dest="submit_interval"
    )
    submit_group.add_argument(
        "--submit-cooldown", "--submit_cooldown", type=float, default=config.get("submit_cooldown", 30.0), help="Cooldown seconds after repeated review failures", dest="submit_cooldown"
    )
    prewarm_standby_default = config.get("prewarm_standby", False)
    submit_group.add_argument(
        "--prewarm-standby", "--prewarm_standby",
        action="store_true" if not prewarm_standby_default else "store_false",
        default=prewarm_standby_default,
        help="Experimental: use two persistent windows and alternate focus",
        dest="prewarm_standby"
    )

    # -- Legacy Options --
    legacy_group = parser.add_argument_group("Legacy Options")
    no_manual_verification_default = config.get("no_manual_verification", False)
    legacy_group.add_argument(
        "--no-manual-verification", "--no_manual_verification",
        action="store_true" if not no_manual_verification_default else "store_false",
        default=no_manual_verification_default,
        help="Legacy flag kept for compatibility",
        dest="no_manual_verification"
    )
    legacy_group.add_argument(
        "--flare-url", "--flare_url", default=config.get("flare_url"), help="Legacy flag kept for compatibility", dest="flare_url"
    )
    
    args = parser.parse_args()
    
    if not args.player and not args.account_id:
        parser.error("-p/--player or -a/--account-id is required either via command line arguments or config file")
        
    args.target_name = args.player if args.player else str(args.account_id)
        
    return args


def build_output_path(nickname: str, output_format: str) -> tuple[str, str]:
    safe_nick = "".join(
        c if c.isalnum() or c in ("_", "-", "\u4e00", "\u9fa5") else "_"
        for c in nickname
    )
    output_root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "results", safe_nick)
    out_path = os.path.join(output_root, f"results.{output_format}")
    return output_root, out_path


def detect_proxy(explicit_proxy: str | None) -> str | None:
    if explicit_proxy:
        return explicit_proxy
    sys_proxies = urllib.request.getproxies()
    return sys_proxies.get("https") or sys_proxies.get("http")


def collect_tasks(account_id: int, modes: list[int], limit: int, output_root: str, processed_uuids: set) -> list[dict]:
    tasks = []
    for mode in modes:
        log_line(f"[Mode {mode}] Fetching records...")
        try:
            records = get_player_records(account_id, limit, mode)
        except Exception as exc:
            logging.error(f"[ERROR] mode={mode}: {exc} - skipping this mode")
            continue

        if not records:
            logging.info(f"[mode={mode}] No records found. Skipping.")
            continue

        items = build_paipu_urls(records, account_id)
        mode_dir = os.path.join(output_root, f"mode_{mode}")
        for item in items:
            if item["uuid"] in processed_uuids:
                log_line(f"[Skip] uuid={item['uuid']} already processed.")
                continue
            tasks.append(
                {
                    "mode": mode,
                    "uuid": item["uuid"],
                    "paipu_url": item["paipuUrl"],
                    "start_time": item.get("startTime", ""),
                    "end_time": item.get("endTime", ""),
                    "mode_dir": mode_dir,
                }
            )

    total_tasks = len(tasks)
    for index, task in enumerate(tasks, start=1):
        task["idx"] = index
        task["total"] = total_tasks
        short_url = task["uuid"].split("-")[-1]
        task["log_prefix"] = f"[{index}/{total_tasks}][{short_url}]"

    return tasks


def print_summary(args, modes):
    log_line("=== Batch Mortal Analysis ===")
    target_display = args.target_name + (f" (ID: {args.account_id})" if args.account_id and args.target_name != str(args.account_id) else "")
    log_line(f"  Target:    {target_display}")
    log_line(f"  Modes:     {modes}")
    log_line(f"  Limit:     {args.limit} per mode")
    log_line(f"  ModelTag:  {args.model_tag}")
    log_line(f"  Headless:  {args.headless}")
    log_line(f"  DryRun:    {args.dry_run}")
    log_line(f"  Retry:     {args.retry}")
    log_line(f"  BadMove:   {args.analyze_bad_move_rate}")
    log_line("=============================")


def parse_float(value) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("%", "")
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def create_analysis_stats() -> dict:
    return {
        "rating_sum": 0.0,
        "rating_count": 0,
        "ai_rate_sum": 0.0,
        "ai_rate_count": 0,
        "bad_move_5_sum": 0.0,
        "bad_move_5_count": 0,
        "bad_move_10_sum": 0.0,
        "bad_move_10_count": 0,
    }


def add_average_sample(stats: dict, prefix: str, value):
    parsed = parse_float(value)
    if parsed is None:
        return
    stats[f"{prefix}_sum"] += parsed
    stats[f"{prefix}_count"] += 1


def format_average(stats: dict, prefix: str, suffix: str = "") -> str:
    count = stats.get(f"{prefix}_count", 0)
    if not count:
        return "N/A"
    return f"{stats[f'{prefix}_sum'] / count:.3f}{suffix}"


def log_final_averages(args, stats: dict):
    log_line(f"  AvgRating: {format_average(stats, 'rating')}")
    log_line(f"  AvgMatch:  {format_average(stats, 'ai_rate', '%')}")
    if args.analyze_bad_move_rate:
        log_line(f"  AvgBadMove5:  {format_average(stats, 'bad_move_5', '%')}")
        log_line(f"  AvgBadMove10: {format_average(stats, 'bad_move_10', '%')}")


def add_result_row_to_stats(stats: dict, row: dict, include_bad_move: bool):
    if str(row.get("rating", "")).strip() == "ERROR":
        return

    add_average_sample(stats, "rating", row.get("rating", ""))
    add_average_sample(stats, "ai_rate", row.get("aiConsistencyRate", ""))
    if include_bad_move:
        add_average_sample(stats, "bad_move_5", row.get("badMoveRate5", ""))
        add_average_sample(stats, "bad_move_10", row.get("badMoveRate10", ""))


def create_analysis_stats_from_rows(rows: list[dict], include_bad_move: bool) -> dict:
    stats = create_analysis_stats()
    for row in rows:
        add_result_row_to_stats(stats, row, include_bad_move)
    return stats


def load_final_analysis_stats(out_path: str, output_format: str, include_bad_move: bool) -> dict | None:
    try:
        rows = read_result_rows(out_path, output_format)
    except Exception as exc:
        logging.warning(f"Failed to read final result stats from {out_path}: {exc}")
        return None

    return create_analysis_stats_from_rows(rows, include_bad_move)


def consume_result_event(args, writer: ResultWriter, result_event: dict, stats: dict | None = None) -> tuple[int, int]:
    task = result_event["task"]
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    base_row = {
        "nickname": args.target_name,
        "mode": task["mode"],
        "uuid": task["uuid"],
        "paipuUrl": task["paipu_url"],
        "startTime": task.get("start_time", ""),
        "endTime": task.get("end_time", ""),
        "timestamp": timestamp,
    }

    if result_event["status"] == "success":
        result = result_event["result"]
        parsed = parse_metadata(result["metadata"])
        bad_move_stats = result.get("badMoveStats") or {}
        rating_value = parse_float(parsed.get("rating", ""))
        if stats is not None:
            add_average_sample(stats, "rating", rating_value)
            add_average_sample(stats, "ai_rate", parsed.get("aiConsistencyRate", ""))
            if args.analyze_bad_move_rate:
                add_average_sample(stats, "bad_move_5", bad_move_stats.get("badMoveRate5", ""))
                add_average_sample(stats, "bad_move_10", bad_move_stats.get("badMoveRate10", ""))
        writer.write_row(
            {
                **base_row,
                "resultUrl": result["resultUrl"],
                "modelTag": parsed.get("modelTag") or args.model_tag,
                "rating": rating_value if rating_value is not None else "",
                "aiConsistencyRate": parsed.get("aiConsistencyRate", ""),
                "aiConsistencyNumerator": parsed.get("aiConsistencyNumerator", ""),
                "aiConsistencyDenominator": parsed.get("aiConsistencyDenominator", ""),
                "temperature": parsed.get("temperature", ""),
                "gameLength": parsed.get("gameLength", ""),
                "playerId": parsed.get("playerId", ""),
                "reviewDuration": parsed.get("reviewDuration", ""),
                "screenshotPath": result.get("screenshotPath", ""),
                "badMoveRate5": bad_move_stats.get("badMoveRate5", ""),
                "badMoveCount5": bad_move_stats.get("badMoveCount5", ""),
                "badMoveRate10": bad_move_stats.get("badMoveRate10", ""),
                "badMoveCount10": bad_move_stats.get("badMoveCount10", ""),
                "badMoveDenominator": bad_move_stats.get("badMoveDenominator", ""),
            }
        )
        message = (
            f"{task['log_prefix']} OK "
            f"rating={parsed.get('rating', 'N/A')} "
            f"match={parsed.get('aiConsistencyRate', 'N/A')}"
        )
        if args.analyze_bad_move_rate:
            bad_move_denominator = bad_move_stats.get("badMoveDenominator", "")
            bad_move_5 = (
                f"{bad_move_stats.get('badMoveCount5', '')}/{bad_move_denominator}="
                f"{bad_move_stats.get('badMoveRate5', '')}"
                if bad_move_denominator
                else "N/A"
            )
            bad_move_10 = (
                f"{bad_move_stats.get('badMoveCount10', '')}/{bad_move_denominator}="
                f"{bad_move_stats.get('badMoveRate10', '')}"
                if bad_move_denominator
                else "N/A"
            )
            message += f" badMove5={bad_move_5} badMove10={bad_move_10}"
        log_line(message)
        return 1, 0

    writer.write_row(
        {
            **base_row,
            "modelTag": args.model_tag,
            "rating": "ERROR",
        }
    )
    log_line(f"{task['log_prefix']} ERROR")
    return 0, 1


def run_parallel_analysis(
    args,
    tasks: list[dict],
    out_path: str,
    automator: BrowserAutomator,
    stats: dict | None = None,
) -> tuple[int, int]:
    total_processed = 0
    total_failed = 0
    writer = ResultWriter(out_path, args.output)

    for task in tasks:
        task["model_tag"] = args.model_tag
        task["save_screenshot"] = args.save_screenshot
        task["analyze_bad_move_rate"] = args.analyze_bad_move_rate
    log_line("[Serial] Starting analysis with 1 persistent browser")

    try:
        with SB(uc=True, headless=automator.headless, proxy=automator.proxy) as sb:
            for task in tasks:
                result_event = None
                for attempt in range(args.retry + 1):
                    try:
                        result = automator.analyze_single(sb, task)
                        result_event = {"status": "success", "task": task, "result": result}
                        break
                    except Exception as exc:
                        prefix = task["log_prefix"]
                        logging.error(f"{prefix} ERROR exception: {exc}")
                        if attempt < args.retry:
                            logging.warning(
                                f"{prefix} RETRY ({attempt + 1}/{args.retry}) with a fresh page load."
                            )
                            continue

                        logging.error(
                            f"{prefix} SKIP permanently failed after {args.retry} retries."
                        )
                        result_event = {"status": "fail", "task": task}
                        break

                succeeded, failed = consume_result_event(args, writer, result_event, stats)
                total_processed += succeeded
                total_failed += failed
    finally:
        writer.close()

    return total_processed, total_failed


def run_controlled_pipeline_analysis(
    args,
    tasks: list[dict],
    out_path: str,
    automator: BrowserAutomator,
    stats: dict | None = None,
) -> tuple[int, int]:
    total_processed = 0
    total_failed = 0
    writer = ResultWriter(out_path, args.output)

    for task in tasks:
        task["model_tag"] = args.model_tag
        task["save_screenshot"] = args.save_screenshot
        task["analyze_bad_move_rate"] = args.analyze_bad_move_rate

    log_line("[Alternate] Starting two-window alternating review flow")

    try:
        for result_event in automator.iter_alternating_windows(tasks, max_retries=args.retry):
            succeeded, failed = consume_result_event(args, writer, result_event, stats)
            total_processed += succeeded
            total_failed += failed
    finally:
        writer.close()

    return total_processed, total_failed


def ensure_uc_driver():
    import seleniumbase
    sb_dir = seleniumbase.__path__[0]
    drivers_dir = os.path.join(sb_dir, 'drivers')
    uc_name = 'uc_driver.exe' if os.name == 'nt' else 'uc_driver'
    if not os.path.exists(os.path.join(drivers_dir, uc_name)):
        logging.warning("uc_driver not found locally. Preparing to install via domestic mirror...")
        try:
            import install_uc_driver
            install_uc_driver.install_uc_driver()
        except ImportError:
            logging.error("install_uc_driver module not found. Please ensure install_uc_driver.py is in the project root.")
        except Exception as e:
            logging.error(f"Auto-installation of uc_driver failed: {e}")


def main():
    configure_logging()
    start_time = time.time()
    args = parse_args()
    args.retry = max(0, args.retry)
    modes = [int(mode.strip()) for mode in args.modes.split(",")]

    if not args.dry_run:
        ensure_uc_driver()

    try:
        if args.account_id:
            account_id = args.account_id
            if not args.player:
                # Attempt to fetch nickname to use as the target
                fetched_name = get_player_nickname_by_id(account_id)
                if fetched_name:
                    args.target_name = fetched_name
                    logging.info(f"[API] Fetched nickname: '{fetched_name}' for account_id={account_id}")
        else:
            account_id = search_player(args.player)
    except Exception as exc:
        logging.error(f"[FATAL] {exc}")
        sys.exit(1)

    print_summary(args, modes)

    output_root, out_path = build_output_path(args.target_name, args.output)
    processed_uuids = get_processed_uuids(out_path, args.output)
    proxy = detect_proxy(args.proxy)

    if proxy:
        logging.info(f"[Proxy] Using proxy for browser: {proxy}")
    else:
        logging.info("[Proxy] No system proxy detected, running directly.")

    tasks = collect_tasks(account_id, modes, args.limit, output_root, processed_uuids)
    total_processed = 0
    total_failed = 0
    analysis_stats = create_analysis_stats()

    if args.dry_run:
        for task in tasks:
            log_line(f"{task['log_prefix']} dry-run mode={task['mode']} paipu_url={task['paipu_url']}")
            total_processed += 1
    elif tasks:
        if args.unsafe_parallel_review:
            automator = BrowserAutomator(
                headless=args.headless,
                proxy=proxy,
                submission_coordinator=None,
                controlled_submission=False,
            )
            total_processed, total_failed = run_parallel_analysis(args, tasks, out_path, automator, analysis_stats)
        elif args.prewarm_standby and len(tasks) >= 2:
            submission_coordinator = ReviewSubmissionCoordinator(
                base_interval=min(args.submit_interval, 1.0),
                cooldown_seconds=args.submit_cooldown,
            )
            automator = BrowserAutomator(
                headless=args.headless,
                proxy=proxy,
                submission_coordinator=submission_coordinator,
                controlled_submission=True,
            )
            total_processed, total_failed = run_controlled_pipeline_analysis(args, tasks, out_path, automator, analysis_stats)
        else:
            submission_coordinator = ReviewSubmissionCoordinator(
                base_interval=args.submit_interval,
                cooldown_seconds=args.submit_cooldown,
            )
            automator = BrowserAutomator(
                headless=args.headless,
                proxy=proxy,
                submission_coordinator=submission_coordinator,
                controlled_submission=True,
            )
            total_processed, total_failed = run_parallel_analysis(args, tasks, out_path, automator, analysis_stats)

    elapsed = time.time() - start_time
    final_stats = analysis_stats
    if not args.dry_run:
        final_stats = load_final_analysis_stats(out_path, args.output, args.analyze_bad_move_rate) or analysis_stats

    log_line("=== Done ===")
    log_line(f"  Succeeded: {total_processed}")
    log_line(f"  Failed:    {total_failed}")
    log_final_averages(args, final_stats)
    log_line(f"  Time:      {elapsed:.2f}s")
    if not args.dry_run:
        log_line(f"  Output:    {out_path}")
        plot_results(args.target_name, args.plot, args.output, plot_limit=args.plot_limit)
    log_line("============")


if __name__ == "__main__":
    main()
