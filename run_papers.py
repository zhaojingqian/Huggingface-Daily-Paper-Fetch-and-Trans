#!/usr/bin/env python3
"""
通用论文处理 runner
被 run_daily.py / run_monthly.py / main.py(weekly) 共用
"""
import os, sys, json, time, subprocess
from datetime import datetime
from pathlib import Path

from failure_taxonomy import is_failure_retryable
from paperhub import paper_store
from paperhub.json_io import read_json, write_json_atomic
from paperhub.publication_lock import (
    InvalidIndexError,
    PublicationBusyError,
    index_lock_path,
    index_publication_lock,
    lock_dir_for_index,
    merge_index_paper_fields,
    read_index_snapshot,
)
from paperhub.paths import (
    ROOT_DIR as BASE_DIR,
    DATA_DIR,
    PAPER_STORE_DIR,
    LOGS_DIR,
    LOCK_DIR,
    TEX_FAILED_BACKUP_DIR,
    mode_dir,
    mode_index_path,
    mode_key_dir,
    mode_papers_dir,
)

sys.path.insert(0, BASE_DIR)


# ── Paper Store (统一存 JSON + PDF) ─────────────────────────────────────────
def _paper_pdf_path(arxiv_id):
    """PDF 唯一存储路径"""
    return paper_store.pdf_path(arxiv_id)


def _pdf_quality_tainted(arxiv_id):
    """A quality taint survives later compile diagnostics until new PDF success."""
    if paper_store.pdf_quality_tainted(arxiv_id):
        return True
    diagnosis = read_json(
        os.path.join(LOGS_DIR, "pdf_errors", f"{arxiv_id}.json"),
        {},
    )
    return bool(
        isinstance(diagnosis, dict)
        and (
            diagnosis.get("phase") == "quality"
            or str(diagnosis.get("category", "")).startswith("quality.")
        )
    )


def _pdf_store_hit(arxiv_id, include_tainted=False):
    """Return a valid PDF only when it is publishable, unless explicitly physical-only."""
    if not include_tainted and _pdf_quality_tainted(arxiv_id):
        return None
    return paper_store.pdf_hit(arxiv_id)


def _pdf_store_save(arxiv_id, src_path):
    """成功生成的 PDF → 写入 paper store"""
    try:
        paper_store.save_pdf(arxiv_id, src_path)
    except Exception as e:
        print(f"  ⚠️ paper store PDF 写入失败: {e}", flush=True)


def _paper_store_update_pdf_status(arxiv_id, status):
    """在 paper store JSON 里记录 pdf_status: ok / failed"""
    paper_store.update_pdf_status(arxiv_id, status)


def _paper_store_mark_pdf_verified(arxiv_id):
    """Clear a persistent quality taint only after a new PDF passes all gates."""
    return paper_store.mark_pdf_verified(arxiv_id)


_STAT_FIELDS = (
    "metadata_attempted",
    "metadata_succeeded",
    "metadata_failed",
    "summary_attempted",
    "summary_succeeded",
    "summary_failed",
    "pdf_attempted",
    "pdf_succeeded",
    "pdf_failed",
)


def _new_stats():
    """Create a stable result shape shared by run/repair/retry commands."""
    stats = {field: 0 for field in _STAT_FIELDS}
    stats["residual_failures"] = 0
    stats["residual_ids"] = []
    return stats


def _finalize_stats(stats, residual_ids=()):
    result = dict(stats)
    ids = sorted({str(item) for item in residual_ids if item})
    result["residual_ids"] = ids
    result["residual_failures"] = len(ids)
    return result


def _merge_stats(target, source):
    """Merge counters and residual IDs into ``target`` in place."""
    for field in _STAT_FIELDS:
        target[field] = target.get(field, 0) + int(source.get(field, 0) or 0)
    ids = set(target.get("residual_ids", []))
    ids.update(source.get("residual_ids", []))
    target["residual_ids"] = sorted(ids)
    target["residual_failures"] = len(ids)
    return target


def _stats_line(stats):
    return (
        f"metadata={stats['metadata_succeeded']}/{stats['metadata_attempted']}"
        f"(失败={stats['metadata_failed']}) "
        f"summary={stats['summary_succeeded']}/{stats['summary_attempted']}"
        f"(失败={stats['summary_failed']}) "
        f"pdf={stats['pdf_succeeded']}/{stats['pdf_attempted']}"
        f"(失败={stats['pdf_failed']}) "
        f"残留={stats['residual_failures']}"
    )


def _metadata_complete(data):
    return bool(
        isinstance(data, dict)
        and str(data.get("title", "")).strip()
        and str(data.get("abstract") or data.get("summary") or "").strip()
    )


