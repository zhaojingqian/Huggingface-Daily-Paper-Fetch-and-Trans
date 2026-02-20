#!/usr/bin/env python3
"""Paper Hub Web Server — 端口 18080"""

import http.server, os, json, re, threading, subprocess, sys
from urllib.parse import unquote
from datetime import datetime, date
import urllib.request
import xml.etree.ElementTree as ET

PORT       = 18080
BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DATA_DIR   = os.path.join(BASE_DIR, "data")
WEEKLY_DIR = os.path.join(BASE_DIR, "weekly")   # 兼容旧目录
BOOKMARKS_FILE = os.path.join(DATA_DIR, "bookmarks.json")
_bm_lock   = threading.Lock()

# 部署路径前缀，如 /paper（nginx strip-prefix 模式）
# 通过环境变量注入：Environment=BASE_PATH=/paper
BASE_PATH  = os.environ.get("BASE_PATH", "").rstrip("/")



# ── 手动提交任务 ──────────────────────────────────────────────────────────────
MANUAL_DIR       = os.path.join(DATA_DIR, "manual")
SUBMIT_JOBS_FILE = os.path.join(MANUAL_DIR, "jobs.json")
_submit_lock     = threading.Lock()
_submit_queue    = []
_submit_running  = False

os.makedirs(MANUAL_DIR, exist_ok=True)


