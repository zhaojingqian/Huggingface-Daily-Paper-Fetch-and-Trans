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

周日 02:00 的当前周串行修复由 scripts/repair_weekly_current.py 负责；它会等待
当前 ISO 周 index.json 出现并等待 weekly 抓取锁释放，再执行摘要/翻译修复和 PDF 编译重试。
"""
import sys, os, argparse, json, re
from datetime import datetime, timedelta

from paperhub.modes import CONTENT_MODES, FETCH_MODES, mode_spec
from paperhub.paths import ROOT_DIR as BASE_DIR, LOGS_DIR, mode_dir, mode_index_path

sys.path.insert(0, BASE_DIR)

LOG_FILE = os.path.join(LOGS_DIR, "repair.log")
REFETCH_MODES = FETCH_MODES
_COUNTER_FIELDS = (
    "metadata_attempted",
    "metadata_succeeded",
    "metadata_failed",
    "summary_attempted",
    "summary_succeeded",
    "summary_failed",
    "pdf_attempted",
    "pdf_succeeded",
    "pdf_failed",
    "refetch_attempted",
    "refetch_succeeded",
    "refetch_failed",
    "summary_repaired",
)


def _new_stats():
    stats = {field: 0 for field in _COUNTER_FIELDS}
    stats["residual_failures"] = 0
    stats["residual_ids"] = []
    stats["audited_ids"] = []
    stats["abort_reason"] = ""
    return stats


def _merge_stats(target, source):
    for field in _COUNTER_FIELDS:
        target[field] += int(source.get(field, 0) or 0)
    residual_ids = set(target.get("residual_ids", []))
    source_residuals = set(source.get("residual_ids", []))
    source_audited = set(source.get("audited_ids", []))
    # A later persisted-state audit is authoritative for the IDs it inspected.
    residual_ids.difference_update(source_audited - source_residuals)
    residual_ids.update(source_residuals)
    audited_ids = set(target.get("audited_ids", []))
    audited_ids.update(source_audited)
    target["audited_ids"] = sorted(audited_ids)
    target["residual_ids"] = sorted(residual_ids)
    target["residual_failures"] = len(residual_ids)
    if source.get("abort_reason"):
        target["abort_reason"] = source["abort_reason"]
    return target


def _finish_stats(stats, residual_ids=()):
    result = dict(stats)
    ids = set(result.get("residual_ids", []))
    ids.update(str(item) for item in residual_ids if item)
    result["residual_ids"] = sorted(ids)
    result["residual_failures"] = len(ids)
    result["audited_ids"] = sorted(set(result.get("audited_ids", [])))
    return result


def _include_failure_artifact_residuals(stats):
    """Keep quota-aborted summaries honest without attempting more PDFs."""
    from paperhub.failure_reports import load_failure_records

    records = load_failure_records(os.path.join(LOGS_DIR, "pdf_errors"))
    residual_ids = set(stats.get("residual_ids", []))
    residual_ids.update(
        str(record.get("arxiv_id") or "")
        for record in records
        if record.get("arxiv_id")
    )
    stats["residual_ids"] = sorted(residual_ids)
    stats["residual_failures"] = len(residual_ids)
    return stats


def _stats_line(stats):
    return (
        f"metadata={stats['metadata_succeeded']}/{stats['metadata_attempted']}"
        f"(失败={stats['metadata_failed']}) "
        f"summary={stats['summary_succeeded']}/{stats['summary_attempted']}"
        f"(失败={stats['summary_failed']}, 修复={stats['summary_repaired']}) "
        f"pdf={stats['pdf_succeeded']}/{stats['pdf_attempted']}"
        f"(失败={stats['pdf_failed']}) "
        f"refetch={stats['refetch_succeeded']}/{stats['refetch_attempted']}"
        f"(失败={stats['refetch_failed']}) "
        f"残留={stats['residual_failures']}"
    )


def _return_stats_or_count(stats, field, return_stats):
    return stats if return_stats else stats[field]


def _log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    os.makedirs(LOGS_DIR, exist_ok=True)
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def _recent_keys(mode, days):
    """返回最近 N 天/周/月对应的 key 列表"""
    return list(mode_spec(mode).recent_keys(days))


def _validate_date_key(value, label):
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
        if parsed.strftime("%Y-%m-%d") != value:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError(f"无效的 {label} key: {value!r}（应为 YYYY-MM-DD）")


def validate_explicit_key(mode, key, topic=None):
    """Reject ambiguous, malformed, or path-like explicit repair keys."""
    value = str(key or "")
    if not value or value != value.strip():
        raise ValueError(f"无效的 {mode} key: {key!r}")

    if mode in ("daily", "manual"):
        _validate_date_key(value, mode)
        return value

    if mode == "weekly":
        if not re.fullmatch(r"\d{4}-W\d{2}", value):
            raise ValueError(
                f"无效的 weekly key: {value!r}（应为 YYYY-Www）"
            )
        try:
            parsed = datetime.strptime(value + "-1", "%G-W%V-%u")
            if parsed.strftime("%G-W%V") != value:
                raise ValueError
        except ValueError:
            raise ValueError(
                f"无效的 weekly key: {value!r}（ISO 周不存在）"
            )
        return value

    if mode == "monthly":
        try:
            parsed = datetime.strptime(value + "-01", "%Y-%m-%d")
            if parsed.strftime("%Y-%m") != value:
                raise ValueError
        except (TypeError, ValueError):
            raise ValueError(
                f"无效的 monthly key: {value!r}（应为 YYYY-MM）"
            )
        return value

    if mode == "topic":
        parts = value.split("/")
        if len(parts) == 1:
            date_key = parts[0]
        elif len(parts) == 2:
            slug, date_key = parts
            if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,59}", slug):
                raise ValueError(
                    f"无效的 topic key: {value!r}（slug 非法）"
                )
            if topic:
                from paperhub.topic_store import slugify
                if slugify(topic) != slug:
                    raise ValueError(
                        f"topic={topic!r} 与 key 中的 slug={slug!r} 不一致"
                    )
        else:
            raise ValueError(
                f"无效的 topic key: {value!r}（应为 YYYY-MM-DD 或 slug/YYYY-MM-DD）"
            )
        _validate_date_key(date_key, "topic")
        return value

    raise ValueError(f"不支持的 mode: {mode!r}")


def _week_key(dt):
    return mode_spec("weekly").key_for(dt.date() if isinstance(dt, datetime) else dt)


def _pending_refetch_key(mode, now=None):
    """
    Return the current key only when its scheduled first fetch has not run yet.

    Once the scheduled trigger time has passed, the key must be eligible for
    refetch so a transient network failure can be repaired the same day.
    """
    return mode_spec(mode).pending_refetch_key(now)


def _existing_recent_keys(mode, days):
    """Return recent scheduled keys that already have a mode directory."""
    path = mode_dir(mode)
    if not os.path.isdir(path):
        return []
    if mode == "manual":
        cutoff = (datetime.now().date() - timedelta(days=max(0, days - 1))).isoformat()
        return sorted(
            key
            for key in os.listdir(path)
            if len(key) == 10 and key >= cutoff
        )
    return sorted(set(_recent_keys(mode, days)) & set(os.listdir(path)))


def _index_has_papers(index_path):
    """Return whether an index has a non-empty, structurally valid paper list."""
    try:
        with open(index_path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            return False
        papers = data.get("papers")
        return bool(
            isinstance(papers, list)
            and papers
            and all(
                isinstance(item, dict)
                and isinstance(item.get("arxiv_id"), str)
                and item["arxiv_id"].strip()
                for item in papers
            )
        )
    except Exception:
        return False


def _index_arxiv_ids(index_path):
    """Return unique persisted IDs from a valid index, or an empty set."""
    try:
        import json
        with open(index_path, encoding="utf-8") as handle:
            payload = json.load(handle)
        papers = payload.get("papers", [])
        if not isinstance(papers, list):
            return set()
        return {
            str(item.get("arxiv_id"))
            for item in papers
            if isinstance(item, dict) and item.get("arxiv_id")
        }
    except Exception:
        return set()


def refetch_missing(mode="daily", days=3, key=None, return_stats=False):
    """
    扫描近 days 天内指定 mode 下缺少有效 index.json 的 key，重新执行完整抓取+翻译。

    各 mode 的 cron 触发时间不同，跳过"当前未到触发时间"的 key，避免误判：
      daily   — 每天 23:00 触发，23:00 前跳过今天
      weekly  — 每周日 02:00 触发，触发前跳过当前 ISO 周
      monthly — 每月 28 日 02:00 触发，触发前跳过当前月
    """
    from run_papers import run

    limit = mode_spec(mode).limit

    stats = _new_stats()
    residual_ids = set()
    if key:
        validate_explicit_key(mode, key)
        # Explicit keys are an exact operation.  Do not expand one failed
        # paper into a multi-day fetch scan or apply the scheduled-key skip.
        keys = [key]
    else:
        now = datetime.now()
        skip_key = _pending_refetch_key(mode, now)
        keys = [k for k in _recent_keys(mode, days) if k != skip_key]
    if not keys:
        _log(f"[refetch:{mode}] 近 {days} 天无需检查的 key，跳过")
        result = _finish_stats(stats)
        return _return_stats_or_count(result, "refetch_succeeded", return_stats)

    refetched = 0
    for target_key in keys:
        index_path = mode_index_path(mode, target_key)
        if _index_has_papers(index_path):
            _log(f"[refetch:{mode}] {target_key} — index.json 正常，跳过")
            continue

        stats["refetch_attempted"] += 1
        _log(f"[refetch:{mode}] {target_key} — 缺少有效 index.json，开始补抓...")
        try:
            ok = run(mode=mode, key=target_key, limit=limit, do_full_translate=True)
            if ok:
                _log(f"[refetch:{mode}] {target_key} — ✅ 补抓成功")
                refetched += 1
                stats["refetch_succeeded"] += 1
                stats["audited_ids"] = sorted(
                    set(stats.get("audited_ids", []))
                    | _index_arxiv_ids(index_path)
                )
            else:
                stats["refetch_failed"] += 1
                residual_ids.add(f"{mode}/{target_key}:refetch")
                _log(f"[refetch:{mode}] {target_key} — ❌ 补抓失败（仍有抓取/翻译/PDF 残留）")
        except Exception as e:
            stats["refetch_failed"] += 1
            residual_ids.add(f"{mode}/{target_key}:refetch")
            _log(f"[refetch:{mode}] {target_key} — ❌ 异常: {e}")

    _log(f"[refetch:{mode}] 完成，共补抓 {refetched} 个 key")
    result = _finish_stats(stats, residual_ids)
    return _return_stats_or_count(result, "refetch_succeeded", return_stats)


def retry_pdf_keys(
    mode, days, scan_all, key, return_stats=False, processed_ids=None
):
    """
    根据参数确定要重试 PDF 的 key 列表，调用 run_papers.retry_pdf()。
    返回成功翻译的篇数。
    """
    from run_papers import retry_pdf

    stats = _new_stats()
    if key:
        _log(f"[retry-pdf:{mode}] 指定 key={key}，开始重试...")
        result = retry_pdf(
            mode=mode,
            key=key,
            return_stats=True,
            processed_ids=processed_ids,
        )
        _merge_stats(stats, result)
        _log(f"[retry-pdf:{mode}] {key} — {_stats_line(stats)}")
        return _return_stats_or_count(stats, "pdf_succeeded", return_stats)

    if scan_all:
        _log(f"[retry-pdf:{mode}] 全量扫描，开始重试...")
        result = retry_pdf(
            mode=mode,
            key=None,
            return_stats=True,
            processed_ids=processed_ids,
        )
        _merge_stats(stats, result)
        _log(f"[retry-pdf:{mode}] 全量完成 — {_stats_line(stats)}")
        return _return_stats_or_count(stats, "pdf_succeeded", return_stats)

    mode_path = mode_dir(mode)
    if not os.path.isdir(mode_path):
        _log(f"[retry-pdf:{mode}] 目录不存在，跳过")
        return _return_stats_or_count(stats, "pdf_succeeded", return_stats)

    targets = _existing_recent_keys(mode, days)
    if not targets:
        _log(f"[retry-pdf:{mode}] 近 {days} 天无数据，跳过")
        return _return_stats_or_count(stats, "pdf_succeeded", return_stats)

    _log(f"[retry-pdf:{mode}] 扫描 {len(targets)} 个 key: {targets[0]} ~ {targets[-1]}")
    result = retry_pdf(
        mode=mode,
        keys=targets,
        return_stats=True,
        processed_ids=processed_ids,
    )
    _merge_stats(stats, result)
    _log(f"[retry-pdf:{mode}] 完成 — {_stats_line(stats)}")
    return _return_stats_or_count(stats, "pdf_succeeded", return_stats)


def _load_topic_index_for_audit(topic_store, slug, key):
    """Load a topic index without collapsing corruption into an empty result."""
    path = topic_store.index_path(slug, key)
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(payload, dict) or not isinstance(payload.get("papers"), list):
        return None
    return payload


def _topic_summary_stats(topic, days, scan_all, key):
    """Audit the persisted summary state for exactly the selected topic targets."""
    from paperhub import paper_store, topic_store
    from topic_engine import topic_repair_targets

    stats = _new_stats()
    residual_ids = set()
    targets = topic_repair_targets(
        topic=topic,
        key=key,
        days=None if key else days,
        scan_all=scan_all or bool(key),
    )
    if key and not targets:
        residual_ids.add(f"topic/{key}:index")

    ids = set()
    missing = []
    for profile, target_key in targets:
        slug = profile.get("slug", "")
        idx = _load_topic_index_for_audit(topic_store, slug, target_key)
        if idx is None:
            residual_ids.add(f"topic/{slug}/{target_key}:index")
            continue
        for position, slim in enumerate(idx.get("papers", []), 1):
            if not isinstance(slim, dict):
                missing.append(
                    f"topic/{slug}/{target_key}:invalid-paper-{position}"
                )
                continue
            aid = slim.get("arxiv_id", "")
            if not aid:
                missing.append(f"topic/{slug}/{target_key}:missing-id-{position}")
                continue
            ids.add(aid)
    stats["audited_ids"] = sorted(ids)
    stats["metadata_attempted"] = len(ids) + len(missing)
    stats["summary_attempted"] = len(ids)
    stats["metadata_failed"] = len(missing)
    residual_ids.update(missing)
    for aid in sorted(ids):
        stored = paper_store.read_raw(aid)
        if (
            str(stored.get("title", "")).strip()
            and str(stored.get("abstract") or stored.get("summary") or "").strip()
        ):
            stats["metadata_succeeded"] += 1
        else:
            stats["metadata_failed"] += 1
            residual_ids.add(aid)
        if paper_store.translation_complete(stored):
            stats["summary_succeeded"] += 1
        else:
            stats["summary_failed"] += 1
            residual_ids.add(aid)
    return _finish_stats(stats, residual_ids)


def _topic_pdf_failures(topic, days, scan_all, key):
    """Return retryable occurrence count and residual IDs for topic indexes."""
    from paperhub import paper_store, topic_store
    from topic_engine import topic_repair_targets

    targets = topic_repair_targets(
        topic=topic,
        key=key,
        days=None if key else days,
        scan_all=scan_all or bool(key),
    )
    residual_ids = set()
    candidate_ids = set()
    if key and not targets:
        residual_ids.add(f"topic/{key}:index")
    for profile, target_key in targets:
        slug = profile.get("slug", "")
        idx = _load_topic_index_for_audit(topic_store, slug, target_key)
        if idx is None:
            residual_ids.add(f"topic/{slug}/{target_key}:index")
            continue
        for position, slim in enumerate(idx.get("papers", []), 1):
            if not isinstance(slim, dict):
                residual_ids.add(
                    f"topic/{slug}/{target_key}:invalid-paper-{position}"
                )
                continue
            aid = slim.get("arxiv_id", "")
            if not aid:
                residual_ids.add(
                    f"topic/{slug}/{target_key}:missing-id-{position}"
                )
                continue
            status = slim.get("pdf_status")
            stored = paper_store.read_raw(aid)
            stored_status = (
                stored.get("pdf_status") if isinstance(stored, dict) else None
            )
            retryable = (
                paper_store.pdf_quality_tainted(aid)
                or status == "failed"
                or stored_status == "failed"
                or (
                    (status == "ok" or stored_status == "ok")
                    and not paper_store.pdf_hit(aid)
                )
            )
            if retryable:
                residual_ids.add(aid)
                candidate_ids.add(aid)
    return len(candidate_ids), residual_ids


def repair_topic_keys(
    topic, days, scan_all, key, return_stats=False, processed_ids=None
):
    """根据参数确定 topic 摘要修复范围，调用 topic_engine.repair_topic()。"""
    from topic_engine import repair_topic, topic_repair_targets

    label = f"topic={topic or 'all'}"
    if key:
        _log(f"[repair:topic] 指定 key={key} ({label})，开始修复...")
        n = repair_topic(
            topic=topic, key=key, scan_all=True, processed_ids=processed_ids
        )
        stats = _topic_summary_stats(topic, days, scan_all, key)
        stats["summary_repaired"] = n
        _log(f"[repair:topic] {key} — {_stats_line(stats)}")
        return _return_stats_or_count(stats, "summary_repaired", return_stats)

    if scan_all:
        _log(f"[repair:topic] 全量扫描 ({label})，开始修复...")
        n = repair_topic(
            topic=topic, scan_all=True, processed_ids=processed_ids
        )
        stats = _topic_summary_stats(topic, days, scan_all, key)
        stats["summary_repaired"] = n
        _log(f"[repair:topic] 全量完成 — {_stats_line(stats)}")
        return _return_stats_or_count(stats, "summary_repaired", return_stats)

    targets = topic_repair_targets(topic=topic, days=days, scan_all=False)
    if not targets:
        _log(f"[repair:topic] 近 {days} 天无数据，跳过")
        stats = _new_stats()
        return _return_stats_or_count(stats, "summary_repaired", return_stats)
    _log(f"[repair:topic] 扫描 {len(targets)} 个 topic/date")
    n = repair_topic(
        topic=topic,
        days=days,
        scan_all=False,
        processed_ids=processed_ids,
    )
    stats = _topic_summary_stats(topic, days, scan_all, key)
    stats["summary_repaired"] = n
    _log(f"[repair:topic] 完成 — {_stats_line(stats)}")
    return _return_stats_or_count(stats, "summary_repaired", return_stats)


def retry_topic_pdf_keys(
    topic, days, scan_all, key, return_stats=False, processed_ids=None
):
    """根据参数确定 topic PDF 重试范围，调用 topic_engine.retry_topic_pdf()。"""
    from topic_engine import retry_topic_pdf, topic_repair_targets

    label = f"topic={topic or 'all'}"
    attempted, _ = _topic_pdf_failures(topic, days, scan_all, key)
    if key:
        _log(f"[retry-pdf:topic] 指定 key={key} ({label})，开始重试...")
        n = retry_topic_pdf(
            topic=topic,
            key=key,
            scan_all=True,
            processed_ids=processed_ids,
        )
        _, residual_ids = _topic_pdf_failures(topic, days, scan_all, key)
        stats = _new_stats()
        stats["pdf_attempted"] = attempted
        stats["pdf_succeeded"] = n
        stats["pdf_failed"] = len([aid for aid in residual_ids if not aid.endswith(":index")])
        stats = _finish_stats(stats, residual_ids)
        _log(f"[retry-pdf:topic] {key} — {_stats_line(stats)}")
        return _return_stats_or_count(stats, "pdf_succeeded", return_stats)

    if scan_all:
        _log(f"[retry-pdf:topic] 全量扫描 ({label})，开始重试...")
        n = retry_topic_pdf(
            topic=topic,
            scan_all=True,
            processed_ids=processed_ids,
        )
        _, residual_ids = _topic_pdf_failures(topic, days, scan_all, key)
        stats = _new_stats()
        stats["pdf_attempted"] = attempted
        stats["pdf_succeeded"] = n
        stats["pdf_failed"] = len(residual_ids)
        stats = _finish_stats(stats, residual_ids)
        _log(f"[retry-pdf:topic] 全量完成 — {_stats_line(stats)}")
        return _return_stats_or_count(stats, "pdf_succeeded", return_stats)

    targets = topic_repair_targets(topic=topic, days=days, scan_all=False)
    if not targets:
        _log(f"[retry-pdf:topic] 近 {days} 天无数据，跳过")
        stats = _new_stats()
        return _return_stats_or_count(stats, "pdf_succeeded", return_stats)
    _log(f"[retry-pdf:topic] 扫描 {len(targets)} 个 topic/date")
    n = retry_topic_pdf(
        topic=topic,
        days=days,
        scan_all=False,
        processed_ids=processed_ids,
    )
    _, residual_ids = _topic_pdf_failures(topic, days, scan_all, key)
    stats = _new_stats()
    stats["pdf_attempted"] = attempted
    stats["pdf_succeeded"] = n
    stats["pdf_failed"] = len(residual_ids)
    stats = _finish_stats(stats, residual_ids)
    _log(f"[retry-pdf:topic] 完成 — {_stats_line(stats)}")
    return _return_stats_or_count(stats, "pdf_succeeded", return_stats)


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

    actions = [
        name
        for name, enabled in (
            ("--post", args.post),
            ("--refetch", args.refetch),
            ("--retry-pdf", args.retry_pdf),
        )
        if enabled
    ]
    if len(actions) > 1:
        parser.error(
            "--post、--refetch、--retry-pdf 是互斥执行模式，"
            f"不能同时指定：{', '.join(actions)}"
        )
    if args.refetch and args.mode and args.mode not in REFETCH_MODES:
        parser.error(
            f"--refetch 不支持 --mode {args.mode}；"
            f"可用模式：{', '.join(REFETCH_MODES)}"
        )
    if args.topic and args.mode != "topic":
        parser.error("--topic 仅可与 --mode topic 一起使用")
    if args.key and args.scan_all:
        parser.error("--key 与 --all 不能同时指定")
    if args.key and not args.mode:
        parser.error("--key 是精确操作，必须同时指定 --mode")
    if args.key:
        try:
            validate_explicit_key(args.mode, args.key, topic=args.topic)
        except ValueError as exc:
            parser.error(str(exc))
    if (
        args.post
        and args.scan_all
        and (args.mode is None or args.mode in REFETCH_MODES)
    ):
        parser.error(
            "--post --all 会产生无界补抓，已禁止；"
            "请用 --all 仅修复已有历史，或为 --post 指定有限 --days"
        )

    _log("=" * 50)

    # ── 组合模式：补翻译 → 补索引 ────────────────────────────────────────────
    if args.post:
        modes = [args.mode] if args.mode else CONTENT_MODES
        scope = f"key={args.key}" if args.key else ("all" if args.scan_all else f"days={args.days}")
        _log(f"开始 post (modes={modes}, {scope})")

        from run_papers import repair
        stats = _new_stats()
        processed_summary_ids = set()
        for m in modes:
            if m == "topic":
                result = repair_topic_keys(
                    args.topic,
                    args.days,
                    args.scan_all,
                    args.key,
                    return_stats=True,
                    processed_ids=processed_summary_ids,
                )
                _merge_stats(stats, result)
                continue
            if args.key:
                if not _index_has_papers(mode_index_path(m, args.key)):
                    _log(
                        f"[post:repair:{m}] {args.key} — index 缺失，"
                        "跳过旧索引修复并交给精确 refetch"
                    )
                else:
                    result = repair(
                        mode=m,
                        key=args.key,
                        return_stats=True,
                        processed_ids=processed_summary_ids,
                    )
                    _merge_stats(stats, result)
            elif args.scan_all:
                result = repair(
                    mode=m,
                    key=None,
                    return_stats=True,
                    processed_ids=processed_summary_ids,
                )
                _merge_stats(stats, result)
            else:
                targets = _existing_recent_keys(m, args.days)
                if not targets:
                    _log(f"[post:repair:{m}] 近 {args.days} 天无数据，跳过")
                    continue
                _log(f"[post:repair:{m}] 扫描 {len(targets)} 个 key: {targets[0]} ~ {targets[-1]}")
                result = repair(
                    mode=m,
                    keys=targets,
                    return_stats=True,
                    processed_ids=processed_summary_ids,
                )
                _merge_stats(stats, result)

        for m in [m for m in modes if m in REFETCH_MODES]:
            result = refetch_missing(
                mode=m,
                days=args.days if not args.scan_all else 9999,
                key=args.key,
                return_stats=True,
            )
            _merge_stats(stats, result)

        _log(f"post 完成 — {_stats_line(stats)}")
        if stats["residual_ids"]:
            _log(f"post 残留 — {', '.join(stats['residual_ids'])}")
        _log("=" * 50)
        sys.exit(1 if stats["residual_failures"] else 0)

    # ── PDF 重试模式 ─────────────────────────────────────────────────────────
    if args.retry_pdf:
        modes = [args.mode] if args.mode else CONTENT_MODES
        _log(f"开始 retry-pdf (modes={modes}, key={args.key or 'auto'}, "
             f"days={args.days if not args.scan_all else 'all'})")
        stats = _new_stats()
        processed_pdf_ids = set()
        for m in modes:
            if m == "topic":
                result = retry_topic_pdf_keys(
                    args.topic,
                    args.days,
                    args.scan_all,
                    args.key,
                    return_stats=True,
                    processed_ids=processed_pdf_ids,
                )
            else:
                result = retry_pdf_keys(
                    m,
                    args.days,
                    args.scan_all,
                    args.key,
                    return_stats=True,
                    processed_ids=processed_pdf_ids,
                )
            _merge_stats(stats, result)
            if stats.get("abort_reason"):
                _include_failure_artifact_residuals(stats)
                _log(
                    "retry-pdf 全局熔断 — "
                    f"{stats['abort_reason']}，停止剩余 mode"
                )
                break
        _log(f"retry-pdf 完成 — {_stats_line(stats)}")
        if stats["residual_ids"]:
            _log(f"retry-pdf 残留 — {', '.join(stats['residual_ids'])}")
        _log("=" * 50)
        sys.exit(1 if stats["residual_failures"] else 0)

    # ── 补抓模式：专门处理 fetch 完全失败的 key ───────────────────────────────
    if args.refetch:
        modes = [args.mode] if args.mode else REFETCH_MODES
        modes = [m for m in modes if m in REFETCH_MODES]
        scope = f"key={args.key}" if args.key else f"days={args.days}"
        _log(f"开始 refetch 补抓 (modes={modes}, {scope})")
        stats = _new_stats()
        for m in modes:
            result = refetch_missing(
                mode=m, days=args.days, key=args.key, return_stats=True
            )
            _merge_stats(stats, result)
        _log(f"refetch 完成 — {_stats_line(stats)}")
        if stats["residual_ids"]:
            _log(f"refetch 残留 — {', '.join(stats['residual_ids'])}")
        _log("=" * 50)
        sys.exit(1 if stats["residual_failures"] else 0)

    _log(f"开始 repair 扫描 (mode={args.mode or 'all'}, key={args.key or 'auto'}, "
         f"days={args.days if not args.scan_all else 'all'})")

    from run_papers import repair

    modes = [args.mode] if args.mode else CONTENT_MODES
    stats = _new_stats()
    processed_summary_ids = set()

    for m in modes:
        if m == "topic":
            result = repair_topic_keys(
                args.topic,
                args.days,
                args.scan_all,
                args.key,
                return_stats=True,
                processed_ids=processed_summary_ids,
            )
            _merge_stats(stats, result)
            continue
        if args.key:
            # 指定 key：直接修复
            result = repair(
                mode=m,
                key=args.key,
                return_stats=True,
                processed_ids=processed_summary_ids,
            )
            _merge_stats(stats, result)
        elif args.scan_all:
            # 全量扫描
            result = repair(
                mode=m,
                key=None,
                return_stats=True,
                processed_ids=processed_summary_ids,
            )
            _merge_stats(stats, result)
        else:
            # 仅扫描最近 N 天范围的 key
            targets = _existing_recent_keys(m, args.days)
            if not targets:
                _log(f"[{m}] 近 {args.days} 天无数据，跳过")
                continue
            _log(f"[{m}] 扫描 {len(targets)} 个 key: {targets[0]} ~ {targets[-1]}")
            result = repair(
                mode=m,
                keys=targets,
                return_stats=True,
                processed_ids=processed_summary_ids,
            )
            _merge_stats(stats, result)

    _log(f"repair 完成 — {_stats_line(stats)}")
    if stats["residual_ids"]:
        _log(f"repair 残留 — {', '.join(stats['residual_ids'])}")
    _log("=" * 50)
    sys.exit(1 if stats["residual_failures"] else 0)


if __name__ == "__main__":
    main()