def _audit_translation_state(arxiv_ids, structural_residuals=(), missing_attempts=0):
    """Recompute final summary/metadata status from the persisted paper store."""
    ids = {str(item) for item in arxiv_ids if item}
    stats = _new_stats()
    stats["audited_ids"] = sorted(ids)
    stats["metadata_attempted"] = len(ids) + int(missing_attempts)
    stats["summary_attempted"] = len(ids)
    stats["metadata_failed"] = int(missing_attempts)
    residual_ids = {str(item) for item in structural_residuals if item}
    for aid in sorted(ids):
        stored = paper_store.read_raw(aid)
        if _metadata_complete(stored):
            stats["metadata_succeeded"] += 1
        else:
            stats["metadata_failed"] += 1
            residual_ids.add(aid)
        if paper_store.translation_complete(stored):
            stats["summary_succeeded"] += 1
        else:
            stats["summary_failed"] += 1
            residual_ids.add(aid)
    return _finalize_stats(stats, residual_ids)


def _clear_stale_failure_artifacts(arxiv_id):
    """Remove diagnostics that no longer describe a verified successful PDF."""
    paths = [
        os.path.join(LOGS_DIR, "pdf_errors", f"{arxiv_id}.json"),
        os.path.join(LOGS_DIR, "pdf_errors", f"{arxiv_id}.log"),
        os.path.join(TEX_FAILED_BACKUP_DIR, f"{arxiv_id}_merge_translate_zh.tex"),
    ]
    removed = []
    for path in paths:
        try:
            os.remove(path)
            removed.append(path)
        except FileNotFoundError:
            pass
        except OSError as exc:
            print(f"  ⚠️ 清理陈旧失败记录失败 {path}: {exc}", flush=True)
    return removed


def _accept_new_pdf(arxiv_id):
    """Promote a newly generated physical PDF and clear quality taint atomically enough."""
    physical_pdf = _pdf_store_hit(arxiv_id, include_tainted=True)
    if not physical_pdf:
        return None
    if not _paper_store_mark_pdf_verified(arxiv_id):
        return None
    _clear_stale_failure_artifacts(arxiv_id)
    return _pdf_store_hit(arxiv_id)



# ── 进程级锁，防止同一 mode/key 并发执行 ─────────────────────────────────────
class RunLock:
    """对 mode/key 加文件锁，同一任务第二个进程直接退出"""
    def __init__(self, mode, key, lock_dir=None):
        self.mode = mode
        self.key = key
        self.lock_dir = lock_dir or LOCK_DIR
        self.path = index_lock_path(mode, key, lock_dir=self.lock_dir)
        self._context = None

    def __enter__(self):
        self._context = index_publication_lock(
            self.mode,
            self.key,
            lock_dir=self.lock_dir,
            timeout=0,
        )
        try:
            self._context.__enter__()
        except PublicationBusyError:
            self._context = None
            raise RuntimeError(f"另一个进程正在处理此任务，跳过: {self.path}")
        return self

    def __exit__(self, *_):
        if self._context is not None:
            context, self._context = self._context, None
            context.__exit__(None, None, None)


def _publication_lock_dir(index_path, mode):
    return lock_dir_for_index(
        index_path,
        mode,
        data_dir=DATA_DIR,
        lock_dir=LOCK_DIR,
    )


def setup_dirs(mode, key):
    """创建目录 data/<mode>/<key>/  和  data/papers/"""
    base = mode_key_dir(mode, key)
    os.makedirs(base, exist_ok=True)
    os.makedirs(PAPER_STORE_DIR, exist_ok=True)
    os.makedirs(LOGS_DIR, exist_ok=True)
    # 兼容：旧版 papers/ 子目录仍创建，防止老代码报错
    papers_subdir = mode_papers_dir(mode, key)
    os.makedirs(papers_subdir, exist_ok=True)
    return base, papers_subdir


def get_log_file(mode, key):
    return os.path.join(LOGS_DIR, f"{mode}-{key}.log")


def log(msg, mode=None, key=None, also_print=True):
    ts   = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    if also_print:
        print(line, flush=True)
    if mode and key:
        with open(get_log_file(mode, key), "a", encoding="utf-8") as f:
            f.write(line + "\n")


_SLIM_KEYS = {"arxiv_id", "rank", "upvotes", "pdf_failed"}   # index.json 只保留这些


def _slim(entry):
    """从完整 entry 提取 slim 字段存入 index.json"""
    s = {k: entry[k] for k in _SLIM_KEYS if k in entry}
    # pdf_status：pdf_zh 存在→ok / pdf_zh_failed→failed / pdf_status="none"→none / 其余不写
    if entry.get("pdf_zh"):
        s["pdf_status"] = "ok"
    elif entry.get("pdf_zh_failed"):
        s["pdf_status"] = "failed"
    elif entry.get("pdf_status") == "none":
        s["pdf_status"] = "none"
    return s


def save_index(base_dir, mode, key, papers_data, extra=None):
    """写 slim index（只存 arxiv_id + rank + upvotes + pdf_status）"""
    slim_papers = [_slim(p) for p in papers_data]
    idx = {
        "mode": mode,
        "key": key,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(slim_papers),
        "papers": slim_papers,
    }
    if extra:
        idx.update(extra)
    idx_file = os.path.join(base_dir, "index.json")
    write_json_atomic(idx_file, idx)
    return idx_file