def _load_jobs():
    try:
        with open(SUBMIT_JOBS_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_jobs(jobs):
    with open(SUBMIT_JOBS_FILE, "w", encoding="utf-8") as f:
        json.dump(jobs, f, ensure_ascii=False, indent=2)


def _update_job(arxiv_id, **kw):
    with _submit_lock:
        jobs = _load_jobs()
        if arxiv_id not in jobs:
            jobs[arxiv_id] = {"arxiv_id": arxiv_id}
        jobs[arxiv_id].update(kw)
        jobs[arxiv_id]["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _save_jobs(jobs)


def fetch_arxiv_meta(arxiv_id):
    """从 arXiv API 获取论文元数据"""
    clean_id = arxiv_id.strip().split("v")[0]
    url = "http://export.arxiv.org/api/query?id_list=" + clean_id
    xml_data = None
    try:
        proxy = urllib.request.ProxyHandler(
            {"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"})
        opener = urllib.request.build_opener(proxy)
        with opener.open(url, timeout=30) as resp:
            xml_data = resp.read()
    except Exception:
        with urllib.request.urlopen(url, timeout=30) as resp:
            xml_data = resp.read()
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(xml_data)
    entry = root.find("atom:entry", ns)
    if entry is None:
        raise ValueError("arXiv 未找到论文: " + clean_id)
    title   = (entry.findtext("atom:title", "", ns) or "").strip().replace("\n", " ")
    summary = (entry.findtext("atom:summary", "", ns) or "").strip().replace("\n", " ")
    authors_list = [a.findtext("atom:name", "", ns)
                    for a in entry.findall("atom:author", ns)]
    published = (entry.findtext("atom:published", "", ns) or "")[:10]
    return {
        "arxiv_id": clean_id,
        "title": title,
        "summary": summary,
        "authors": "Authors:" + ", ".join(authors_list),
        "submitted": published,
        "url": "https://arxiv.org/abs/" + clean_id,
    }


def _upsert_manual_index(mode, key, paper_entry):
    idx_dir  = os.path.join(MANUAL_DIR, key)
    idx_file = os.path.join(idx_dir, "index.json")
    os.makedirs(idx_dir, exist_ok=True)
    try:
        with open(idx_file, encoding="utf-8") as f:
            idx = json.load(f)
    except Exception:
        idx = {"mode": mode, "key": key, "generated_at": "", "total": 0, "papers": []}
    papers = idx.get("papers", [])
    aid = paper_entry.get("arxiv_id", "")
    for i, p in enumerate(papers):
        if p.get("arxiv_id") == aid:
            papers[i] = paper_entry
            break
    else:
        papers.insert(0, paper_entry)
    idx["papers"] = papers
    idx["total"]  = len(papers)
    idx["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(idx_file, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=2)


def _do_submit_job(arxiv_id):
    """后台线程：抓元数据 -> 摘要翻译 -> 全文 PDF"""
    global _submit_running
    today = date.today().strftime("%Y-%m-%d")
    mode, key = "manual", today
    papers_dir = os.path.join(MANUAL_DIR, key, "papers")
    os.makedirs(papers_dir, exist_ok=True)
    try:
        _update_job(arxiv_id, status="fetching", msg="正在从 arXiv 获取元数据...")
        meta = fetch_arxiv_meta(arxiv_id)
        _update_job(arxiv_id, title=meta["title"],
                    submitted=meta.get("submitted", ""),
                    authors=meta.get("authors", ""),
                    mode=mode, key=key,
                    status="abstract", msg="正在翻译摘要...")
        sys.path.insert(0, BASE_DIR)
        from translate_arxiv import load_api_config, translate_and_save
        config = load_api_config()
        result = translate_and_save(
            arxiv_id=arxiv_id,
            output_dir=papers_dir,
            rank=0,
            week_str=mode + "/" + key,
            config=config,
        )
        paper_entry = dict(list(meta.items()) + list(result.items()))
        paper_entry["html_file"] = "papers/" + arxiv_id + ".html"
        paper_entry["rank"] = 0
        _upsert_manual_index(mode, key, paper_entry)
        _update_job(arxiv_id, title_zh=result.get("title_zh", ""),
                    status="full_pdf", msg="正在翻译全文 PDF（耗时较长）...")
        from translate_full import translate_full
        r = translate_full(arxiv_id=arxiv_id, output_dir=papers_dir,
                           no_cache=False, timeout=3600)
        if r.get("pdf_path"):
            paper_entry["pdf_zh"] = "papers/" + arxiv_id + "_zh.pdf"
            _upsert_manual_index(mode, key, paper_entry)
            _update_job(arxiv_id, status="done", msg="完成",
                        pdf_zh="papers/" + arxiv_id + "_zh.pdf")
        else:
            _update_job(arxiv_id, status="done_no_pdf",
                        msg="摘要完成，全文PDF失败: " + r.get("error", ""))
    except Exception as e:
        _update_job(arxiv_id, status="error", msg=str(e))
    finally:
        with _submit_lock:
            _submit_running = False
        _drain_submit_queue()


def _drain_submit_queue():
    global _submit_running, _submit_queue
    with _submit_lock:
        if _submit_running or not _submit_queue:
            return
        next_id = _submit_queue.pop(0)
        _submit_running = True
    t = threading.Thread(target=_do_submit_job, args=(next_id,), daemon=True)
    t.start()


def enqueue_submit(arxiv_id):
    with _submit_lock:
        jobs = _load_jobs()
        if arxiv_id in jobs and jobs[arxiv_id].get("status") not in ("error", "done_no_pdf"):
            return False, "已存在或正在处理中"
        jobs[arxiv_id] = {
            "arxiv_id": arxiv_id, "status": "queued",
            "msg": "排队等待中",
            "submitted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        _save_jobs(jobs)
        _submit_queue.append(arxiv_id)
    _drain_submit_queue()
    return True, "已加入队列"


# ── 收藏存储 ──────────────────────────────────────────────────────────────────
def load_bookmarks():
    """返回 {lists: {lid: {name, papers: [{arxiv_id,mode,key,added}]}}}"""
    if os.path.exists(BOOKMARKS_FILE):
        try:
            with open(BOOKMARKS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"lists": {}}


def save_bookmarks(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(BOOKMARKS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_paper_entry(mode, key, arxiv_id):
    """从 index.json 取论文元数据，找不到返回最小字典"""
    idx = load_index(mode, key)
    if idx:
        for p in idx.get("papers", []):
            if p.get("arxiv_id") == arxiv_id:
                return p
    return {"arxiv_id": arxiv_id}


# ── 数据加载 ──────────────────────────────────────────────────────────────────
def index_path(mode, key):
    p = os.path.join(DATA_DIR, mode, key, "index.json")
    if os.path.exists(p):
        return p
    # 兼容旧 weekly/ 目录
    if mode == "weekly":
        p2 = os.path.join(WEEKLY_DIR, key, "index.json")
        if os.path.exists(p2):
            return p2
    return None

def load_index(mode, key):
    p = index_path(mode, key)
    if p:
        try:
            return json.load(open(p, encoding="utf-8"))
        except Exception:
            pass
    return None

def papers_dir(mode, key):
    """论文 HTML/PDF 所在目录"""
    d = os.path.join(DATA_DIR, mode, key, "papers")
    if os.path.exists(d):
        return d
    if mode == "weekly":
        d2 = os.path.join(WEEKLY_DIR, key, "papers")
        if os.path.exists(d2):
            return d2
    return d

def list_keys(mode):
    """按时间倒序列出某 mode 下所有已有数据的 key"""
    keys = []
    d = os.path.join(DATA_DIR, mode)
    if os.path.exists(d):
        keys = sorted([k for k in os.listdir(d)
                       if os.path.isdir(os.path.join(d, k))], reverse=True)
    # 兼容旧 weekly/
    if mode == "weekly" and os.path.exists(WEEKLY_DIR):
        old = sorted([k for k in os.listdir(WEEKLY_DIR)
                      if os.path.isdir(os.path.join(WEEKLY_DIR, k))
                      and k not in keys], reverse=True)
        keys = sorted(list(set(keys + old)), reverse=True)
    return keys

def count_pdfs(mode, key, index):
    if not index:
        return 0
    pd = papers_dir(mode, key)
    return sum(1 for p in index.get("papers", [])
               if p.get("pdf_zh") and
               os.path.exists(os.path.join(pd, p["pdf_zh"].replace("papers/", ""))))


# ── CSS / 公共样式 ─────────────────────────────────────────────────────────────
CSS = """
*{box-sizing:border-box;margin:0;padding:0}
html{scroll-behavior:smooth}
body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;
     line-height:1.6;color:#1e293b;background:#f1f5f9;min-height:100vh}
a{text-decoration:none;color:inherit}
/* ── 顶栏 ── */
.topbar{background:linear-gradient(135deg,#1e3a5f 0%,#1a56db 60%,#7c3aed 100%);
        color:#fff;padding:0;position:sticky;top:0;z-index:100;
        box-shadow:0 2px 12px rgba(0,0,0,.25)}
.topbar-inner{max-width:1200px;margin:0 auto;padding:12px 20px;
              display:flex;align-items:center;gap:16px;flex-wrap:wrap}
.topbar h1{font-size:20px;font-weight:700;letter-spacing:-.3px;flex:1;min-width:180px}
.topbar h1 span{opacity:.7;font-weight:400;font-size:14px}
/* ── Tab 导航 ── */
.tabs{display:flex;gap:4px;background:rgba(255,255,255,.12);
      border-radius:24px;padding:3px}
.tab{padding:5px 18px;border-radius:20px;font-size:13px;font-weight:600;
     cursor:pointer;color:rgba(255,255,255,.75);transition:all .2s}
.tab.active,.tab:hover{background:#fff;color:#1e3a5f}
/* ── 主体 ── */
.main{max-width:1200px;margin:0 auto;padding:20px}
/* ── 统计栏 ── */
.stats{display:flex;gap:12px;flex-wrap:wrap;margin-bottom:20px}
.stat-card{background:#fff;border-radius:12px;padding:12px 20px;flex:1;min-width:140px;
           box-shadow:0 1px 6px rgba(0,0,0,.07);border-left:4px solid #1a56db}
.stat-card.green{border-color:#059669}.stat-card.purple{border-color:#7c3aed}
.stat-card.orange{border-color:#d97706}
.stat-val{font-size:28px;font-weight:700;color:#1e293b}
.stat-lbl{font-size:12px;color:#64748b;font-weight:500}
/* ── 卡片列表 ── */
.section-title{font-size:16px;font-weight:700;color:#334155;
               margin:24px 0 12px;display:flex;align-items:center;gap:8px}
.section-title .badge{background:#e0f2fe;color:#0369a1;font-size:11px;
                      padding:2px 10px;border-radius:10px;font-weight:600}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(340px,1fr));gap:16px}
/* ── 单张卡片 ── */
.card{background:#fff;border-radius:14px;overflow:hidden;
      box-shadow:0 2px 8px rgba(0,0,0,.07);transition:all .25s;
      border:1px solid #e2e8f0;display:flex;flex-direction:column}
.card:hover{transform:translateY(-3px);box-shadow:0 8px 24px rgba(0,0,0,.12);border-color:#a5b4fc}
.card-hdr{padding:14px 16px 10px;background:linear-gradient(135deg,#f8fafc,#eff6ff)}
.rank{display:inline-block;font-size:11px;font-weight:700;
      background:#dbeafe;color:#1d4ed8;padding:2px 9px;border-radius:10px;margin-bottom:6px}
.badge-new{background:#fef3c7;color:#92400e;font-size:10px;font-weight:700;
           padding:2px 7px;border-radius:8px;margin-left:6px}
.badge-pdf{background:#dcfce7;color:#166534;font-size:10px;font-weight:700;
           padding:2px 7px;border-radius:8px}
.card-title{font-size:14px;font-weight:700;color:#1e293b;line-height:1.4;margin-bottom:4px}
.card-title-zh{font-size:13px;color:#334155;font-weight:600;margin-bottom:6px}
.card-body{padding:10px 16px 14px;flex:1;display:flex;flex-direction:column;gap:8px}
.summary{font-size:12px;color:#64748b;line-height:1.65;
         display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden}
.meta-row{display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.meta-item{font-size:11px;color:#94a3b8;display:flex;align-items:center;gap:3px}
.kw{display:inline-block;background:#eef2ff;color:#4338ca;font-size:11px;
    padding:2px 9px;border-radius:10px;margin:2px 2px 0 0;font-weight:500}
.btns{display:flex;flex-wrap:wrap;gap:6px;margin-top:auto;padding-top:8px}
.btn{display:inline-flex;align-items:center;gap:4px;font-size:11px;font-weight:600;
     padding:5px 13px;border-radius:16px;transition:all .2s;white-space:nowrap;cursor:pointer}
.btn:hover{transform:translateY(-1px);box-shadow:0 3px 10px rgba(0,0,0,.15)}
.btn-detail{background:#4f46e5;color:#fff}
.btn-full-pdf{background:linear-gradient(135deg,#059669,#10b981);color:#fff}
.btn-arxiv{background:#b31b1b;color:#fff}
.btn-pdf{background:#dc2626;color:#fff}
.btn-back{background:#f1f5f9;color:#475569;border:1px solid #e2e8f0}
/* ── 索引列表（week/month/day 列表页）── */
.list-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px}
.list-card{background:#fff;border-radius:12px;padding:16px;
           box-shadow:0 1px 6px rgba(0,0,0,.07);border:1px solid #e2e8f0;
           transition:all .2s;display:flex;flex-direction:column;gap:10px}
.list-card:hover{transform:translateY(-2px);box-shadow:0 6px 20px rgba(0,0,0,.1);border-color:#a5b4fc}
.list-card-title{font-size:15px;font-weight:700;color:#1e293b}
.list-card-meta{font-size:12px;color:#64748b}
.list-card-btns{display:flex;gap:8px;flex-wrap:wrap}
/* ── 详情页 ── */
.detail-wrap{max-width:860px;margin:0 auto;padding:20px}
.detail-hdr{background:linear-gradient(135deg,#1e3a5f,#1a56db);color:#fff;
            border-radius:16px;padding:28px;margin-bottom:20px}
.detail-hdr h2{font-size:22px;font-weight:700;margin-bottom:8px;line-height:1.4}
.detail-hdr .zh{font-size:18px;opacity:.85;margin-bottom:14px}
.detail-hdr .meta{font-size:13px;opacity:.7}
.detail-sec{background:#fff;border-radius:14px;padding:22px;margin-bottom:14px;
            box-shadow:0 1px 6px rgba(0,0,0,.07)}
.detail-sec h3{font-size:14px;font-weight:700;color:#334155;margin-bottom:10px;
               padding-bottom:8px;border-bottom:2px solid #e2e8f0}
.detail-sec p{font-size:14px;color:#475569;line-height:1.75}
/* ── 空状态 ── */
.empty{text-align:center;padding:60px 20px;color:#94a3b8}
.empty-icon{font-size:48px;margin-bottom:12px}
/* ── 响应式 ── */
@media(max-width:640px){
  .cards{grid-template-columns:1fr}
  .stats{flex-direction:column}
  .tabs .tab{padding:4px 12px;font-size:12px}
}
/* ── 收藏按钮（卡片右上角）── */
.card-hdr{position:relative}
.btn-bm{position:absolute;top:10px;right:10px;background:transparent;border:none;
        cursor:pointer;font-size:20px;line-height:1;padding:2px 4px;
        border-radius:6px;transition:all .2s;color:#94a3b8;z-index:2}
.btn-bm:hover{background:#fef3c7;color:#f59e0b;transform:scale(1.2)}
.btn-bm.bm-active{color:#f59e0b}
/* ── 收藏模态框 ── */
#bm-overlay{display:none;position:fixed;inset:0;background:rgba(0,0,0,.55);
            z-index:9999;align-items:center;justify-content:center}
#bm-overlay.bm-show{display:flex}
#bm-box{background:#fff;border-radius:18px;padding:24px 24px 20px;width:360px;max-width:94vw;
        box-shadow:0 24px 64px rgba(0,0,0,.3);display:flex;flex-direction:column;gap:0;
        max-height:88vh;overflow:hidden}
.bm-hdr{margin-bottom:14px}
.bm-hdr-title{font-size:16px;font-weight:700;color:#1e293b}
.bm-hdr-sub{font-size:12px;color:#64748b;margin-top:2px;
            white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
#bm-list-items{flex:1;overflow-y:auto;display:flex;flex-direction:column;gap:6px;
               max-height:46vh;padding-right:2px;margin-bottom:12px}
.bm-item{display:flex;align-items:center;gap:10px;padding:10px 12px;
         border-radius:10px;border:1.5px solid #e2e8f0;cursor:pointer;
         transition:all .15s;user-select:none}
.bm-item:hover{border-color:#4f46e5;background:#f5f3ff}
.bm-item.bm-in{border-color:#059669;background:#f0fdf4}
.bm-check{width:18px;height:18px;border-radius:5px;border:2px solid #cbd5e1;flex-shrink:0;
          display:flex;align-items:center;justify-content:center;font-size:12px;font-weight:700}
.bm-item.bm-in .bm-check{background:#059669;border-color:#059669;color:#fff}
.bm-item-name{flex:1;font-size:13px;font-weight:600;color:#334155}
.bm-item-cnt{font-size:11px;color:#94a3b8}
/* move arrows in list view */
.bm-item-move{font-size:11px;color:#7c3aed;background:#f5f3ff;
              border:none;border-radius:6px;padding:3px 8px;cursor:pointer;
              font-weight:600;white-space:nowrap}
.bm-item-move:hover{background:#ede9fe}
#bm-new-row{display:none;margin-bottom:10px}
.bm-new-wrap{display:flex;gap:8px}
.bm-new-wrap input{flex:1;padding:8px 12px;border:1.5px solid #e2e8f0;
                   border-radius:8px;font-size:13px;outline:none}
.bm-new-wrap input:focus{border-color:#4f46e5}
.bm-footer{display:flex;gap:8px;flex-wrap:wrap;padding-top:4px;border-top:1px solid #f1f5f9}
.bm-btn{padding:7px 16px;border-radius:8px;border:none;cursor:pointer;
        font-size:12px;font-weight:600;transition:all .15s;white-space:nowrap}
.bm-btn-primary{background:#4f46e5;color:#fff}.bm-btn-primary:hover{background:#4338ca}
.bm-btn-ghost{background:#f1f5f9;color:#475569}.bm-btn-ghost:hover{background:#e2e8f0}
.bm-btn-danger{background:#fef2f2;color:#ef4444}.bm-btn-danger:hover{background:#fee2e2}
/* ── 收藏页 ── */
.bm-pg-toolbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;flex-wrap:wrap;gap:10px}
.bm-pg-title{font-size:22px;font-weight:700;color:#1e293b}
.bm-pg-grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(280px,1fr));gap:14px}
.bm-list-card{background:#fff;border-radius:14px;padding:20px;border:1px solid #e2e8f0;
              box-shadow:0 2px 8px rgba(0,0,0,.06);transition:all .2s;cursor:default}
.bm-list-card:hover{transform:translateY(-2px);box-shadow:0 6px 20px rgba(0,0,0,.1)}
.bm-list-card-name{font-size:16px;font-weight:700;color:#1e293b;margin-bottom:4px;
                   display:flex;align-items:center;gap:8px}
.bm-list-card-meta{font-size:12px;color:#64748b;margin-bottom:14px}
.bm-list-actions{display:flex;gap:6px;flex-wrap:wrap}
.bm-inline-btn{padding:5px 12px;border-radius:8px;border:none;cursor:pointer;
               font-size:11px;font-weight:600;transition:all .15s}
.bm-inline-view{background:#4f46e5;color:#fff}.bm-inline-view:hover{background:#4338ca}
.bm-inline-rename{background:#f1f5f9;color:#475569}.bm-inline-rename:hover{background:#e2e8f0}
.bm-inline-del{background:#fef2f2;color:#ef4444}.bm-inline-del:hover{background:#fee2e2}
/* ── 收藏列表视图的卡片操作 ── */
.bm-card-actions{display:flex;gap:6px;flex-wrap:wrap;margin-top:6px;padding-top:8px;
                 border-top:1px solid #f1f5f9}
.bm-rm-btn{background:#fef2f2;color:#ef4444;padding:4px 12px;border-radius:8px;
           border:none;cursor:pointer;font-size:11px;font-weight:600}
.bm-rm-btn:hover{background:#fee2e2}
.bm-mv-sel{padding:4px 8px;border-radius:8px;border:1px solid #e2e8f0;
           font-size:11px;color:#475569;cursor:pointer;background:#fff}
"""

# ── 收藏 JS ───────────────────────────────────────────────────────────────────
BM_JS = r"""
<script>
const BM = {
  data: {lists: {}},

  async init() {
    try {
      const r = await fetch((window.BP||'') + '/api/bookmarks');
      BM.data = await r.json();
    } catch(e) { BM.data = {lists: {}}; }
    BM.refreshButtons();
  },

  isIn(aid, lid) {
    const list = (BM.data.lists||{})[lid];
    return list ? list.papers.some(p => p.arxiv_id === aid) : false;
  },

  anyIn(aid) {
    return Object.keys(BM.data.lists||{}).some(lid => BM.isIn(aid, lid));
  },

  refreshButtons() {
    document.querySelectorAll('.btn-bm').forEach(btn => {
      const aid = btn.dataset.aid;
      const on  = BM.anyIn(aid);
      btn.textContent = on ? '★' : '☆';
      btn.classList.toggle('bm-active', on);
      btn.title = on ? '已收藏 — 点击管理' : '添加到收藏';
    });
  },

  /* ── 打开模态框 ── */
  open(aid, mode, key, titleZh) {
    BM._aid = aid; BM._mode = mode; BM._key = key;
    document.getElementById('bm-sub').textContent = titleZh || aid;
    BM.renderItems();
    document.getElementById('bm-overlay').classList.add('bm-show');
  },
  close() {
    document.getElementById('bm-overlay').classList.remove('bm-show');
    document.getElementById('bm-new-row').style.display = 'none';
    document.getElementById('bm-new-name').value = '';
  },

  renderItems() {
    const lists = BM.data.lists || {};
    const el = document.getElementById('bm-list-items');
    el.innerHTML = '';
    const lids = Object.keys(lists);
    if (!lids.length) {
      el.innerHTML = '<div style="color:#94a3b8;text-align:center;padding:20px;font-size:13px">暂无收藏列表<br>请点击「新建列表」创建</div>';
      return;
    }
    lids.forEach(lid => {
      const list = lists[lid];
      const inList = BM.isIn(BM._aid, lid);
      const div = document.createElement('div');
      div.className = 'bm-item' + (inList ? ' bm-in' : '');
      div.innerHTML = `<div class="bm-check">${inList ? '✓' : ''}</div>
        <span class="bm-item-name">${list.name}</span>
        <span class="bm-item-cnt">${list.papers.length} 篇</span>`;
      div.onclick = () => BM.toggle(lid);
      el.appendChild(div);
    });
  },

  /* ── 切换收藏状态 ── */
  async toggle(lid) {
    const r = await fetch((window.BP||'') + '/api/bookmarks', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({action:'toggle', list_id:lid,
                            arxiv_id:BM._aid, mode:BM._mode, key:BM._key})
    });
    BM.data = await r.json();
    BM.renderItems();
    BM.refreshButtons();
  },

  showNewInput() {
    document.getElementById('bm-new-row').style.display = 'block';
    document.getElementById('bm-new-name').focus();
  },

  async confirmCreate() {
    const name = document.getElementById('bm-new-name').value.trim();
    if (!name) return;
    const r = await fetch((window.BP||'') + '/api/bookmarks', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({action:'create_list', name,
                            arxiv_id:BM._aid, mode:BM._mode, key:BM._key})
    });
    BM.data = await r.json();
    BM.renderItems();
    BM.refreshButtons();
    document.getElementById('bm-new-row').style.display = 'none';
    document.getElementById('bm-new-name').value = '';
  },

  /* ── 收藏页操作（通过 data 属性在页面元素上触发）── */
  async deleteList(lid) {
    if (!confirm('确认删除收藏列表？其中的论文记录也会清除。')) return;
    const r = await fetch((window.BP||'') + '/api/bookmarks', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({action:'delete_list', list_id:lid})
    });
    BM.data = await r.json();
    location.reload();
  },

  async renameList(lid, oldName) {
    const name = prompt('新列表名称：', oldName);
    if (!name || name === oldName) return;
    const r = await fetch((window.BP||'') + '/api/bookmarks', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({action:'rename_list', list_id:lid, name})
    });
    BM.data = await r.json();
    location.reload();
  },

  async removePaper(arxivId, lid) {
    const r = await fetch((window.BP||'') + '/api/bookmarks', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({action:'remove', arxiv_id:arxivId, list_id:lid})
    });
    BM.data = await r.json();
    document.getElementById('bm-paper-' + arxivId)?.remove();
  },

  async movePaper(arxivId, fromLid, toLid) {
    if (!toLid || toLid === fromLid) return;
    const r = await fetch((window.BP||'') + '/api/bookmarks', {
      method: 'POST', headers: {'Content-Type':'application/json'},
      body: JSON.stringify({action:'move', arxiv_id:arxivId,
                            from_list:fromLid, to_list:toLid})
    });
    BM.data = await r.json();
    document.getElementById('bm-paper-' + arxivId)?.remove();
  }
};

document.addEventListener('DOMContentLoaded', () => BM.init());
document.getElementById('bm-overlay').addEventListener('click', function(e){
  if (e.target === this) BM.close();
});
document.getElementById('bm-new-name').addEventListener('keydown', e => {
  if (e.key === 'Enter') BM.confirmCreate();
});
</script>"""

BM_MODAL = """
<div id="bm-overlay">
  <div id="bm-box">
    <div class="bm-hdr">
      <div class="bm-hdr-title">⭐ 添加到收藏</div>
      <div class="bm-hdr-sub" id="bm-sub"></div>
    </div>
    <div id="bm-list-items"></div>
    <div id="bm-new-row">
      <div class="bm-new-wrap">
        <input id="bm-new-name" placeholder="新列表名称…">
        <button class="bm-btn bm-btn-primary" onclick="BM.confirmCreate()">创建</button>
      </div>
    </div>
    <div class="bm-footer">
      <button class="bm-btn bm-btn-ghost" onclick="BM.showNewInput()">＋ 新建列表</button>
      <button class="bm-btn bm-btn-ghost" onclick="BM.close()">关闭</button>
    </div>
  </div>
</div>"""


# ── HTML 工具 ─────────────────────────────────────────────────────────────────
def page(title, body, active_tab="home"):
    tab_items = [
        ("home",      "📅 每日",   "/daily"),
        ("weekly",    "📚 每周",   "/weekly"),
        ("monthly",   "📆 每月",   "/monthly"),
        ("bookmarks", "⭐ 收藏",   "/bookmarks"),
        ("submit",    "➕ 手动",   "/submit"),
    ]
    tabs_html = "".join(
        f'<a class="tab{" active" if t==active_tab else ""}" href="{href}">{label}</a>'
        for t, label, href in tab_items
    )
    html = f"""<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — Paper Hub</title>
<style>{CSS}</style>
<script>window.BP="{BASE_PATH}";</script>
</head><body>
<div class="topbar">
  <div class="topbar-inner">
    <h1><a href="/" style="color:inherit;text-decoration:none">📰 Paper Hub</a> <span>HF Papers 中文精选</span></h1>
    <div class="tabs">{tabs_html}</div>
  </div>
</div>
<div class="main">{body}</div>
{BM_MODAL}
{BM_JS}
</body></html>"""
    # 统一把所有内部绝对路径加上 BASE_PATH 前缀（仅当设置了前缀时）
    if BASE_PATH:
        html = html.replace('href="/', f'href="{BASE_PATH}/')
        html = html.replace("href='/", f"href='{BASE_PATH}/")
        html = html.replace('action="/', f'action="{BASE_PATH}/')
    return html

def paper_card(p, mode, key, pdir):
    aid        = p.get("arxiv_id","")
    rank       = p.get("rank",0)
    title      = p.get("title","") or aid
    title_zh   = p.get("title_zh","")
    summary_zh = p.get("summary_zh","")
    authors    = p.get("authors","")
    submitted  = p.get("submitted","")
    upvotes    = p.get("upvotes",0)
    kws        = p.get("keywords_zh",[]) or []
    html_file  = p.get("html_file","")
    pdf_zh     = p.get("pdf_zh","")

    has_pdf  = bool(pdf_zh and pdir and
                    os.path.exists(os.path.join(pdir, pdf_zh.replace("papers/","",1))))
    has_html = bool(html_file and pdir and
                    os.path.exists(os.path.join(pdir, html_file.replace("papers/","",1))))

    rank_badge  = f'<span class="rank">#{rank}</span>' if rank else ""
    pdf_badge   = '<span class="badge-pdf">✅ PDF</span>' if has_pdf else ""
    up_badge    = f'<span class="badge-new">▲ {upvotes}</span>' if upvotes else ""

    kw_html = "".join(f'<span class="kw">{k}</span>' for k in kws[:4])

    meta_parts = []
    if submitted:
        meta_parts.append(f'<span class="meta-item">📅 {submitted}</span>')
    if authors:
        short_au = authors[:50] + ("…" if len(authors) > 50 else "")
        meta_parts.append(f'<span class="meta-item">👥 {short_au}</span>')

    # 收藏按钮（JS 初始化后自动更新 ☆/★）
    title_esc = (title_zh or title).replace("'", "\\'")[:60]
    bm_btn = (f'<button class="btn-bm" data-aid="{aid}" '
              f'onclick="BM.open(\'{aid}\',\'{mode}\',\'{key}\',\'{title_esc}\')"'
              f' title="收藏">☆</button>')

    # buttons
    btns = []
    if has_html:
        btns.append(f'<a class="btn btn-detail" href="/{mode}/{key}/papers/{aid}">🔍 详情</a>')
    if has_pdf:
        pdf_url = f"/{mode}/{key}/{pdf_zh}"
        btns.append(f'<a class="btn btn-full-pdf" href="{pdf_url}" target="_blank">📄 全文PDF</a>')
    btns.append(f'<a class="btn btn-arxiv" href="https://arxiv.org/abs/{aid}" target="_blank">arXiv</a>')
    btns.append(f'<a class="btn btn-pdf" href="https://arxiv.org/pdf/{aid}" target="_blank">原文PDF</a>')

    return f"""<div class="card">
  <div class="card-hdr">
    {bm_btn}
    <div>{rank_badge}{pdf_badge}{up_badge}</div>
    <div class="card-title">{title[:120]}</div>
    {"<div class='card-title-zh'>" + title_zh[:100] + "</div>" if title_zh else ""}
  </div>
  <div class="card-body">
    {"<p class='summary'>" + summary_zh[:300] + "</p>" if summary_zh else ""}
    {"<div>" + kw_html + "</div>" if kw_html else ""}
    <div class="meta-row">{"".join(meta_parts)}</div>
    <div class="btns">{"".join(btns)}</div>
  </div>
</div>"""


# ── 页面构建 ──────────────────────────────────────────────────────────────────
def build_list_page(mode):
    """某 mode 的索引列表页（如所有 daily 条目）"""
    keys = list_keys(mode)
    label_map = {"daily":"每日","weekly":"每周","monthly":"每月"}
    emoji_map  = {"daily":"📅","weekly":"📚","monthly":"📆"}
    label = label_map.get(mode, mode)
    emoji = emoji_map.get(mode, "")

    total_papers = 0
    cards = []
    for k in keys:
        idx = load_index(mode, k)
        n   = len(idx.get("papers",[])) if idx else 0
        total_papers += n
        pdfs = count_pdfs(mode, k, idx)
        gen  = (idx or {}).get("generated_at","")
        cards.append(f"""<div class="list-card">
  <div class="list-card-title">{emoji} {k}</div>
  <div class="list-card-meta">📄 {n} 篇{"　✅ " + str(pdfs) + " 个PDF" if pdfs else ""}{"　🕐 " + gen[:16] if gen else ""}</div>
  <div class="list-card-btns">
    <a class="btn btn-detail" href="/{mode}/{k}">查看</a>
  </div>
</div>""")

    stats = f"""<div class="stats">
  <div class="stat-card"><div class="stat-val">{len(keys)}</div><div class="stat-lbl">已抓取期数</div></div>
  <div class="stat-card green"><div class="stat-val">{total_papers}</div><div class="stat-lbl">总论文数</div></div>
</div>"""

    grid = '<div class="list-grid">' + ("".join(cards) if cards else '<div class="empty"><div class="empty-icon">📭</div><p>暂无数据</p></div>') + "</div>"
    body = f'<div class="section-title">{emoji} {label}论文列表</div>{stats}{grid}'
    return page(f"{label}论文", body, active_tab=mode)


def build_papers_page(mode, key):
    """某期具体的论文列表页"""
    idx   = load_index(mode, key)
    pdir  = papers_dir(mode, key)
    label_map = {"daily":"每日 Top 3","weekly":"每周 Top 10","monthly":"每月 Top 10"}
    label = label_map.get(mode, key)
    emoji_map  = {"daily":"📅","weekly":"📚","monthly":"📆"}
    emoji = emoji_map.get(mode,"")

    if not idx:
        body = f'<div class="empty"><div class="empty-icon">📭</div><p>暂无数据 {key}</p></div>'
        return page(key, body, active_tab=mode)

    papers  = idx.get("papers",[])
    n_pdfs  = count_pdfs(mode, key, idx)
    gen_at  = idx.get("generated_at","")

    stats = f"""<div class="stats">
  <div class="stat-card"><div class="stat-val">{len(papers)}</div><div class="stat-lbl">论文总数</div></div>
  <div class="stat-card green"><div class="stat-val">{n_pdfs}</div><div class="stat-lbl">全文 PDF</div></div>
  <div class="stat-card purple"><div class="stat-val">{gen_at[:10] if gen_at else "—"}</div><div class="stat-lbl">更新日期</div></div>
</div>"""

    cards = "".join(paper_card(p, mode, key, pdir) for p in papers)
    back_link = {"daily":"/","weekly":"/weekly","monthly":"/monthly"}.get(mode,"/")
    body = (f'<div style="margin-bottom:12px">'
            f'<a class="btn btn-back" href="{back_link}">← 返回列表</a></div>'
            f'<div class="section-title">{emoji} {key} &nbsp;<span class="badge">{label}</span></div>'
            f'{stats}'
            f'<div class="cards">{cards if cards else "<div class=empty><div class=empty-icon>📭</div><p>暂无数据</p></div>"}</div>')
    return page(key, body, active_tab=mode)


def build_home():
    """首页：汇总最新一期 daily / weekly / monthly"""
    sections = []

    # 最新 daily
    daily_keys = list_keys("daily")
    if daily_keys:
        k   = daily_keys[0]
        idx = load_index("daily", k)
        pd  = papers_dir("daily", k)
        n   = len((idx or {}).get("papers",[]))
        papers_html = "".join(paper_card(p,"daily",k,pd)
                              for p in (idx or {}).get("papers",[]))
        sections.append(
            f'<div class="section-title">📅 每日精选 <span class="badge">{k} · Top 3</span>'
            f'&nbsp;<a href="/daily/{k}" style="font-size:12px;color:#4f46e5">查看全部 →</a></div>'
            f'<div class="cards">{papers_html or "<div class=empty>暂无数据</div>"}</div>'
        )

    # 最新 weekly
    weekly_keys = list_keys("weekly")
    if weekly_keys:
        k   = weekly_keys[0]
        idx = load_index("weekly", k)
        pd  = papers_dir("weekly", k)
        papers_html = "".join(paper_card(p,"weekly",k,pd)
                              for p in (idx or {}).get("papers",[])[:5])
        sections.append(
            f'<div class="section-title">📚 本周热榜 <span class="badge">{k} · Top 10</span>'
            f'&nbsp;<a href="/weekly/{k}" style="font-size:12px;color:#4f46e5">查看全部 →</a></div>'
            f'<div class="cards">{papers_html or "<div class=empty>暂无数据</div>"}</div>'
        )

    # 最新 monthly
    monthly_keys = list_keys("monthly")
    if monthly_keys:
        k   = monthly_keys[0]
        idx = load_index("monthly", k)
        pd  = papers_dir("monthly", k)
        papers_html = "".join(paper_card(p,"monthly",k,pd)
                              for p in (idx or {}).get("papers",[])[:3])
        sections.append(
            f'<div class="section-title">📆 本月热榜 <span class="badge">{k} · Top 10</span>'
            f'&nbsp;<a href="/monthly/{k}" style="font-size:12px;color:#4f46e5">查看全部 →</a></div>'
            f'<div class="cards">{papers_html or "<div class=empty>暂无数据</div>"}</div>'
        )

    if not sections:
        sections = ['<div class="empty"><div class="empty-icon">🚀</div>'
                    '<p>还没有数据，运行 run_daily.py / main.py 开始吧！</p></div>']

    # 全局统计
    d_cnt = len(daily_keys)
    w_cnt = len(list_keys("weekly"))
    m_cnt = len(list_keys("monthly"))
    stats = f"""<div class="stats">
  <div class="stat-card"><div class="stat-val">{d_cnt}</div><div class="stat-lbl">已抓取天数</div></div>
  <div class="stat-card green"><div class="stat-val">{w_cnt}</div><div class="stat-lbl">已抓取周数</div></div>
  <div class="stat-card purple"><div class="stat-val">{m_cnt}</div><div class="stat-lbl">已抓取月数</div></div>
</div>"""

    body = stats + "".join(sections)
    return page("首页", body, active_tab="daily")


def build_detail_page(mode, key, arxiv_id):
    """单篇论文详情页"""
    idx  = load_index(mode, key)
    pdir = papers_dir(mode, key)
    entry = None
    if idx:
        for p in idx.get("papers",[]):
            if p.get("arxiv_id") == arxiv_id:
                entry = p
                break

    if not entry:
        return None  # 尝试从 HTML 文件读

    title     = entry.get("title","") or arxiv_id
    title_zh  = entry.get("title_zh","")
    abs_en    = entry.get("abstract","")
    abs_zh    = entry.get("summary_zh","")
    authors   = entry.get("authors","")
    submitted = entry.get("submitted","")
    kws       = entry.get("keywords_zh",[]) or []
    pdf_zh    = entry.get("pdf_zh","")
    has_pdf   = bool(pdf_zh and pdir and
                     os.path.exists(os.path.join(pdir, pdf_zh.replace("papers/","",1))))

    kw_html = "".join(f'<span class="kw">{k}</span>' for k in kws)
    btns = [f'<a class="btn btn-arxiv" href="https://arxiv.org/abs/{arxiv_id}" target="_blank">arXiv 原文</a>',
            f'<a class="btn btn-pdf" href="https://arxiv.org/pdf/{arxiv_id}" target="_blank">原文 PDF</a>']
    if has_pdf:
        btns.insert(0, f'<a class="btn btn-full-pdf" href="/{mode}/{key}/{pdf_zh}" target="_blank">📄 全文中文 PDF</a>')
    back = {"daily":f"/daily/{key}","weekly":f"/weekly/{key}","monthly":f"/monthly/{key}"}.get(mode,"/")

    body = f"""<div class="detail-wrap">
<a class="btn btn-back" href="{back}">← 返回</a>
<div style="height:14px"></div>
<div class="detail-hdr">
  <h2>{title}</h2>
  {"<div class='zh'>" + title_zh + "</div>" if title_zh else ""}
  <div class="meta">{"👥 " + authors[:80] + " &nbsp;&nbsp;" if authors else ""}{"📅 " + submitted if submitted else ""}</div>
</div>
{"<div class='detail-sec'><h3>关键词</h3><div>" + kw_html + "</div></div>" if kw_html else ""}
{"<div class='detail-sec'><h3>中文摘要</h3><p>" + abs_zh + "</p></div>" if abs_zh else ""}
{"<div class='detail-sec'><h3>English Abstract</h3><p>" + abs_en + "</p></div>" if abs_en else ""}
<div class="detail-sec"><h3>链接</h3><div class="btns">{"".join(btns)}</div></div>
</div>"""
    return page(title_zh or title, body, active_tab=mode)


# ── 收藏页面 ──────────────────────────────────────────────────────────────────
def build_bookmarks_overview():
    """所有收藏列表的概览页"""
    bm   = load_bookmarks()
    lists = bm.get("lists", {})

    if not lists:
        cards_html = """<div class="empty">
  <div class="empty-icon">⭐</div>
  <p>还没有收藏列表</p>
  <p style="font-size:13px;margin-top:8px;color:#94a3b8">
    点击任意论文卡片右上角的 ☆ 按钮即可收藏
  </p>
</div>"""
    else:
        cards = []
        for lid, lst in lists.items():
            cnt   = len(lst.get("papers", []))
            cdate = lst.get("created", "")[:10]
            cards.append(f"""<div class="bm-list-card">
  <div class="bm-list-card-name">📚 {lst['name']}</div>
  <div class="bm-list-card-meta">{cnt} 篇论文{f' · 创建于 {cdate}' if cdate else ''}</div>
  <div class="bm-list-actions">
    <a class="bm-inline-btn bm-inline-view" href="/bookmarks/{lid}">查看</a>
    <button class="bm-inline-btn bm-inline-rename"
            onclick="BM.renameList('{lid}','{lst['name'].replace(chr(39),chr(39))}')">重命名</button>
    <button class="bm-inline-btn bm-inline-del"
            onclick="BM.deleteList('{lid}')">删除</button>
  </div>
</div>""")
        cards_html = f'<div class="bm-pg-grid">{"".join(cards)}</div>'

    body = f"""<div class="bm-pg-toolbar">
  <div class="bm-pg-title">⭐ 我的收藏</div>
  <button class="bm-btn bm-btn-primary"
          onclick="BM._aid='';BM._mode='';BM._key='';
                   document.getElementById('bm-sub').textContent='新建收藏列表';
                   BM.renderItems();
                   BM.showNewInput();
                   document.getElementById('bm-overlay').classList.add('bm-show')">
    ＋ 新建列表
  </button>
</div>
{cards_html}"""
    return page("我的收藏", body, active_tab="bookmarks")


def build_bookmark_list_page(lid):
    """某个收藏列表的论文详情页"""
    bm    = load_bookmarks()
    lists = bm.get("lists", {})

    if lid not in lists:
        return None

    lst     = lists[lid]
    papers  = lst.get("papers", [])
    other_lists = {k: v["name"] for k, v in lists.items() if k != lid}

    if not papers:
        cards_html = '<div class="empty"><div class="empty-icon">📭</div><p>这个列表还没有论文</p></div>'
    else:
        cards = []
        for entry in papers:
            aid   = entry.get("arxiv_id", "")
            mode  = entry.get("mode", "")
            key   = entry.get("key", "")
            added = entry.get("added", "")[:10]
            # 从 index.json 拉取完整元数据
            p    = get_paper_entry(mode, key, aid)
            pdir = papers_dir(mode, key) if mode and key else None

            # 构造卡片 HTML（复用 paper_card 的大部分逻辑）
            title    = p.get("title","") or aid
            title_zh = p.get("title_zh","")
            sum_zh   = p.get("summary_zh","")
            kws      = p.get("keywords_zh",[]) or []
            has_pdf  = bool(p.get("pdf_zh") and pdir and
                            os.path.exists(os.path.join(pdir,
                                p["pdf_zh"].replace("papers/","",1))))

            kw_html = "".join(f'<span class="kw">{k}</span>' for k in kws[:4])
            pdf_btn = (f'<a class="btn btn-full-pdf" href="/{mode}/{key}/{p["pdf_zh"]}" target="_blank">📄 全文PDF</a>'
                       if has_pdf else "")

            # 移动到其他列表的 <select>
            mv_opts = "".join(f'<option value="{k}">{v}</option>' for k, v in other_lists.items())
            mv_sel  = (f'<select class="bm-mv-sel" onchange="BM.movePaper(\'{aid}\',\'{lid}\',this.value)">'
                       f'<option value="">移动到…</option>{mv_opts}</select>'
                       if mv_opts else "")

            cards.append(f"""<div class="card" id="bm-paper-{aid}">
  <div class="card-hdr">
    <div><span class="badge-pdf" style="background:#fef3c7;color:#92400e">⭐ {added}</span>
         {"<span class='badge-pdf'>✅ PDF</span>" if has_pdf else ""}
    </div>
    <div class="card-title">{title[:120]}</div>
    {"<div class='card-title-zh'>" + title_zh[:100] + "</div>" if title_zh else ""}
  </div>
  <div class="card-body">
    {"<p class='summary'>" + sum_zh[:300] + "</p>" if sum_zh else ""}
    {"<div>" + kw_html + "</div>" if kw_html else ""}
    <div class="btns">
      {f'<a class="btn btn-detail" href="/{mode}/{key}/papers/{aid}">🔍 详情</a>' if mode and key else ""}
      {pdf_btn}
      <a class="btn btn-arxiv" href="https://arxiv.org/abs/{aid}" target="_blank">arXiv</a>
    </div>
    <div class="bm-card-actions">
      <button class="bm-rm-btn" onclick="BM.removePaper('{aid}','{lid}')">✕ 移出列表</button>
      {mv_sel}
    </div>
  </div>
</div>""")
        cards_html = f'<div class="cards">{"".join(cards)}</div>'

    cnt  = len(papers)
    body = f"""<div class="bm-pg-toolbar">
  <div>
    <a class="btn btn-back" href="/bookmarks">← 所有列表</a>
  </div>
  <div class="bm-pg-title">📚 {lst['name']}
    <span style="font-size:14px;color:#64748b;font-weight:400">（{cnt} 篇）</span>
  </div>
  <div style="display:flex;gap:8px">
    <button class="bm-btn bm-btn-ghost"
            onclick="BM.renameList('{lid}','{lst['name']}')">重命名</button>
    <button class="bm-btn bm-btn-danger"
            onclick="BM.deleteList('{lid}')">删除列表</button>
  </div>
</div>
{cards_html}"""
    return page(lst["name"], body, active_tab="bookmarks")


# ── HTTP Handler ──────────────────────────────────────────────────────────────
class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # 静默

    def send_html(self, html, code=200):
        b = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def send_file(self, path):
        ext = os.path.splitext(path)[1].lower()
        ct_map = {".pdf":"application/pdf",".html":"text/html; charset=utf-8",
                  ".json":"application/json",".css":"text/css",".js":"application/javascript"}
        ct = ct_map.get(ext, "application/octet-stream")
        with open(path, "rb") as f:
            data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ct)
        self.send_header("Content-Length", str(len(data)))
        if ext == ".pdf":
            self.send_header("Content-Disposition", "inline")
        self.end_headers()
        self.wfile.write(data)

    def send_404(self, msg="页面未找到"):
        html = f"<html><body style='font-family:sans-serif;padding:40px'><h2>404 — {msg}</h2><a href='/'>← 返回首页</a></body></html>"
        self.send_html(html, 404)

    def send_json(self, data, code=200):
        b = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_POST(self):
        raw = unquote(self.path).split("?")[0]

        # ── /api/submit  手动提交 arxiv_id ─────────────────
        if raw == "/api/submit":
            length = int(self.headers.get("Content-Length", 0))
            try:
                req = json.loads(self.rfile.read(length).decode("utf-8"))
            except Exception:
                self.send_json({"error": "bad json"}, 400); return
            arxiv_id = req.get("arxiv_id", "").strip()
            arxiv_id = re.sub(r'\s+', '', arxiv_id).split("v")[0]
            if not re.match(r'^\d{4}\.\d{4,5}$', arxiv_id):
                self.send_json({"error": "无效的 arXiv ID，格式示例：2602.12345"}, 400)
                return
            ok, msg = enqueue_submit(arxiv_id)
            self.send_json({"ok": ok, "msg": msg, "arxiv_id": arxiv_id})
            return

        if raw != "/api/bookmarks":
            self.send_json({"error": "not found"}, 404)
            return
        length = int(self.headers.get("Content-Length", 0))
        body   = self.rfile.read(length)
        try:
            req = json.loads(body.decode("utf-8"))
        except Exception:
            self.send_json({"error": "bad json"}, 400)
            return

        action = req.get("action", "")
        with _bm_lock:
            bm = load_bookmarks()
            lists = bm.setdefault("lists", {})

            if action == "toggle":
                lid     = req.get("list_id", "")
                aid     = req.get("arxiv_id", "")
                mode    = req.get("mode", "")
                key     = req.get("key", "")
                if lid not in lists:
                    self.send_json({"error": "list not found"}, 404); return
                papers = lists[lid].setdefault("papers", [])
                idx    = next((i for i, p in enumerate(papers) if p["arxiv_id"] == aid), -1)
                if idx >= 0:
                    papers.pop(idx)          # 已有 → 移除
                else:
                    papers.append({"arxiv_id": aid, "mode": mode, "key": key,
                                   "added": datetime.now().strftime("%Y-%m-%d")})

            elif action == "create_list":
                name = req.get("name", "").strip()
                if not name:
                    self.send_json({"error": "name required"}, 400); return
                lid = re.sub(r'[^a-z0-9_-]', '', name.lower().replace(" ", "_")) or \
                      f"list_{len(lists)}"
                if lid in lists:
                    lid = lid + f"_{len(lists)}"
                lists[lid] = {
                    "name": name,
                    "created": datetime.now().strftime("%Y-%m-%d"),
                    "papers": []
                }
                # 创建列表后，若同时传了论文 ID，立刻收藏
                aid = req.get("arxiv_id", "")
                if aid:
                    lists[lid]["papers"].append({
                        "arxiv_id": aid,
                        "mode": req.get("mode",""), "key": req.get("key",""),
                        "added": datetime.now().strftime("%Y-%m-%d")
                    })

            elif action == "delete_list":
                lid = req.get("list_id", "")
                lists.pop(lid, None)

            elif action == "rename_list":
                lid  = req.get("list_id", "")
                name = req.get("name", "").strip()
                if lid in lists and name:
                    lists[lid]["name"] = name

            elif action == "remove":
                lid = req.get("list_id", "")
                aid = req.get("arxiv_id", "")
                if lid in lists:
                    lists[lid]["papers"] = [
                        p for p in lists[lid].get("papers", [])
                        if p["arxiv_id"] != aid
                    ]

            elif action == "move":
                from_lid = req.get("from_list", "")
                to_lid   = req.get("to_list", "")
                aid      = req.get("arxiv_id", "")
                if from_lid in lists and to_lid in lists:
                    entry = next((p for p in lists[from_lid].get("papers",[])
                                  if p["arxiv_id"] == aid), None)
                    if entry:
                        lists[from_lid]["papers"] = [
                            p for p in lists[from_lid]["papers"] if p["arxiv_id"] != aid
                        ]
                        if not any(p["arxiv_id"] == aid
                                   for p in lists[to_lid].get("papers",[])):
                            lists[to_lid].setdefault("papers", []).append(entry)
            else:
                self.send_json({"error": "unknown action"}, 400); return

            save_bookmarks(bm)
        self.send_json(bm)

    def do_GET(self):
        raw  = unquote(self.path).split("?")[0]
        parts = [p for p in raw.strip("/").split("/") if p]

        # ── /api/submit/status  任务状态 ───────────────────
        if raw == "/api/submit/status":
            with _submit_lock:
                jobs = _load_jobs()
            return self.send_json(jobs)

        # ── /api/submit  提交（POST only，此处仅防误访问）──
        if raw == "/api/submit":
            return self.send_json({"error": "POST only"}, 405)

        # ── /submit  手动提交页面 ─────────────────────────
        if raw == "/submit":
            return self.send_html(build_submit_page())

        # ── /api/bookmarks  JSON 接口 ─────────────────────
        if raw == "/api/bookmarks":
            return self.send_json(load_bookmarks())

        # ── /bookmarks  /bookmarks/{id}  收藏页 ──────────
        if parts and parts[0] == "bookmarks":
            if len(parts) == 1:
                return self.send_html(build_bookmarks_overview())
            if len(parts) == 2:
                html = build_bookmark_list_page(parts[1])
                if html:
                    return self.send_html(html)
                return self.send_404("收藏列表不存在")

        # ── /  首页 ──────────────────────────────────────
        if not parts:
            return self.send_html(build_home())

        # ── /daily  /weekly  /monthly  列表页 ────────────
        if len(parts) == 1 and parts[0] in ("daily","weekly","monthly"):
            return self.send_html(build_list_page(parts[0]))

        # ── /daily/KEY  /weekly/KEY  /monthly/KEY  期论文列表 ────
        if len(parts) == 2 and parts[0] in ("daily","weekly","monthly"):
            mode, key = parts
            return self.send_html(build_papers_page(mode, key))

        # ── /MODE/KEY/papers/NAME  详情页 或 文件下载 ────────
        if len(parts) == 4 and parts[0] in ("daily","weekly","monthly") and parts[2] == "papers":
            mode, key, _, name = parts
            # arXiv ID 格式：YYMM.NNNNN（纯数字 + 一个点）
            # PDF/HTML 文件名含 "_zh" 或 ".html" 等后缀，不匹配此模式
            if re.match(r'^\d{4}\.\d+$', name):
                html = build_detail_page(mode, key, name)
                if html:
                    return self.send_html(html)
                # index 中未找到则回退到 HTML 文件
                name = name + ".html"
            # 以文件形式伺服（PDF / HTML / 其他）
            for base in [os.path.join(DATA_DIR, mode, key, "papers"),
                         os.path.join(WEEKLY_DIR, key, "papers")]:
                fp = os.path.join(base, name)
                if os.path.exists(fp):
                    return self.send_file(fp)
            return self.send_404(f"{name} 未找到")

        # ── /MODE/KEY/...  其他文件（兼容 5 段以上路径）────────
        if len(parts) >= 4 and parts[0] in ("daily","weekly","monthly"):
            mode = parts[0]; key = parts[1]
            rel  = "/".join(parts[2:])
            for base in [os.path.join(DATA_DIR, mode, key),
                         os.path.join(WEEKLY_DIR, key)]:
                fp = os.path.join(base, rel)
                if os.path.exists(fp) and os.path.isfile(fp):
                    return self.send_file(fp)
            return self.send_404(rel)

        # ── 兼容旧 /2026-W08/... 路径 ─────────────────────
        if parts[0].startswith("20") and ("W" in parts[0] or "-" in parts[0]):
            key = parts[0]
            if len(parts) == 1:
                return self.send_html(build_papers_page("weekly", key))
            # 静态文件
            for base in [os.path.join(DATA_DIR, "weekly", key),
                         os.path.join(WEEKLY_DIR, key)]:
                fp = os.path.join(base, *parts[1:])
                if os.path.exists(fp) and os.path.isfile(fp):
                    return self.send_file(fp)

        self.send_404(raw)


def build_submit_page():
    STATUS_LABEL = {
        "queued":      ("⏳", "#94a3b8", "排队中"),
        "fetching":    ("🔍", "#60a5fa", "获取元数据"),
        "abstract":    ("✍️",  "#a78bfa", "翻译摘要"),
        "full_pdf":    ("🔬", "#f59e0b", "翻译全文 PDF"),
        "done":        ("✅", "#22c55e", "完成"),
        "done_no_pdf": ("⚠️",  "#f97316", "完成（无 PDF）"),
        "error":       ("❌", "#ef4444", "失败"),
    }
    jobs = _load_jobs()
    job_list = sorted(jobs.values(),
                      key=lambda j: j.get("submitted_at", ""), reverse=True)

    has_active = any(j.get("status") in ("queued","fetching","abstract","full_pdf")
                     for j in job_list)
    auto_refresh = '<meta http-equiv="refresh" content="8">' if has_active else ""

    # ── 进行中任务状态条 ────────────────────────────────────────
    active_rows = ""
    for j in job_list:
        status = j.get("status", "queued")
        if status not in ("queued","fetching","abstract","full_pdf","error"):
            continue
        aid   = j.get("arxiv_id", "")
        icon, color, label = STATUS_LABEL.get(status, ("?", "#94a3b8", status))
        title = j.get("title") or aid
        msg   = j.get("msg", "")
        spin  = ' <span class="spin">↻</span>' if status not in ("error",) else ""
        retry = ""
        if status == "error":
            retry = (f'<button onclick="submitId(\'{aid}\')" '
                     f'style="margin-left:8px;padding:2px 8px;font-size:12px;'
                     f'background:#334155;color:#e2e8f0;border:none;'
                     f'border-radius:4px;cursor:pointer">重试</button>')
        active_rows += (
            f'<div style="display:flex;align-items:center;gap:10px;'
            f'padding:10px 0;border-bottom:1px solid #1e293b">'
            f'<span style="color:{color};white-space:nowrap">{icon} {label}{spin}</span>'
            f'<span style="font-family:monospace;color:#93c5fd;white-space:nowrap">{aid}</span>'
            f'<span style="color:#94a3b8;font-size:13px;overflow:hidden;'
            f'text-overflow:ellipsis;white-space:nowrap">{title}</span>'
            f'<span style="color:#64748b;font-size:12px;white-space:nowrap;margin-left:auto">{msg}</span>'
            f'{retry}</div>'
        )
    active_section = ""
    if active_rows:
        active_section = (
            f'<div style="background:#1e293b;border-radius:10px;'
            f'padding:16px 20px;margin-bottom:24px">'
            f'<div style="font-size:13px;font-weight:600;color:#94a3b8;margin-bottom:4px">'
            f'进行中任务</div>{active_rows}</div>'
        )

    # ── 已完成的论文：复用 paper_card ──────────────────────────
    done_cards = ""
    done_modes = {}   # key -> papers_dir
    for j in job_list:
        status = j.get("status", "")
        if status not in ("done", "done_no_pdf"):
            continue
        aid     = j.get("arxiv_id", "")
        key_val = j.get("key", "")
        if not key_val:
            continue
        pdir = os.path.join(MANUAL_DIR, key_val, "papers")
        # 从 index.json 里取完整 paper entry
        idx_file = os.path.join(MANUAL_DIR, key_val, "index.json")
        paper_entry = {"arxiv_id": aid}
        try:
            with open(idx_file, encoding="utf-8") as f:
                idx_data = json.load(f)
            for p in idx_data.get("papers", []):
                if p.get("arxiv_id") == aid:
                    paper_entry = p
                    break
        except Exception:
            pass
        done_cards += paper_card(paper_entry, "manual", key_val, pdir)

    if done_cards:
        done_section = (
            f'<h3 style="color:#e2e8f0;margin:0 0 16px">已翻译论文</h3>'
            f'<div class="grid">{done_cards}</div>'
        )
    elif not active_rows:
        done_section = '<p style="color:#64748b;margin-top:8px">暂无提交记录</p>'
    else:
        done_section = ""

    body = f"""{auto_refresh}
<div style="max-width:900px;margin:0 auto;padding:20px 0">
  <h2 style="color:#e2e8f0;margin-bottom:16px">➕ 手动添加论文</h2>
  <div style="background:#1e293b;border-radius:12px;padding:20px 24px;margin-bottom:20px">
    <p style="color:#94a3b8;margin:0 0 12px;font-size:14px">
      输入 arXiv ID（如 <code style="color:#93c5fd">2602.12345</code>），
      系统自动翻译摘要 + 全文 PDF。</p>
    <div style="display:flex;gap:10px;align-items:center">
      <input id="aid-input" type="text" placeholder="2602.12345"
        style="flex:1;padding:10px 14px;border-radius:8px;border:1px solid #334155;
               background:#0f172a;color:#e2e8f0;font-size:15px;outline:none"
        onkeydown="if(event.key==='Enter')submitForm()">
      <button onclick="submitForm()"
        style="padding:10px 22px;border-radius:8px;border:none;
               background:#4f46e5;color:#fff;font-size:15px;cursor:pointer;font-weight:600">
        提交
      </button>
    </div>
    <div id="submit-msg" style="margin-top:10px;font-size:13px;color:#94a3b8"></div>
  </div>
  {active_section}
  {done_section}
</div>
<style>
  .spin{{display:inline-block;animation:spin 1s linear infinite;margin-left:4px}}
  @keyframes spin{{to{{transform:rotate(360deg)}}}}
</style>
<script>
async function submitForm() {{
  const aid = document.getElementById('aid-input').value.trim();
  if (!aid) return;
  const msgEl = document.getElementById('submit-msg');
  msgEl.textContent = '提交中...'; msgEl.style.color='#94a3b8';
  try {{
    const r = await fetch((window.BP||'')+'/api/submit',{{
      method:'POST',headers:{{'Content-Type':'application/json'}},
      body:JSON.stringify({{arxiv_id:aid}})
    }});
    const d = await r.json();
    if (d.ok) {{
      msgEl.style.color='#22c55e'; msgEl.textContent='✅ '+d.msg+'，页面将自动刷新';
      setTimeout(()=>location.reload(),1500);
    }} else {{
      msgEl.style.color='#ef4444'; msgEl.textContent='❌ '+(d.error||d.msg);
    }}
  }} catch(e) {{ msgEl.style.color='#ef4444'; msgEl.textContent='❌ 网络错误'; }}
}}
async function submitId(aid) {{ document.getElementById('aid-input').value=aid; await submitForm(); }}
</script>"""
    return page("手动添加", body, active_tab="submit")


def main():
    import socketserver
    HOST = os.environ.get("BIND_HOST", "127.0.0.1")   # 默认只监听本机
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer((HOST, PORT), Handler) as httpd:
        print(f"Paper Hub Web → http://{HOST}:{PORT}", flush=True)
        httpd.serve_forever()

if __name__ == "__main__":
    main()
