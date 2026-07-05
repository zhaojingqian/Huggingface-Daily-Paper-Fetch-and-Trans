#!/usr/bin/env python3
"""
Paper Trans — 翻译修复扫描器

模式说明：
  (默认)       补翻译：找出 title_zh/summary_zh 为空的条目并重新翻译
  --refetch    补索引：重新执行缺少 index.json 的任务
  --post       补翻译 + 补索引（顺序执行，等价于先默认再 --refetch）
  --retry-pdf  PDF 重试：对 pdf_status=failed 的条目重新翻译全文 PDF

通用参数（所有模式均支持）：
  --mode  daily|weekly|monthly|topic   仅处理指定 mode（默认全部）
  --key   2026-W12 或 opd/2026-07-05   仅处理指定 key
  --topic opd                         仅处理指定 topic（仅 --mode topic）
  --days  N                      扫描最近 N 天范围（默认 30）
  --all                          扫描全部历史（忽略 --days）

crontab 示例（当前配置）：
  每天 01:00   --post      --mode daily   --days 2   # 补翻译+补索引
  每天 06:00   --retry-pdf --mode daily   --days 7   # PDF 重试（docker 05:00 重启后）
  每周日 04:00 --post      --mode weekly  --days 14
  每周日 07:00 --retry-pdf --mode weekly  --days 14
  每月28日04:00 --post     --mode monthly --days 60
  每月28日07:00 --retry-pdf --mode monthly --days 60
  每天 06:30   --retry-pdf --mode topic   --days 7
"""
import sys, os, argparse
from datetime import datetime, timedelta

from paperhub.paths import ROOT_DIR as BASE_DIR, LOGS_DIR, mode_dir, mode_index_path

sys.path.insert(0, BASE_DIR)

LOG_FILE = os.path.join(LOGS_DIR, "repair.log")
CONTENT_MODES = ["daily", "weekly", "monthly", "topic"]
REFETCH_MODES = ["daily", "weekly", "monthly"]


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


def _week_key(dt):
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