def _load_prior_index(base_dir):
    """运行前加载已有 index.json + paper store，合并为完整快照。
    必须在循环开始前一次性读取，避免被 save_index() 中途覆盖后读到截断数据。"""
    from translate_arxiv import paper_store_read
    idx_file = os.path.join(base_dir, "index.json")
    slim_map = {}
    try:
        with open(idx_file, encoding="utf-8") as f:
            data = json.load(f)
        slim_map = {p["arxiv_id"]: p for p in data.get("papers", []) if p.get("arxiv_id")}
    except Exception:
        pass

    # 用 paper store 补全完整元数据
    merged = {}
    for aid, slim in slim_map.items():
        full = paper_store_read(aid) or {}
        entry = {**full, **slim}   # slim 字段（rank/upvotes）优先覆盖
        # 恢复 pdf_zh / pdf_zh_failed 字段供后续逻辑使用
        status = slim.get("pdf_status") or full.get("pdf_status")
        if status == "ok" and _pdf_store_hit(aid):
            entry["pdf_zh"] = f"papers/{aid}_zh.pdf"   # 兼容旧字段
        elif status == "failed":
            entry["pdf_zh_failed"] = True
        merged[aid] = entry
    return merged


def run(mode, key, limit, do_full_translate=False):
    """
    主流程
    mode:  'daily' | 'weekly' | 'monthly'
    key:   日期字符串
    limit: 论文数上限
    """
    print("=" * 60, flush=True)
    print(f"📚 Paper Trans — {mode.upper()} {key}", flush=True)
    print("=" * 60, flush=True)

    try:
        lock = RunLock(mode, key)
        lock.__enter__()
    except RuntimeError as e:
        print(f"⚠️  {e}", flush=True)
        return False

    try:
        return _run_locked(mode, key, limit, do_full_translate)
    finally:
        lock.__exit__(None, None, None)


