#!/usr/bin/env python3
"""
通用论文处理 runner
被 run_daily.py / run_monthly.py / main.py(weekly) 共用
"""
import os, sys, json, time, fcntl
from datetime import datetime
from pathlib import Path

BASE_DIR        = os.path.dirname(os.path.abspath(__file__))
DATA_DIR        = os.path.join(BASE_DIR, "data")
PAPER_STORE_DIR = os.path.join(DATA_DIR, "papers")   # 唯一数据源
LOGS_DIR        = os.path.join(BASE_DIR, "logs")
LOCK_DIR        = os.path.join(BASE_DIR, "locks")
sys.path.insert(0, BASE_DIR)


# ── Paper Store (统一存 JSON + PDF) ─────────────────────────────────────────
def _paper_pdf_path(arxiv_id):
    """PDF 唯一存储路径"""
    os.makedirs(PAPER_STORE_DIR, exist_ok=True)
    return os.path.join(PAPER_STORE_DIR, f"{arxiv_id}_zh.pdf")


def _pdf_store_hit(arxiv_id):
    """paper store 中有有效 PDF → 返回路径，否则 None"""
    p = _paper_pdf_path(arxiv_id)
    return p if os.path.exists(p) and os.path.getsize(p) > 10240 else None


def _pdf_store_save(arxiv_id, src_path):
    """成功生成的 PDF → 写入 paper store"""
    import shutil
    try:
        shutil.copy2(src_path, _paper_pdf_path(arxiv_id))
    except Exception as e:
        print(f"  ⚠️ paper store PDF 写入失败: {e}", flush=True)


