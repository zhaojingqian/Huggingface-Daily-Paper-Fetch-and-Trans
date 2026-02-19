#!/usr/bin/env python3
"""Paper Trans Web Server — 端口 18080"""

import http.server, os, json, re
from urllib.parse import unquote
from datetime import datetime

PORT      = 18080
BASE_DIR  = os.path.dirname(os.path.abspath(__file__))
DATA_DIR  = os.path.join(BASE_DIR, "data")
WEEKLY_DIR = os.path.join(BASE_DIR, "weekly")   # 兼容旧目录


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
"""

# ── HTML 工具 ─────────────────────────────────────────────────────────────────
def page(title, body, active_tab="weekly"):
    tab_items = [
        ("daily",   "📅 每日", "/"),
        ("weekly",  "📚 每周", "/weekly"),
        ("monthly", "📆 每月", "/monthly"),
    ]
    tabs_html = "".join(
        f'<a class="tab{" active" if t==active_tab else ""}" href="{href}">{label}</a>'
        for t, label, href in tab_items
    )
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} — Paper Trans</title>
<style>{CSS}</style>
</head><body>
<div class="topbar">
  <div class="topbar-inner">
    <h1>📰 Paper Trans <span>HF Papers 中文精选</span></h1>
    <div class="tabs">{tabs_html}</div>
  </div>
</div>
<div class="main">{body}</div>
</body></html>"""

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

    def do_GET(self):
        raw  = unquote(self.path).split("?")[0]
        parts = [p for p in raw.strip("/").split("/") if p]

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


def main():
    import socketserver
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), Handler) as httpd:
        print(f"Paper Trans Web → http://0.0.0.0:{PORT}", flush=True)
        httpd.serve_forever()

if __name__ == "__main__":
    main()
