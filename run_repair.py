#!/usr/bin/env python3
"""
Paper Trans — 翻译修复扫描器
自动找出 title_zh / summary_zh 为空的条目并重新翻译。
支持 --refetch 模式：补抓因网络故障未生成 index.json 的日期。

用法：
  python3 run_repair.py                        # 扫描全部 mode 近 30 天数据
  python3 run_repair.py --days 7               # 仅扫描最近 7 天
  python3 run_repair.py --mode daily           # 仅扫描 daily
  python3 run_repair.py --mode daily --key 2026-02-28   # 指定 key
  python3 run_repair.py --all                  # 强制扫描全部历史
  python3 run_repair.py --refetch --days 3            # 补抓近 3 天所有 mode 缺失的 key
  python3 run_repair.py --refetch --mode daily        # 只补抓 daily
  python3 run_repair.py --refetch --mode weekly --days 14   # 补抓近 2 周的 weekly

定时建议（crontab）：
  每天 01:00 — 修复前夜 daily 可能失败的空翻译（仅扫近 2 天）
  每天 02:00 — 补抓前夜 fetch 失败（无 index.json）的 daily（仅扫近 2 天）
  每周日 04:00 — 修复 weekly 可能失败的条目
  每周日 05:00 — 补抓 weekly fetch 失败（仅扫近 2 周）
  每月 28 日 04:00 — 修复 monthly 可能失败的条目
  每月 28 日 05:00 — 补抓 monthly fetch 失败（仅扫近 2 月）
"""
import sys, os, argparse
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOGS_DIR = os.path.join(BASE_DIR, "logs")
LOG_FILE = os.path.join(LOGS_DIR, "repair.log")


def _log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    os.makedirs(LOGS_DIR, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _recent_keys(mode, days):
    """返回最近 N 天/周/月对应的 key 列表"""
    today = datetime.now().date()
    keys = set()
    for d in range(days):
        dt = today - timedelta(days=d)
        if mode == "daily":
            keys.add(dt.strftime("%Y-%m-%d"))
        elif mode == "weekly":
            y, w, _ = dt.isocalendar()
            keys.add(f"{y}-W{w:02d}")
        elif mode == "monthly":
            keys.add(dt.strftime("%Y-%m"))
    return sorted(keys)


def _index_has_papers(index_path):
    """index.json 存在且 papers 非空则返回 True"""
    try:
        import json
        with open(index_path, encoding="utf-8") as f:
            data = json.load(f)
        return bool(data.get("papers"))
    except Exception:
        return False


def refetch_missing(mode="daily", days=3):
    """
    扫描近 days 天内指定 mode 下缺少有效 index.json 的 key，重新执行完整抓取+翻译。

    各 mode 的 cron 触发时间不同，跳过"当前未到触发时间"的 key，避免误判：
      daily   — 每天 23:00 触发，跳过今天
      weekly  — 每周日 02:00 触发，跳过当前 ISO 周
      monthly — 每月 28 日 02:00 触发，跳过当前月
    """
    from run_papers import run, DATA_DIR
    from fetch_hf import current_week_key, current_month_key

    LIMITS = {"daily": 3, "weekly": 10, "monthly": 10}
    limit = LIMITS.get(mode, 10)

    now = datetime.now()
    skip_key = {
        "daily":   now.strftime("%Y-%m-%d"),
        "weekly":  current_week_key(),
        "monthly": now.strftime("%Y-%m"),
    }.get(mode)

    keys = [k for k in _recent_keys(mode, days) if k != skip_key]
    if not keys:
        _log(f"[refetch:{mode}] 近 {days} 天无需检查的 key，跳过")
        return 0

    refetched = 0
    for key in keys:
        index_path = os.path.join(DATA_DIR, mode, key, "index.json")
        if _index_has_papers(index_path):
            _log(f"[refetch:{mode}] {key} — index.json 正常，跳过")
            continue

        _log(f"[refetch:{mode}] {key} — 缺少有效 index.json，开始补抓...")
        try:
            ok = run(mode=mode, key=key, limit=limit, do_full_translate=True)
            if ok:
                _log(f"[refetch:{mode}] {key} — ✅ 补抓成功")
                refetched += 1
            else:
                _log(f"[refetch:{mode}] {key} — ❌ 补抓失败（fetch 仍返回空或翻译出错）")
        except Exception as e:
            _log(f"[refetch:{mode}] {key} — ❌ 异常: {e}")

    _log(f"[refetch:{mode}] 完成，共补抓 {refetched} 个 key")
    return refetched


def main():
    parser = argparse.ArgumentParser(description="Paper Trans 翻译修复扫描器")
    parser.add_argument("--mode", choices=["daily", "weekly", "monthly"],
                        help="仅扫描指定 mode（默认全部）")
    parser.add_argument("--key", help="仅修复指定 key（如 2026-02-28 / 2026-W09）")
    parser.add_argument("--days", type=int, default=30,
                        help="扫描最近 N 天范围内的数据（默认 30）")
    parser.add_argument("--all", dest="scan_all", action="store_true",
                        help="扫描全部历史数据（忽略 --days）")
    parser.add_argument("--refetch", action="store_true",
                        help="补抓模式：重新执行近期缺少 index.json 的任务（支持 --mode 筛选）")
    args = parser.parse_args()

    _log("=" * 50)

    # ── 补抓模式：专门处理 fetch 完全失败的 key ───────────────────────────────
    if args.refetch:
        modes = [args.mode] if args.mode else ["daily", "weekly", "monthly"]
        _log(f"开始 refetch 补抓 (modes={modes}, days={args.days})")
        total_refetched = 0
        for m in modes:
            total_refetched += refetch_missing(mode=m, days=args.days)
        _log(f"refetch 完成，共补抓 {total_refetched} 个 key")
        _log("=" * 50)
        sys.exit(0 if total_refetched >= 0 else 1)

    _log(f"开始 repair 扫描 (mode={args.mode or 'all'}, key={args.key or 'auto'}, "
         f"days={args.days if not args.scan_all else 'all'})")

    from run_papers import repair, DATA_DIR

    modes = [args.mode] if args.mode else ["daily", "weekly", "monthly"]
    total = 0

    for m in modes:
        if args.key:
            # 指定 key：直接修复
            n = repair(mode=m, key=args.key)
            total += n
        elif args.scan_all:
            # 全量扫描
            n = repair(mode=m, key=None)
            total += n
        else:
            # 仅扫描最近 N 天范围的 key
            mode_dir = os.path.join(DATA_DIR, m)
            if not os.path.isdir(mode_dir):
                continue
            recent = _recent_keys(m, args.days)
            existing = set(os.listdir(mode_dir))
            targets = sorted(set(recent) & existing)
            if not targets:
                _log(f"[{m}] 近 {args.days} 天无数据，跳过")
                continue
            _log(f"[{m}] 扫描 {len(targets)} 个 key: {targets[0]} ~ {targets[-1]}")
            for k in targets:
                n = repair(mode=m, key=k)
                total += n

    _log(f"repair 完成，共修复 {total} 篇")
    _log("=" * 50)
    sys.exit(0)


if __name__ == "__main__":
    main()