def _paper_store_update_pdf_status(arxiv_id, status):
    """在 paper store JSON 里记录 pdf_status: ok / failed"""
    from translate_arxiv import paper_store_path
    try:
        p = paper_store_path(arxiv_id)
        if os.path.exists(p):
            with open(p, encoding="utf-8") as f:
                data = json.load(f)
            data["pdf_status"] = status
            with open(p, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass



# ── 进程级锁，防止同一 mode/key 并发执行 ─────────────────────────────────────
class RunLock:
    """对 mode/key 加文件锁，同一任务第二个进程直接退出"""
    def __init__(self, mode, key):
        os.makedirs(LOCK_DIR, exist_ok=True)
        self.path = os.path.join(LOCK_DIR, f"{mode}-{key}.lock")
        self._f = None

    def __enter__(self):
        self._f = open(self.path, "w")
        try:
            fcntl.flock(self._f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self._f.close()
            raise RuntimeError(f"另一个进程正在处理此任务，跳过: {self.path}")
        self._f.write(str(os.getpid()))
        self._f.flush()
        return self

    def __exit__(self, *_):
        if self._f:
            fcntl.flock(self._f, fcntl.LOCK_UN)
            self._f.close()
        try:
            os.remove(self.path)
        except OSError:
            pass


def setup_dirs(mode, key):
    """创建目录 data/<mode>/<key>/  和  data/papers/"""
    base = os.path.join(DATA_DIR, mode, key)
    os.makedirs(base, exist_ok=True)
    os.makedirs(PAPER_STORE_DIR, exist_ok=True)
    os.makedirs(LOGS_DIR, exist_ok=True)
    # 兼容：旧版 papers/ 子目录仍创建，防止老代码报错
    papers_subdir = os.path.join(base, "papers")
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
    # pdf_status 合并：pdf_zh 存在 → ok，pdf_zh_failed → failed
    if entry.get("pdf_zh"):
        s["pdf_status"] = "ok"
    elif entry.get("pdf_zh_failed"):
        s["pdf_status"] = "failed"
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
    with open(idx_file, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=2)
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

    base_dir, papers_dir = setup_dirs(mode, key)
    log(f"📁 {base_dir}", mode, key)

    # 1. 抓取
    papers = fetch_hf_papers(mode, key, limit)
    if not papers:
        log("❌ 未获取到论文", mode, key)
        return False

    log(f"✅ 获取到 {len(papers)} 篇", mode, key)

    # 2. API 配置
    config = load_api_config()
    log(f"📡 模型: {config['model']}", mode, key)

    # ★ 核心修复：在循环开始前一次性快照已有 index，不受后续 save_index() 影响
    prior = _load_prior_index(base_dir)

    # 3. 逐一翻译摘要
    papers_data = []
    ok = fail = 0

    for i, paper in enumerate(papers, 1):
        arxiv_id = paper.get("arxiv_id", "")
        if not arxiv_id:
            continue

        html_path = os.path.join(papers_dir, f"{arxiv_id}.html")
        if os.path.exists(html_path) and os.path.getsize(html_path) > 500:
            # ★ 从循环前快照中恢复，而非从动态写入的 index.json 里读
            existing_entry = prior.get(arxiv_id)

            # 若 title_zh 或 summary_zh 为空，删除旧 HTML 强制重新翻译
            if not existing_entry or not existing_entry.get("title_zh") or \
                    not existing_entry.get("summary_zh"):
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
                ok += 1
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
            ok += 1
            log(f"  ✅ {result.get('title_zh') or result.get('title', arxiv_id)}", mode, key)
        except Exception as e:
            log(f"  ❌ {arxiv_id}: {e}", mode, key)
            papers_data.append({"arxiv_id": arxiv_id, "rank": i, "error": str(e),
                                 "html_file": f"papers/{arxiv_id}.html"})
            fail += 1

        save_index(base_dir, mode, key, papers_data)

        if i < len(papers):
            time.sleep(2)

    idx_file = save_index(base_dir, mode, key, papers_data)

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
                save_index(base_dir, mode, key, papers_data)
                continue

            # ② 无缓存 → 调用翻译，输出直接写入 paper store
            log(f"  🔬 全文翻译: {aid}", mode, key)
            try:
                r = translate_full(arxiv_id=aid, output_dir=PAPER_STORE_DIR,
                                   no_cache=False, timeout=3600)
                if r.get("pdf_path"):
                    entry["pdf_zh"] = f"papers/{aid}_zh.pdf"
                    entry.pop("pdf_zh_failed", None)
                    _paper_store_update_pdf_status(aid, "ok")
                    log(f"  ✅ PDF: {r['pdf_path']}", mode, key)
                else:
                    entry["pdf_zh_failed"] = True
                    _paper_store_update_pdf_status(aid, "failed")
                    log(f"  ❌ {r.get('error','')}", mode, key)
            except Exception as e:
                entry["pdf_zh_failed"] = True
                _paper_store_update_pdf_status(aid, "failed")
                log(f"  ❌ {aid}: {e}", mode, key)
            save_index(base_dir, mode, key, papers_data)
        idx_file = save_index(base_dir, mode, key, papers_data)

    log(f"📊 完成: 成功={ok} 失败={fail}  {idx_file}", mode, key)
    return fail == 0


def repair(mode=None, key=None):
    """
    扫描已有数据目录，找出 title_zh / summary_zh 为空的条目，重新翻译并更新 index.json。
    mode=None 时扫描全部 (daily/weekly/monthly)。
    key=None  时扫描该 mode 下所有 key。
    """
    modes = [mode] if mode else ["daily", "weekly", "monthly"]
    from translate_arxiv import load_api_config, translate_and_save
    import re as _re

    config = load_api_config()
    total_fixed = 0

    for m in modes:
        mode_dir = os.path.join(DATA_DIR, m)
        if not os.path.isdir(mode_dir):
            continue
        keys = [key] if key else sorted(os.listdir(mode_dir))
        for k in keys:
            idx_file = os.path.join(mode_dir, k, "index.json")
            if not os.path.exists(idx_file):
                continue
            try:
                with open(idx_file, encoding="utf-8") as f:
                    idx = json.load(f)
            except Exception:
                continue

            from translate_arxiv import paper_store_read
            slim_papers = idx.get("papers", [])
            changed = False

            for slim in slim_papers:
                aid = slim.get("arxiv_id", "")
                if not aid:
                    continue

                # 从 paper store 检查翻译完整性
                stored = paper_store_read(aid)
                if stored and stored.get("title_zh") and stored.get("summary_zh"):
                    continue  # paper store 已有完整翻译，跳过

                print(f"[repair] {m}/{k} — 重新翻译: {aid}", flush=True)
                try:
                    result = translate_and_save(
                        arxiv_id=aid,
                        output_dir=PAPER_STORE_DIR,   # 直接写入 paper store
                        rank=slim.get("rank", 1),
                        week_str=f"{m}/{k}",
                        config=config,
                    )
                    if result.get("title_zh"):
                        changed = True
                        total_fixed += 1
                        print(f"[repair] ✅ {result['title_zh'][:60]}", flush=True)
                    else:
                        print(f"[repair] ❌ 仍无中文翻译: {aid}", flush=True)
                except Exception as e:
                    print(f"[repair] ❌ {aid}: {e}", flush=True)

            if changed:
                # slim index 本身不变（元数据在 paper store），只记录日志
                print(f"[repair] 💾 paper store 已更新，slim index 无需改变: {idx_file}", flush=True)

    print(f"[repair] 完成，共修复 {total_fixed} 篇", flush=True)
    return total_fixed


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Paper Trans runner / repair tool")
    sub = parser.add_subparsers(dest="cmd")

    r = sub.add_parser("repair", help="修复空翻译条目")
    r.add_argument("--mode", choices=["daily", "weekly", "monthly"], help="仅修复指定 mode")
    r.add_argument("--key", help="仅修复指定 key（如 2026-02-28 / 2026-W09）")

    args = parser.parse_args()
    if args.cmd == "repair":
        n = repair(mode=args.mode, key=args.key)
        sys.exit(0 if n >= 0 else 1)
    else:
        parser.print_help()