def _run_locked(mode, key, limit, do_full_translate):
    log(f"开始: {mode} {key}", mode, key)

    from fetch_hf import fetch_hf_papers
    from translate_arxiv import load_api_config, translate_and_save

    stats = _new_stats()
    residual_ids = set()
    base_dir, papers_dir = setup_dirs(mode, key)
    log(f"📁 {base_dir}", mode, key)

    # 1. 抓取
    papers = fetch_hf_papers(mode, key, limit)
    if not papers:
        log("❌ 未获取到论文", mode, key)
        stats["metadata_attempted"] = 1
        stats["metadata_failed"] = 1
        result_stats = _finalize_stats(stats, [f"{mode}/{key}:fetch"])
        log(f"📊 失败: {_stats_line(result_stats)}", mode, key)
        return False

    log(f"✅ 获取到 {len(papers)} 篇", mode, key)

    # 2. API 配置
    config = load_api_config()
    log(f"📡 模型: {config['model']}", mode, key)

    # ★ 核心修复：在循环开始前一次性快照已有 index，不受后续 save_index() 影响
    prior = _load_prior_index(base_dir)

    # 3. 逐一翻译摘要
    papers_data = []

    for i, paper in enumerate(papers, 1):
        arxiv_id = paper.get("arxiv_id", "")
        if not arxiv_id:
            log(f"  [{i}/{len(papers)}] ❌ 缺少 arxiv_id，无法处理", mode, key)
            continue

        html_path = os.path.join(papers_dir, f"{arxiv_id}.html")
        if os.path.exists(html_path) and os.path.getsize(html_path) > 500:
            # ★ 从循环前快照中恢复，而非从动态写入的 index.json 里读
            existing_entry = prior.get(arxiv_id)

            # 缓存必须真正包含中文标题和中文摘要；非空英文不能算成功。
            if not paper_store.translation_complete(existing_entry):
                log(f"  [{i}/{len(papers)}] 🔁 翻译不完整，重新翻译: {arxiv_id}", mode, key)
                try:
                    os.remove(html_path)
                except OSError:
                    pass
                # 进入下方翻译流程
            else:
                log(f"  [{i}/{len(papers)}] ⏭️  已存在: {arxiv_id}", mode, key)
                # 保留原 rank/upvotes，避免本次列表顺序变化时被覆盖
                entry = dict(existing_entry)
                entry["rank"] = i
                entry["upvotes"] = paper.get("upvotes", entry.get("upvotes", 0))
                papers_data.append(entry)
                continue

        log(f"  [{i}/{len(papers)}] 🔄 翻译: {arxiv_id}", mode, key)
        try:
            # week_str 传 "mode/key" 使 HTML 内嵌的"返回"链接指向正确路径
            result = translate_and_save(
                arxiv_id=arxiv_id,
                output_dir=papers_dir,
                rank=i,
                week_str=f"{mode}/{key}",
                config=config,
            )
            result["rank"] = i
            result["upvotes"] = paper.get("upvotes", 0)
            result["html_file"] = f"papers/{arxiv_id}.html"
            papers_data.append(result)
            persisted = paper_store.read_raw(arxiv_id)
            if paper_store.translation_complete(persisted):
                log(f"  ✅ {result.get('title_zh') or result.get('title', arxiv_id)}", mode, key)
            else:
                log(f"  ❌ {arxiv_id}: 翻译结果未通过中文完整性/持久化校验", mode, key)
        except Exception as e:
            log(f"  ❌ {arxiv_id}: {e}", mode, key)
            papers_data.append({"arxiv_id": arxiv_id, "rank": i, "error": str(e),
                                 "html_file": f"papers/{arxiv_id}.html"})

        save_index(base_dir, mode, key, papers_data)

        if i < len(papers):
            time.sleep(2)

    idx_file = save_index(base_dir, mode, key, papers_data)

    # 对最终持久化状态做统一门禁，避免 translate_and_save 返回后“假绿”。
    final_entries = {p.get("arxiv_id"): p for p in papers_data if p.get("arxiv_id")}
    stats["metadata_attempted"] = len(papers)
    for position, paper in enumerate(papers, 1):
        aid = paper.get("arxiv_id", "")
        if not aid:
            stats["metadata_failed"] += 1
            residual_ids.add(f"{mode}/{key}:missing-id-{position}")
            continue

        stored = paper_store.read_raw(aid)
        merged = {}
        merged.update(paper)
        merged.update(final_entries.get(aid, {}))
        if isinstance(stored, dict):
            merged.update(stored)
        has_metadata = bool(
            str(merged.get("title", "")).strip()
            and str(merged.get("abstract") or merged.get("summary") or "").strip()
        )
        if has_metadata:
            stats["metadata_succeeded"] += 1
        else:
            stats["metadata_failed"] += 1
            residual_ids.add(aid)

        stats["summary_attempted"] += 1
        if paper_store.translation_complete(stored):
            stats["summary_succeeded"] += 1
        else:
            stats["summary_failed"] += 1
            residual_ids.add(aid)

    # 4. 全文翻译（所有模式均支持，传 do_full_translate=False 可跳过）
    if do_full_translate:
        log("🔬 开始全文翻译...", mode, key)
        from translate_full import translate_full
        for entry in papers_data:
            aid = entry.get("arxiv_id", "")
            if not aid:
                continue

            # ① 命中 paper store PDF → 直接标记，无需重新翻译
            store_pdf = _pdf_store_hit(aid)
            if store_pdf:
                entry["pdf_zh"] = f"papers/{aid}_zh.pdf"
                entry.pop("pdf_zh_failed", None)
                entry.pop("pdf_status", None)
                log(f"  ⚡ paper store PDF 命中: {aid} ({os.path.getsize(store_pdf)//1024} KB)", mode, key)
                _paper_store_update_pdf_status(aid, "ok")
                _clear_stale_failure_artifacts(aid)
                save_index(base_dir, mode, key, papers_data)
                continue

            # ② 无缓存 → 调用翻译，输出直接写入 paper store
            log(f"  🔬 全文翻译: {aid}", mode, key)
            try:
                r = translate_full(arxiv_id=aid, output_dir=PAPER_STORE_DIR,
                                   no_cache=False, timeout=3600)
                verified_pdf = _accept_new_pdf(aid) if r.get("pdf_path") else None
                if r.get("pdf_path") and verified_pdf:
                    entry["pdf_zh"] = f"papers/{aid}_zh.pdf"
                    entry.pop("pdf_zh_failed", None)
                    log(f"  ✅ PDF: {r['pdf_path']}", mode, key)
                else:
                    entry["pdf_zh_failed"] = True
                    entry.pop("pdf_zh", None)
                    _paper_store_update_pdf_status(aid, "failed")
                    error = r.get("error", "") or "返回 PDF 路径但 paper store 校验失败"
                    log(f"  ❌ {error}", mode, key)
            except Exception as e:
                entry["pdf_zh_failed"] = True
                entry.pop("pdf_zh", None)
                _paper_store_update_pdf_status(aid, "failed")
                log(f"  ❌ {aid}: {e}", mode, key)
            save_index(base_dir, mode, key, papers_data)

        # 兜底：全文翻译结束后，仍无 pdf_zh 且无失败标志的条目 → 补标 failed
        for entry in papers_data:
            aid = entry.get("arxiv_id", "")
            if aid and not entry.get("pdf_zh") and not entry.get("pdf_zh_failed"):
                entry["pdf_zh_failed"] = True
                _paper_store_update_pdf_status(aid, "failed")
                log(f"  ⚠️ 补标 pdf_status=failed: {aid}", mode, key)

        idx_file = save_index(base_dir, mode, key, papers_data)

        # 以实际 PDF 文件而不是驱动返回值作为最终成功依据。
        for entry in papers_data:
            aid = entry.get("arxiv_id", "")
            if not aid:
                continue
            stats["pdf_attempted"] += 1
            if _pdf_store_hit(aid):
                stats["pdf_succeeded"] += 1
                entry["pdf_zh"] = f"papers/{aid}_zh.pdf"
                entry.pop("pdf_zh_failed", None)
                entry.pop("pdf_status", None)
                _paper_store_update_pdf_status(aid, "ok")
                _clear_stale_failure_artifacts(aid)
            else:
                stats["pdf_failed"] += 1
                entry["pdf_zh_failed"] = True
                entry.pop("pdf_zh", None)
                _paper_store_update_pdf_status(aid, "failed")
                residual_ids.add(aid)
        idx_file = save_index(base_dir, mode, key, papers_data)
    else:
        # do_full_translate=False 时，明确标记 pdf_status="none"（未尝试）
        for entry in papers_data:
            if not entry.get("pdf_zh") and not entry.get("pdf_zh_failed"):
                entry["pdf_status"] = "none"
        idx_file = save_index(base_dir, mode, key, papers_data)

    result_stats = _finalize_stats(stats, residual_ids)
    status = "完成" if not result_stats["residual_failures"] else "部分失败"
    log(f"📊 {status}: {_stats_line(result_stats)}  {idx_file}", mode, key)
    if result_stats["residual_ids"]:
        log(f"📌 残留: {', '.join(result_stats['residual_ids'])}", mode, key)
    return result_stats["residual_failures"] == 0