def _pending_refetch_key(mode, now=None):
    """
    Return the current key only when its scheduled first fetch has not run yet.

    Once the scheduled trigger time has passed, the key must be eligible for
    refetch so a transient network failure can be repaired the same day.
    """
    now = now or datetime.now()

    if mode == "daily":
        return now.strftime("%Y-%m-%d") if now.hour < 23 else None

    if mode == "weekly":
        # weekly cron runs Sunday 02:00 for the current ISO week.
        if now.weekday() != 6 or now.hour < 2:
            return _week_key(now)
        return None

    if mode == "monthly":
        # monthly cron runs on the 28th at 02:00 for the current month.
        if now.day < 28 or (now.day == 28 and now.hour < 2):
            return now.strftime("%Y-%m")
        return None

    return None


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
      daily   — 每天 23:00 触发，23:00 前跳过今天
      weekly  — 每周日 02:00 触发，触发前跳过当前 ISO 周
      monthly — 每月 28 日 02:00 触发，触发前跳过当前月
    """
    from run_papers import run

    LIMITS = {"daily": 3, "weekly": 10, "monthly": 10}
    limit = LIMITS.get(mode, 10)

    now = datetime.now()
    skip_key = _pending_refetch_key(mode, now)

    keys = [k for k in _recent_keys(mode, days) if k != skip_key]
    if not keys:
        _log(f"[refetch:{mode}] 近 {days} 天无需检查的 key，跳过")
        return 0

    refetched = 0
    for key in keys:
        index_path = mode_index_path(mode, key)
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


def retry_pdf_keys(mode, days, scan_all, key):
    """
    根据参数确定要重试 PDF 的 key 列表，调用 run_papers.retry_pdf()。
    返回成功翻译的篇数。
    """
    from run_papers import retry_pdf

    if key:
        _log(f"[retry-pdf:{mode}] 指定 key={key}，开始重试...")
        n = retry_pdf(mode=mode, key=key)
        _log(f"[retry-pdf:{mode}] {key} — 成功 {n} 篇")
        return n

    if scan_all:
        _log(f"[retry-pdf:{mode}] 全量扫描，开始重试...")
        n = retry_pdf(mode=mode, key=None)
        _log(f"[retry-pdf:{mode}] 全量完成 — 成功 {n} 篇")
        return n

    mode_path = mode_dir(mode)
    if not os.path.isdir(mode_path):
        _log(f"[retry-pdf:{mode}] 目录不存在，跳过")
        return 0

    recent = _recent_keys(mode, days)
    existing = set(os.listdir(mode_path))
    targets = sorted(set(recent) & existing)
    if not targets:
        _log(f"[retry-pdf:{mode}] 近 {days} 天无数据，跳过")
        return 0

    _log(f"[retry-pdf:{mode}] 扫描 {len(targets)} 个 key: {targets[0]} ~ {targets[-1]}")
    total = 0
    for k in targets:
        n = retry_pdf(mode=mode, key=k)
        total += n
    _log(f"[retry-pdf:{mode}] 完成 — 成功 {total} 篇")
    return total


def repair_topic_keys(topic, days, scan_all, key):
    """根据参数确定 topic 摘要修复范围，调用 topic_engine.repair_topic()。"""
    from topic_engine import repair_topic, topic_repair_targets

    label = f"topic={topic or 'all'}"
    if key:
        _log(f"[repair:topic] 指定 key={key} ({label})，开始修复...")
        n = repair_topic(topic=topic, key=key, scan_all=True)
        _log(f"[repair:topic] {key} — 修复 {n} 篇")
        return n

    if scan_all:
        _log(f"[repair:topic] 全量扫描 ({label})，开始修复...")
        n = repair_topic(topic=topic, scan_all=True)
        _log(f"[repair:topic] 全量完成 — 修复 {n} 篇")
        return n

    targets = topic_repair_targets(topic=topic, days=days, scan_all=False)
    if not targets:
        _log(f"[repair:topic] 近 {days} 天无数据，跳过")
        return 0
    _log(f"[repair:topic] 扫描 {len(targets)} 个 topic/date")
    n = repair_topic(topic=topic, days=days, scan_all=False)
    _log(f"[repair:topic] 完成 — 修复 {n} 篇")
    return n


def retry_topic_pdf_keys(topic, days, scan_all, key):
    """根据参数确定 topic PDF 重试范围，调用 topic_engine.retry_topic_pdf()。"""
    from topic_engine import retry_topic_pdf, topic_repair_targets

    label = f"topic={topic or 'all'}"
    if key:
        _log(f"[retry-pdf:topic] 指定 key={key} ({label})，开始重试...")
        n = retry_topic_pdf(topic=topic, key=key, scan_all=True)
        _log(f"[retry-pdf:topic] {key} — 成功 {n} 篇")
        return n

    if scan_all:
        _log(f"[retry-pdf:topic] 全量扫描 ({label})，开始重试...")
        n = retry_topic_pdf(topic=topic, scan_all=True)
        _log(f"[retry-pdf:topic] 全量完成 — 成功 {n} 篇")
        return n

    targets = topic_repair_targets(topic=topic, days=days, scan_all=False)
    if not targets:
        _log(f"[retry-pdf:topic] 近 {days} 天无数据，跳过")
        return 0
    _log(f"[retry-pdf:topic] 扫描 {len(targets)} 个 topic/date")
    n = retry_topic_pdf(topic=topic, days=days, scan_all=False)
    _log(f"[retry-pdf:topic] 完成 — 成功 {n} 篇")
    return n


def main():
    parser = argparse.ArgumentParser(description="Paper Trans 翻译修复扫描器")
    parser.add_argument("--mode", choices=CONTENT_MODES,
                        help="仅扫描指定 mode（默认全部）")
    parser.add_argument("--key", help="仅修复指定 key（如 2026-02-28 / 2026-W09）")
    parser.add_argument("--topic", help="仅修复指定 topic slug（仅 --mode topic）")
    parser.add_argument("--days", type=int, default=30,
                        help="扫描最近 N 天范围内的数据（默认 30）")
    parser.add_argument("--all", dest="scan_all", action="store_true",
                        help="扫描全部历史数据（忽略 --days）")
    parser.add_argument("--refetch", action="store_true",
                        help="补索引模式：重新执行近期缺少 index.json 的任务")
    parser.add_argument("--post", action="store_true",
                        help="组合模式：顺序执行补翻译（默认）+ 补索引（--refetch）")
    parser.add_argument("--retry-pdf", dest="retry_pdf", action="store_true",
                        help="PDF 重试模式：对 pdf_status=failed 的条目重新翻译全文 PDF")
    args = parser.parse_args()

    _log("=" * 50)

    # ── 组合模式：补翻译 → 补索引 ────────────────────────────────────────────
    if args.post:
        modes = [args.mode] if args.mode else CONTENT_MODES
        scope = f"key={args.key}" if args.key else ("all" if args.scan_all else f"days={args.days}")
        _log(f"开始 post (modes={modes}, {scope})")

        from run_papers import repair
        total_repair = 0
        for m in modes:
            if m == "topic":
                total_repair += repair_topic_keys(args.topic, args.days, args.scan_all, args.key)
                continue
            if args.key:
                total_repair += repair(mode=m, key=args.key)
            elif args.scan_all:
                total_repair += repair(mode=m, key=None)
            else:
                mode_path = mode_dir(m)
                if not os.path.isdir(mode_path):
                    continue
                targets = sorted(set(_recent_keys(m, args.days)) & set(os.listdir(mode_path)))
                if not targets:
                    _log(f"[post:repair:{m}] 近 {args.days} 天无数据，跳过")
                    continue
                _log(f"[post:repair:{m}] 扫描 {len(targets)} 个 key: {targets[0]} ~ {targets[-1]}")
                for k in targets:
                    total_repair += repair(mode=m, key=k)

        total_refetch = 0
        for m in [m for m in modes if m in REFETCH_MODES]:
            total_refetch += refetch_missing(mode=m, days=args.days if not args.scan_all else 9999)

        _log(f"post 完成 — 修复摘要 {total_repair} 篇，补抓索引 {total_refetch} 个 key")
        _log("=" * 50)
        sys.exit(0)

    # ── PDF 重试模式 ─────────────────────────────────────────────────────────
    if args.retry_pdf:
        modes = [args.mode] if args.mode else CONTENT_MODES
        _log(f"开始 retry-pdf (modes={modes}, key={args.key or 'auto'}, "
             f"days={args.days if not args.scan_all else 'all'})")
        total = 0
        for m in modes:
            if m == "topic":
                total += retry_topic_pdf_keys(args.topic, args.days, args.scan_all, args.key)
            else:
                total += retry_pdf_keys(m, args.days, args.scan_all, args.key)
        _log(f"retry-pdf 完成，共成功 {total} 篇")
        _log("=" * 50)
        sys.exit(0)

    # ── 补抓模式：专门处理 fetch 完全失败的 key ───────────────────────────────
    if args.refetch:
        modes = [args.mode] if args.mode else REFETCH_MODES
        modes = [m for m in modes if m in REFETCH_MODES]
        _log(f"开始 refetch 补抓 (modes={modes}, days={args.days})")
        total_refetched = 0
        for m in modes:
            total_refetched += refetch_missing(mode=m, days=args.days)
        _log(f"refetch 完成，共补抓 {total_refetched} 个 key")
        _log("=" * 50)
        sys.exit(0 if total_refetched >= 0 else 1)

    _log(f"开始 repair 扫描 (mode={args.mode or 'all'}, key={args.key or 'auto'}, "
         f"days={args.days if not args.scan_all else 'all'})")

    from run_papers import repair

    modes = [args.mode] if args.mode else CONTENT_MODES
    total = 0

    for m in modes:
        if m == "topic":
            total += repair_topic_keys(args.topic, args.days, args.scan_all, args.key)
            continue
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
            mode_path = mode_dir(m)
            if not os.path.isdir(mode_path):
                continue
            recent = _recent_keys(m, args.days)
            existing = set(os.listdir(mode_path))
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
