#!/usr/bin/env python3
"""
通用论文处理 runner
被 run_daily.py / run_monthly.py / main.py(weekly) 共用
"""
import os, sys, json, time
from datetime import datetime
from pathlib import Path

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DATA_DIR   = os.path.join(BASE_DIR, "data")   # data/daily/ data/weekly/ data/monthly/
LOGS_DIR   = os.path.join(BASE_DIR, "logs")
sys.path.insert(0, BASE_DIR)


def setup_dirs(mode, key):
    """创建目录 data/<mode>/<key>/papers/"""
    base = os.path.join(DATA_DIR, mode, key)
    papers_dir = os.path.join(base, "papers")
    os.makedirs(papers_dir, exist_ok=True)
    os.makedirs(LOGS_DIR, exist_ok=True)
    return base, papers_dir


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


def save_index(base_dir, mode, key, papers_data, extra=None):
    idx = {
        "mode": mode,
        "key": key,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(papers_data),
        "papers": papers_data,
    }
    if extra:
        idx.update(extra)
    idx_file = os.path.join(base_dir, "index.json")
    with open(idx_file, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=2)
    return idx_file


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

    # 3. 逐一翻译摘要
    papers_data = []
    ok = fail = 0

    for i, paper in enumerate(papers, 1):
        arxiv_id = paper.get("arxiv_id", "")
        if not arxiv_id:
            continue

        html_path = os.path.join(papers_dir, f"{arxiv_id}.html")
        if os.path.exists(html_path) and os.path.getsize(html_path) > 500:
            log(f"  [{i}/{len(papers)}] ⏭️  已存在: {arxiv_id}", mode, key)
            # 从已有 index 恢复
            try:
                existing = json.load(open(os.path.join(base_dir, "index.json")))
                for ep in existing.get("papers", []):
                    if ep.get("arxiv_id") == arxiv_id:
                        papers_data.append(ep)
                        ok += 1
                        break
                else:
                    papers_data.append({"arxiv_id": arxiv_id, "rank": i,
                                        "html_file": f"papers/{arxiv_id}.html"})
                    ok += 1
            except Exception:
                papers_data.append({"arxiv_id": arxiv_id, "rank": i,
                                    "html_file": f"papers/{arxiv_id}.html"})
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
            pdf_path = os.path.join(papers_dir, f"{aid}_zh.pdf")
            if os.path.exists(pdf_path) and os.path.getsize(pdf_path) > 10240:
                log(f"  ⏭️  全文PDF已存在: {aid}", mode, key)
                entry["pdf_zh"] = f"papers/{aid}_zh.pdf"
                continue
            log(f"  🔬 全文翻译: {aid}", mode, key)
            try:
                r = translate_full(arxiv_id=aid, output_dir=papers_dir,
                                   no_cache=False, timeout=3600)
                if r.get("pdf_path"):
                    entry["pdf_zh"] = f"papers/{aid}_zh.pdf"
                    log(f"  ✅ PDF: {r['pdf_path']}", mode, key)
                else:
                    log(f"  ❌ {r.get('error','')}", mode, key)
            except Exception as e:
                log(f"  ❌ {aid}: {e}", mode, key)
            save_index(base_dir, mode, key, papers_data)
        idx_file = save_index(base_dir, mode, key, papers_data)

    log(f"📊 完成: 成功={ok} 失败={fail}  {idx_file}", mode, key)
    return fail == 0