def retry_failed_pdf_entries(papers, label="[retry-pdf]", processed_ids=None):
    """
    对一组 slim index paper entries 中 pdf_status=failed 的条目重试全文 PDF。
    若条目标记 ok 但 paper store PDF 已缺失，会先降级为 failed 再重试。

    优先复用 paper store PDF；其次复用容器/宿主机里的翻译 tex 缓存只重跑编译；
    缓存重编译失败时再清缓存重新全文翻译。调用方负责把 papers 写回对应 index。
    """
    from translate_full import (
        CONTAINER_NAME,
        TEX_BACKUP_DIR,
        TEX_FAILED_BACKUP_DIR,
        _restore_tex_to_container,
        translate_full,
    )

    total_ok = 0
    total_fail = 0
    changed = False
    attempted = 0
    processed = processed_ids if processed_ids is not None else set()

    # Reconcile every verified store PDF before selecting retry candidates.
    # This also clears diagnostics left by an older failed attempt when the
    # index was already synchronized to ``ok`` through another mode.
    for slim in papers:
        aid = slim.get("arxiv_id", "")
        if not aid:
            continue
        status = slim.get("pdf_status")
        stored = paper_store.read_raw(aid)
        stored_status = (
            stored.get("pdf_status") if isinstance(stored, dict) else None
        )
        if stored_status == "failed" and status != "failed":
            # The paper store is shared across modes and is the durable source
            # of failure truth.  Older slim indexes may predate pdf_status or
            # still say "none"; do not let that hide a known failed PDF.
            slim["pdf_status"] = "failed"
            status = "failed"
            changed = True
        if _pdf_quality_tainted(aid) and status != "failed":
            slim["pdf_status"] = "failed"
            status = "failed"
            changed = True
        diagnosis = read_json(
            os.path.join(LOGS_DIR, "pdf_errors", f"{aid}.json"),
            {},
        )
        force_retranslation = (
            status == "failed"
            and diagnosis.get("retry_strategy") == "retry_translation"
        )
        if _pdf_store_hit(aid) and not force_retranslation:
            if status != "ok":
                slim["pdf_status"] = "ok"
                changed = True
            _paper_store_update_pdf_status(aid, "ok")
            _clear_stale_failure_artifacts(aid)
            if status == "failed":
                print(
                    f"{label} ✅ {aid} — paper store 已有 PDF，更新状态",
                    flush=True,
                )
                if aid not in processed:
                    processed.add(aid)
                    attempted += 1
                    total_ok += 1
            continue
        if status == "ok":
            print(f"{label} ⚠️  {aid} — pdf_status=ok 但 paper store 缺 PDF，降级重试", flush=True)
            slim["pdf_status"] = "failed"
            _paper_store_update_pdf_status(aid, "failed")
            changed = True

    failed = [p for p in papers if p.get("pdf_status") == "failed"]
    for slim in failed:
        aid = slim.get("arxiv_id", "")
        if not aid:
            continue
        if aid in processed:
            if _pdf_store_hit(aid):
                if slim.get("pdf_status") != "ok":
                    slim["pdf_status"] = "ok"
                    changed = True
                _paper_store_update_pdf_status(aid, "ok")
                _clear_stale_failure_artifacts(aid)
            continue
        diagnosis = read_json(os.path.join(LOGS_DIR, "pdf_errors", f"{aid}.json"), {})
        retry_strategy = diagnosis.get("retry_strategy", "")
        if diagnosis:
            print(
                f"{label} 🧭 {aid} — {diagnosis.get('category', 'unknown')} / "
                f"{retry_strategy or 'default'}",
                flush=True,
            )
        if diagnosis and not is_failure_retryable(diagnosis):
            print(
                f"{label} ⏸️  {aid} — {diagnosis.get('category', 'unknown')} "
                "需要人工处理，跳过自动全文重试",
                flush=True,
            )
            continue
        processed.add(aid)
        attempted += 1

        # paper store 已有有效 PDF（可能由其他途径生成）→ 直接更新状态。
        # 质量门禁或翻译阶段诊断明确要求重译时，旧 PDF 本身就是待替换
        # 的失败产物，不能因为结构完整就把它重新标绿。
        if _pdf_store_hit(aid) and retry_strategy != "retry_translation":
            print(f"{label} ✅ {aid} — paper store 已有 PDF，更新状态", flush=True)
            slim["pdf_status"] = "ok"
            _paper_store_update_pdf_status(aid, "ok")
            _clear_stale_failure_artifacts(aid)
            changed = True
            total_ok += 1
            continue

        # 检测是否已有翻译 tex，有则只重跑编译（优先查宿主机备份，再查容器内）
        container_tex = f"/gpt/gpt_log/arxiv_cache/{aid}/workfolder/merge_translate_zh.tex"
        has_container = subprocess.run(
            ["docker", "exec", CONTAINER_NAME,
             "test", "-s", container_tex],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ).returncode == 0

        host_tex = os.path.join(TEX_BACKUP_DIR, f"{aid}_merge_translate_zh.tex")
        has_host = os.path.exists(host_tex) and os.path.getsize(host_tex) > 0
        if not has_host:
            host_tex_failed = os.path.join(TEX_FAILED_BACKUP_DIR, f"{aid}_merge_translate_zh.tex")
            has_host = os.path.exists(host_tex_failed) and os.path.getsize(host_tex_failed) > 0

        if retry_strategy == "retry_translation":
            has_cache = False
            print(f"{label} 🔄 {aid} — 诊断要求重新翻译，跳过失败 TeX 缓存", flush=True)
        elif has_container:
            has_cache = True
            print(f"{label} 🔬 {aid} — 容器内有翻译缓存，只重跑编译...", flush=True)
        elif has_host:
            print(f"{label} 🔬 {aid} — 发现宿主机 tex 备份，恢复后只重跑编译...", flush=True)
            has_cache = _restore_tex_to_container(aid)
            if not has_cache:
                print(f"{label} ⚠️  {aid} — tex 恢复失败，改为重新翻译", flush=True)
        else:
            has_cache = False
            print(f"{label} 🔬 {aid} — 无翻译缓存，重新翻译全文...", flush=True)
        try:
            r = translate_full(arxiv_id=aid, output_dir=PAPER_STORE_DIR,
                               no_cache=not has_cache,
                               keep_translation=has_cache,
                               timeout=3600)
            if has_cache and not r.get("pdf_path"):
                latest = read_json(os.path.join(LOGS_DIR, "pdf_errors", f"{aid}.json"), {})
                latest_strategy = latest.get("retry_strategy", "")
                # 只在诊断明确要求重译时再次调用 GPT。缓存编译失败、驱动异常或
                # 尚未分类的错误都先保留翻译结果，等待定向补丁，避免昂贵且通常
                # 无效的全文重译掩盖真实编译问题。
                if latest_strategy != "retry_translation":
                    print(
                        f"{label} 🧭 {aid} — 诊断为 {latest.get('category', 'unknown')}，"
                        "保留翻译缓存等待定向编译补丁，不重复调用 GPT",
                        flush=True,
                    )
                else:
                    print(
                        f"{label} ⚠️  {aid} — 缓存重编译失败，清缓存后重新翻译全文...",
                        flush=True,
                    )
                    r = translate_full(arxiv_id=aid, output_dir=PAPER_STORE_DIR,
                                       no_cache=True,
                                       keep_translation=False,
                                       timeout=3600)
            verified_pdf = _accept_new_pdf(aid) if r.get("pdf_path") else None
            if r.get("pdf_path") and verified_pdf:
                slim["pdf_status"] = "ok"
                print(f"{label} ✅ {aid} — 成功: {r['pdf_path']}", flush=True)
                changed = True
                total_ok += 1
            else:
                slim["pdf_status"] = "failed"
                _paper_store_update_pdf_status(aid, "failed")
                error = r.get("error", "") or "返回 PDF 路径但 paper store 校验失败"
                print(f"{label} ❌ {aid} — 仍失败: {error}", flush=True)
                total_fail += 1
        except Exception as e:
            slim["pdf_status"] = "failed"
            _paper_store_update_pdf_status(aid, "failed")
            print(f"{label} ❌ {aid}: {e}", flush=True)
            total_fail += 1

    residual_ids = sorted({
        p.get("arxiv_id", "")
        for p in papers
        if p.get("arxiv_id") and p.get("pdf_status") == "failed"
    })
    return {
        "ok": total_ok,
        "failed": total_fail,
        "changed": changed,
        "pdf_attempted": attempted,
        "pdf_succeeded": total_ok,
        "pdf_failed": total_fail,
        "residual_failures": len(residual_ids),
        "residual_ids": residual_ids,
    }


