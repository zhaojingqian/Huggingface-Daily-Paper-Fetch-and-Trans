#!/usr/bin/env python3
"""
Paper Trans — 翻译修复扫描器
自动找出 title_zh / summary_zh 为空的条目并重新翻译。

用法：
  python3 run_repair.py                        # 扫描全部 mode 近 30 天数据
  python3 run_repair.py --days 7               # 仅扫描最近 7 天
  python3 run_repair.py --mode daily           # 仅扫描 daily
  python3 run_repair.py --mode daily --key 2026-02-28   # 指定 key
  python3 run_repair.py --all                  # 强制扫描全部历史

定时建议（crontab）：
  每天 01:00 — 修复前一晚 daily 可能失败的条目
  每周日 04:00 — 修复 weekly 可能失败的条目
"""
import sys, os, argparse
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

LOGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
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


def main():
    parser = argparse.ArgumentParser(description="Paper Trans 翻译修复扫描器")
    parser.add_argument("--mode", choices=["daily", "weekly", "monthly"],
                        help="仅扫描指定 mode（默认全部）")
    parser.add_argument("--key", help="仅修复指定 key（如 2026-02-28 / 2026-W09）")
    parser.add_argument("--days", type=int, default=30,
                        help="扫描最近 N 天范围内的数据（默认 30）")
    parser.add_argument("--all", dest="scan_all", action="store_true",
                        help="扫描全部历史数据（忽略 --days）")
    args = parser.parse_args()

    _log("=" * 50)
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