def retry_pdf(mode=None, key=None, keys=None, return_stats=False, processed_ids=None):
    """
    扫描 pdf_status=failed 的条目，重新尝试全文 PDF 翻译，成功后更新 paper store 与 slim index。
    mode=None 时扫描全部 (daily/weekly/monthly/manual)。
    key=None  时扫描该 mode 下所有 key。
    默认返回成功翻译的篇数；return_stats=True 返回完整统计。
    """
    if key is not None and keys is not None:
        raise ValueError("key and keys are mutually exclusive")
    modes = [mode] if mode else ["daily", "weekly", "monthly", "manual"]
    processed = processed_ids if processed_ids is not None else set()
    candidate_ids = set()
    references = {}
    structural_residuals = set()

    for m in modes:
        mode_path = mode_dir(m)
        if not os.path.isdir(mode_path):
            requested_keys = [key] if key is not None else list(keys or ())
            structural_residuals.update(
                f"{m}/{requested_key}:index"
                for requested_key in requested_keys
            )
            continue
        selected_keys = (
            [key]
            if key is not None
            else list(keys)
            if keys is not None
            else sorted(os.listdir(mode_path))
        )

        for k in selected_keys:
            idx_file = mode_index_path(m, k)
            if not os.path.exists(idx_file):
                structural_residuals.add(f"{m}/{k}:index")
                continue
            lock_dir = _publication_lock_dir(idx_file, m)
            try:
                idx = read_index_snapshot(
                    idx_file,
                    mode=m,
                    key=k,
                    lock_dir=lock_dir,
                )
            except PublicationBusyError:
                print(
                    f"[retry-pdf] ⚠️ {m}/{k} 正在发布，跳过本轮",
                    flush=True,
                )
                structural_residuals.add(f"{m}/{k}:busy")
                continue
            except InvalidIndexError as exc:
                print(f"[retry-pdf] ❌ {m}/{k} index.json 无法读取: {exc}", flush=True)
                structural_residuals.add(f"{m}/{k}:index")
                continue

            papers = idx.get("papers", [])
            if not papers:
                continue
            before_statuses = {
                slim.get("arxiv_id"): slim.get("pdf_status")
                for slim in papers
                if slim.get("arxiv_id")
            }
            for slim in papers:
                aid = slim.get("arxiv_id", "")
                if not aid:
                    continue
                status = slim.get("pdf_status")
                stored = paper_store.read_raw(aid)
                stored_status = (
                    stored.get("pdf_status")
                    if isinstance(stored, dict)
                    else None
                )
                if (
                    status == "failed"
                    or stored_status == "failed"
                    or (status == "ok" and not _pdf_store_hit(aid))
                    or _pdf_quality_tainted(aid)
                ):
                    candidate_ids.add(aid)
            result = retry_failed_pdf_entries(
                papers,
                label=f"[retry-pdf] {m}/{k}",
                processed_ids=processed,
            )
            changed = result["changed"]

            current_papers = papers
            if changed:
                updates = {
                    slim.get("arxiv_id"): {
                        "pdf_status": slim.get("pdf_status")
                    }
                    for slim in papers
                    if slim.get("arxiv_id")
                    and slim.get("pdf_status") in {"ok", "failed", "none"}
                    and before_statuses.get(slim.get("arxiv_id"))
                    != slim.get("pdf_status")
                }
                try:
                    merged = merge_index_paper_fields(
                        idx_file,
                        updates,
                        mode=m,
                        key=k,
                        lock_dir=lock_dir,
                    )
                    current_papers = merged["payload"]["papers"]
                except PublicationBusyError:
                    print(
                        f"[retry-pdf] ⚠️ {m}/{k} 写回时正在发布，"
                        "保留为 residual",
                        flush=True,
                    )
                    structural_residuals.add(f"{m}/{k}:busy")
                except InvalidIndexError as exc:
                    print(
                        f"[retry-pdf] ❌ {m}/{k} 写回前 index 已失效: {exc}",
                        flush=True,
                    )
                    structural_residuals.add(f"{m}/{k}:index")

            for slim in current_papers:
                aid = slim.get("arxiv_id", "")
                if aid:
                    references.setdefault(aid, []).append(slim)

    stats = _new_stats()
    stats["audited_ids"] = sorted(candidate_ids)
    stats["pdf_attempted"] = len(candidate_ids)
    residual_ids = set(structural_residuals)
    for aid in sorted(candidate_ids):
        publishable = bool(_pdf_store_hit(aid))
        index_failed = any(
            slim.get("pdf_status") == "failed"
            for slim in references.get(aid, ())
        )
        if publishable and not index_failed:
            stats["pdf_succeeded"] += 1
        else:
            stats["pdf_failed"] += 1
            residual_ids.add(aid)
    result_stats = _finalize_stats(stats, residual_ids)
    print(f"[retry-pdf] 完成: {_stats_line(result_stats)}", flush=True)
    if result_stats["residual_ids"]:
        print(f"[retry-pdf] 残留: {', '.join(result_stats['residual_ids'])}", flush=True)
    return result_stats if return_stats else result_stats["pdf_succeeded"]


def repair(mode=None, key=None, keys=None, return_stats=False, processed_ids=None):
    """
    扫描已有数据目录，找出 title_zh / summary_zh 为空的条目，重新翻译并更新 index.json。
    mode=None 时扫描全部 (daily/weekly/monthly/manual)。
    key=None  时扫描该 mode 下所有 key。
    """
    modes = [mode] if mode else ["daily", "weekly", "monthly", "manual"]
    from translate_arxiv import load_api_config, translate_and_save

    if key is not None and keys is not None:
        raise ValueError("key and keys are mutually exclusive")

    config = load_api_config()
    total_fixed = 0
    processed = processed_ids if processed_ids is not None else set()
    scope_ids = set()
    structural_residuals = set()
    missing_attempts = 0

    for m in modes:
        mode_path = mode_dir(m)
        if not os.path.isdir(mode_path):
            requested_keys = [key] if key is not None else list(keys or ())
            structural_residuals.update(
                f"{m}/{requested_key}:index"
                for requested_key in requested_keys
            )
            continue
        selected_keys = (
            [key]
            if key is not None
            else list(keys)
            if keys is not None
            else sorted(os.listdir(mode_path))
        )
        for k in selected_keys:
            idx_file = mode_index_path(m, k)
            if not os.path.exists(idx_file):
                structural_residuals.add(f"{m}/{k}:index")
                continue
            lock_dir = _publication_lock_dir(idx_file, m)
            try:
                idx = read_index_snapshot(
                    idx_file,
                    mode=m,
                    key=k,
                    lock_dir=lock_dir,
                )
            except PublicationBusyError:
                print(
                    f"[repair] ⚠️ {m}/{k} 正在发布，跳过本轮",
                    flush=True,
                )
                structural_residuals.add(f"{m}/{k}:busy")
                continue
            except InvalidIndexError as exc:
                print(f"[repair] ❌ {m}/{k} index.json 无法读取: {exc}", flush=True)
                structural_residuals.add(f"{m}/{k}:index")
                continue

            slim_papers = idx.get("papers", [])
            changed = False

            for slim in slim_papers:
                aid = slim.get("arxiv_id", "")
                if not aid:
                    missing_attempts += 1
                    structural_residuals.add(f"{m}/{k}:missing-id")
                    continue
                scope_ids.add(aid)
                if aid in processed:
                    continue
                processed.add(aid)

                # 从 paper store 检查翻译完整性
                stored = paper_store.read_raw(aid)
                if paper_store.translation_complete(stored):
                    continue  # paper store 已有完整翻译，跳过

                print(f"[repair] {m}/{k} — 重新翻译: {aid}", flush=True)
                persisted = stored
                try:
                    result = translate_and_save(
                        arxiv_id=aid,
                        output_dir=PAPER_STORE_DIR,   # 直接写入 paper store
                        rank=slim.get("rank", 1),
                        week_str=f"{m}/{k}",
                        config=config,
                    )
                    persisted = paper_store.read_raw(aid)
                    if paper_store.translation_complete(persisted):
                        changed = True
                        total_fixed += 1
                        display_title = str(
                            persisted.get("title_zh")
                            or (result.get("title_zh") if isinstance(result, dict) else "")
                            or aid
                        )
                        print(f"[repair] ✅ {display_title[:60]}", flush=True)
                    else:
                        print(f"[repair] ❌ 仍无中文翻译: {aid}", flush=True)
                except Exception as e:
                    print(f"[repair] ❌ {aid}: {e}", flush=True)

            if changed:
                # slim index 本身不变（元数据在 paper store），只记录日志
                print(f"[repair] 💾 paper store 已更新，slim index 无需改变: {idx_file}", flush=True)

    result_stats = _audit_translation_state(
        scope_ids,
        structural_residuals=structural_residuals,
        missing_attempts=missing_attempts,
    )
    result_stats["summary_repaired"] = total_fixed
    print(f"[repair] 完成: 修复={total_fixed} {_stats_line(result_stats)}", flush=True)
    if result_stats["residual_ids"]:
        print(f"[repair] 残留: {', '.join(result_stats['residual_ids'])}", flush=True)
    return result_stats if return_stats else total_fixed


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Paper Trans runner / repair tool")
    sub = parser.add_subparsers(dest="cmd")

    r = sub.add_parser("repair", help="修复空翻译条目")
    r.add_argument("--mode", choices=["daily", "weekly", "monthly"], help="仅修复指定 mode")
    r.add_argument("--key", help="仅修复指定 key（如 2026-02-28 / 2026-W09）")

    args = parser.parse_args()
    if args.cmd == "repair":
        result = repair(mode=args.mode, key=args.key, return_stats=True)
        sys.exit(1 if result["residual_failures"] else 0)
    else:
        parser.print_help()
