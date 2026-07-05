#!/usr/bin/env python3
"""Topic subscription retrieval, ranking, and translation pipeline."""

import json
import math
import os
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from urllib.parse import quote_plus

import requests

from paperhub import paper_store, topic_store
from paperhub.env_config import get_env
from paperhub.paths import PAPER_STORE_DIR


PROXY = "http://127.0.0.1:7890"
ARXIV_NS = {"atom": "http://www.w3.org/2005/Atom"}
TOPIC_MODEL_DEFAULT = "claude-opus-4-8-thinking"
TOPIC_SYSTEM_PROMPT = (
    "你是 AI/ML/CS 论文检索词规划专家。用户输入一定是 AI、机器学习或计算机科学论文主题，"
    "即使是缩写也必须优先按这些研究方向解释。你只输出可解析 JSON。"
)
ALLOWED_TOPIC_CATEGORIES = "cs.AI, cs.LG, cs.CL, cs.CV, cs.RO, cs.IR, stat.ML"
MAX_ARXIV_QUERY_TERMS = 16
KNOWN_TOPIC_HINTS = {
    "opd": {
        "must": ["OPD", "on-policy distillation"],
        "should": [
            "policy distillation",
            "online policy distillation",
            "dual on-policy distillation",
            "on-policy reinforcement learning",
            "student policy",
            "teacher policy",
            "reinforcement learning distillation",
            "policy optimization distillation",
            "distilling reasoning policies",
            "language agent policy distillation",
            "imitation learning distillation",
        ],
        "negative": [
            "openable part detection",
            "openable part",
            "articulated object",
            "articulated object part detection",
            "openable part motion prediction",
            "3D part segmentation",
            "articulation parameter estimation",
            "motion axis prediction",
            "optical path difference",
            "outpatient department",
            "obsessive personality disorder",
            "optimal power distribution",
        ],
    }
}


def _get_proxies(use_proxy):
    return {"http": PROXY, "https": PROXY} if use_proxy else {"http": "", "https": ""}


def _http_get(url, timeout=30, max_retries=3):
    use_proxy = True
    last_exc = None
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, timeout=timeout, proxies=_get_proxies(use_proxy))
            resp.raise_for_status()
            return resp.text
        except requests.exceptions.ProxyError as e:
            last_exc = e
            use_proxy = False
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout, requests.exceptions.SSLError) as e:
            last_exc = e
            if use_proxy:
                use_proxy = False
            elif attempt < max_retries - 1:
                time.sleep(2 ** attempt)
        except Exception as e:
            last_exc = e
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    raise last_exc or RuntimeError("HTTP request failed")


def topic_llm_config():
    base = get_env("TOPIC_LLM_BASE_URL", "").rstrip("/")
    api_key = get_env("TOPIC_LLM_API_KEY", "")
    model = get_env("TOPIC_LLM_MODEL", TOPIC_MODEL_DEFAULT)
    return {"base_url": base, "api_key": api_key, "model": model}


def _call_topic_llm(messages, max_tokens=1200):
    cfg = topic_llm_config()
    if not cfg["base_url"] or not cfg["api_key"]:
        raise RuntimeError("TOPIC_LLM_BASE_URL/TOPIC_LLM_API_KEY not configured")
    url = cfg["base_url"]
    if not url.endswith("/chat/completions"):
        url = url + "/v1/chat/completions" if not url.endswith("/v1") else url + "/chat/completions"
    payload = {
        "model": cfg["model"],
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": 0.2,
    }
    headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + cfg["api_key"],
    }
    proxies = _get_proxies(True)
    last_exc = None
    for attempt in range(3):
        try:
            resp = requests.post(url, headers=headers, json=payload, proxies=proxies, timeout=90)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except requests.exceptions.ProxyError as e:
            proxies = _get_proxies(False)
            last_exc = e
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout, requests.exceptions.SSLError) as e:
            last_exc = e
            if proxies.get("https"):
                proxies = _get_proxies(False)
            elif attempt < 2:
                time.sleep(2 ** attempt)
        except Exception as e:
            last_exc = e
            if attempt < 2:
                time.sleep(2 ** attempt)
    raise RuntimeError(f"topic LLM failed: {last_exc}")


def build_terms_prompt(query, hint=None):
    q = (query or "").strip()
    hint_text = ""
    if hint:
        preferred = _dedupe_terms(hint.get("must", []) + hint.get("should", []), 18)
        excluded = _dedupe_terms(hint.get("negative", []), 12)
        hint_text = f"""

本地语义偏好：
- preferred_terms: {json.dumps(preferred, ensure_ascii=False)}
- excluded_terms: {json.dumps(excluded, ensure_ascii=False)}
- 如果模型认为缩写还有其他 AI/ML/CS 含义，只有在明显贴近 preferred_terms 时才放入 should；否则放入 negative 或忽略。"""
    return f"""为长期 AI/ML/CS 论文订阅生成英文检索词。用户输入一定属于 AI、机器学习、深度学习、NLP、CV、机器人、信息检索或统计机器学习方向；不要把缩写扩展到医学、光学、电力、行政、商业、心理学等非 AI/ML/CS 含义。

订阅主题: {q}
检索范围: arXiv / Hugging Face Papers
允许类别: {ALLOWED_TOPIC_CATEGORIES}
{hint_text}

生成规则：
1. 如果主题是缩写，优先选择 AI/ML/CS 论文中最可能的全称和研究方向；非 AI/ML/CS 常见含义只能放入 negative。
2. must 放 1-3 个最高精度短语，包括原始缩写和最可能的规范英文全称；不要放过宽词。
3. should 放 8-16 个多元检索短语，覆盖同义词、全称变体、方法名、任务名、应用子方向、上游/下游相邻概念和 arXiv 标题常见写法。
4. should 要多样但仍强相关；避免只输出同一短语的大小写/单复数变化，也避免 AI、machine learning、deep learning、LLM、neural network 这类泛词，除非它们就是用户主题本身。
5. negative 放 0-10 个容易误召回的无关含义；不要把合理的 AI/ML/CS 相邻方向放进 negative。

只返回 JSON，不要解释，格式必须为：
{{
  "must": ["1-3 个强相关英文检索短语"],
  "should": ["8-16 个强相关且多元的英文检索短语"],
  "negative": ["0-10 个无关含义或排除短语"]
}}"""


def _dedupe_terms(values, limit=None):
    out = []
    seen = set()
    for item in values or []:
        value = re.sub(r"\s+", " ", str(item).strip())
        key = value.lower()
        if not value or key in seen:
            continue
        seen.add(key)
        out.append(value)
        if limit and len(out) >= limit:
            break
    return out


def _term_conflicts_with_negative(term, negative_terms):
    lower = (term or "").lower()
    if not lower:
        return False
    for neg in negative_terms or []:
        neg_lower = (neg or "").lower().strip()
        if not neg_lower:
            continue
        if lower == neg_lower or neg_lower in lower:
            return True
        if " " in lower and len(lower) > 5 and lower in neg_lower:
            return True
    return False


def generate_terms(query):
    """Generate topic expansion terms with a deterministic fallback."""
    q = (query or "").strip()
    fallback = topic_store.default_terms(q)
    if not q:
        return fallback
    known_hint = KNOWN_TOPIC_HINTS.get(q.lower())
    prompt = build_terms_prompt(q, known_hint)
    messages = [
        {"role": "system", "content": TOPIC_SYSTEM_PROMPT},
        {"role": "user", "content": prompt},
    ]
    def _merge_known_hints(terms):
        hint = known_hint
        if not hint:
            negative = _dedupe_terms(terms.get("negative", []), 10)
            must = [x for x in _dedupe_terms(terms.get("must", []), 3)
                    if not _term_conflicts_with_negative(x, negative)]
            should = [x for x in _dedupe_terms(terms.get("should", []), 16)
                      if not _term_conflicts_with_negative(x, negative)]
            return {"must": must, "should": should, "negative": negative}
        merged = {}
        negative = _dedupe_terms(hint.get("negative", []) + terms.get("negative", []), 10)
        for field in ("must", "should", "negative"):
            values = []
            for item in hint.get(field, []) + terms.get(field, []):
                if field in ("must", "should") and _term_conflicts_with_negative(item, negative):
                    continue
                if item and item.lower() not in {x.lower() for x in values}:
                    values.append(item)
            limit = 3 if field == "must" else 16 if field == "should" else 10
            merged[field] = _dedupe_terms(values, limit)
        return merged

    try:
        raw = _call_topic_llm(messages)
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return _merge_known_hints(fallback)
        data = json.loads(m.group())
        terms = {
            "must": [str(x).strip() for x in data.get("must", []) if str(x).strip()],
            "should": [str(x).strip() for x in data.get("should", []) if str(x).strip()],
            "negative": [str(x).strip() for x in data.get("negative", []) if str(x).strip()],
        }
        if not terms["must"] and not terms["should"]:
            return _merge_known_hints(fallback)
        if q.lower() not in {x.lower() for x in terms["must"] + terms["should"]}:
            terms["must"].insert(0, q)
        return _merge_known_hints(terms)
    except Exception as e:
        print(f"[topic] 术语生成失败，使用原始主题词: {e}", flush=True)
        return _merge_known_hints(fallback)


def ensure_topic(query_or_slug, refresh_terms=False):
    slug = topic_store.slugify(query_or_slug)
    profile = topic_store.get_topic(slug)
    if profile and not refresh_terms:
        return profile
    query = profile.get("query", query_or_slug) if profile else query_or_slug
    terms = generate_terms(query)
    payload = profile or {"slug": slug, "query": query}
    payload["generated_terms"] = terms
    payload["terms_generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return topic_store.upsert_topic(payload)


def _arxiv_id_from_url(url):
    m = re.search(r"/abs/(\d{4}\.\d{4,5})(?:v\d+)?", url or "")
    return m.group(1) if m else ""


def _parse_arxiv_feed(xml_text):
    root = ET.fromstring(xml_text)
    out = []
    for entry in root.findall("atom:entry", ARXIV_NS):
        id_url = (entry.findtext("atom:id", default="", namespaces=ARXIV_NS) or "").strip()
        aid = _arxiv_id_from_url(id_url)
        if not aid:
            continue
        title = re.sub(r"\s+", " ", entry.findtext("atom:title", default="", namespaces=ARXIV_NS) or "").strip()
        abstract = re.sub(r"\s+", " ", entry.findtext("atom:summary", default="", namespaces=ARXIV_NS) or "").strip()
        submitted_raw = entry.findtext("atom:published", default="", namespaces=ARXIV_NS) or ""
        submitted = submitted_raw[:10]
        authors = []
        for au in entry.findall("atom:author", ARXIV_NS):
            name = au.findtext("atom:name", default="", namespaces=ARXIV_NS)
            if name:
                authors.append(name)
        categories = [c.attrib.get("term", "") for c in entry.findall("atom:category", ARXIV_NS)]
        out.append({
            "arxiv_id": aid,
            "title": title,
            "abstract": abstract,
            "authors": ", ".join(authors),
            "submitted": submitted,
            "url": f"https://arxiv.org/abs/{aid}",
            "pdf_url": f"https://arxiv.org/pdf/{aid}",
            "categories": [c for c in categories if c],
        })
    return out


def _quote_term(term):
    term = (term or "").strip()
    if not term:
        return ""
    safe = term.replace('"', "")
    if " " in safe:
        return f'all:"{safe}"'
    return f'all:{safe}'


def fetch_arxiv_candidates(profile, days=30, max_results=80):
    terms = profile.get("generated_terms", {})
    query_terms = []
    for term in terms.get("must", []) + terms.get("should", []):
        q = _quote_term(term)
        if q and q not in query_terms:
            query_terms.append(q)
    if not query_terms:
        query_terms = [_quote_term(profile.get("query", ""))]

    cat_query = " OR ".join(f"cat:{c}" for c in profile.get("categories", topic_store.DEFAULT_CATEGORIES))
    term_query = " OR ".join(query_terms[:MAX_ARXIV_QUERY_TERMS])
    search_query = f"({cat_query}) AND ({term_query})"
    url = (
        "https://export.arxiv.org/api/query?search_query="
        + quote_plus(search_query)
        + f"&start=0&max_results={max_results}&sortBy=submittedDate&sortOrder=descending"
    )
    xml_text = _http_get(url, timeout=40)
    candidates = _parse_arxiv_feed(xml_text)
    cutoff = datetime.now().date() - timedelta(days=days)
    allowed = set(profile.get("categories", topic_store.DEFAULT_CATEGORIES))
    filtered = []
    seen = set()
    for c in candidates:
        if c["arxiv_id"] in seen:
            continue
        seen.add(c["arxiv_id"])
        try:
            submitted_date = datetime.strptime(c.get("submitted", ""), "%Y-%m-%d").date()
            if submitted_date < cutoff:
                continue
        except Exception:
            pass
        if allowed and not (set(c.get("categories", [])) & allowed):
            continue
        filtered.append(c)
    return filtered


def fetch_hf_votes(days=7, limit_per_day=50):
    from fetch_hf import fetch_hf_papers

    votes = {}
    today = datetime.now().date()
    for offset in range(days):
        key = (today - timedelta(days=offset)).strftime("%Y-%m-%d")
        for p in fetch_hf_papers("daily", key, limit_per_day):
            aid = p.get("arxiv_id", "")
            if not aid:
                continue
            votes[aid] = max(int(p.get("upvotes") or 0), votes.get(aid, 0))
    return votes


def _text_for_candidate(candidate):
    return " ".join([
        candidate.get("title", ""),
        candidate.get("abstract", ""),
        " ".join(candidate.get("categories", []) or []),
    ]).lower()


def _term_hit_score(text, term):
    term = (term or "").lower().strip()
    if not term:
        return 0.0
    if term in text:
        return 1.0
    tokens = [t for t in re.split(r"[^a-z0-9]+", term) if len(t) > 2]
    if not tokens:
        return 0.0
    hits = sum(1 for t in tokens if t in text)
    return hits / float(len(tokens))


def relevance_score(profile, candidate):
    text = _text_for_candidate(candidate)
    terms = profile.get("generated_terms", {})
    negative = terms.get("negative", [])
    if any(_term_hit_score(text, t) >= 1.0 for t in negative):
        return 0.0
    must = terms.get("must", []) or [profile.get("query", "")]
    should = terms.get("should", [])
    total_weight = max(1.0, 2.0 * len(must) + len(should))
    score = 0.0
    for term in must:
        score += 2.0 * _term_hit_score(text, term)
    for term in should:
        score += _term_hit_score(text, term)
    return min(1.0, score / total_weight)


def freshness_score(candidate, key_date=None, window_days=30):
    ref = key_date or datetime.now().date()
    if isinstance(ref, str):
        ref = datetime.strptime(ref, "%Y-%m-%d").date()
    try:
        submitted = datetime.strptime(candidate.get("submitted", ""), "%Y-%m-%d").date()
    except Exception:
        return 0.2
    age = max(0, (ref - submitted).days)
    return max(0.0, 1.0 - age / float(window_days))


def rank_candidates(profile, candidates, votes_by_id=None, seen_ids=None, limit=3, force=False, key=None):
    votes_by_id = votes_by_id or {}
    seen_ids = seen_ids or set()
    max_votes = max([1] + [int(votes_by_id.get(c.get("arxiv_id", ""), 0)) for c in candidates])
    weights = {**topic_store.DEFAULT_WEIGHTS, **(profile.get("weights") or {})}
    ranked = []
    for c in candidates:
        aid = c.get("arxiv_id", "")
        if not force and aid in seen_ids:
            continue
        rel = relevance_score(profile, c)
        if rel <= 0:
            continue
        fresh = freshness_score(c, key)
        vote_raw = int(votes_by_id.get(aid, 0))
        vote = math.log1p(vote_raw) / math.log1p(max_votes) if max_votes > 0 else 0.0
        total = (
            weights["relevance"] * rel
            + weights["freshness"] * fresh
            + weights["votes"] * vote
        )
        item = dict(c)
        item.update({
            "upvotes": vote_raw,
            "topic_score": round(total, 4),
            "topic_score_parts": {
                "relevance": round(rel, 4),
                "freshness": round(fresh, 4),
                "votes": round(vote, 4),
            },
            "source": "arxiv+hf" if vote_raw else "arxiv",
        })
        ranked.append(item)
    ranked.sort(key=lambda x: (x["topic_score"], x.get("upvotes", 0), x.get("submitted", "")), reverse=True)
    return ranked[:limit]


def _topic_papers_dir(slug, key):
    path = os.path.join(topic_store.date_dir(slug, key), "papers")
    os.makedirs(path, exist_ok=True)
    return path


def _paper_store_entry(arxiv_id):
    return paper_store.read_translated(arxiv_id) or paper_store.read_raw(arxiv_id)


def _translate_summary(candidate, rank, slug, key):
    from translate_arxiv import load_api_config, translate_and_save

    cached = paper_store.read_translated(candidate["arxiv_id"])
    if cached:
        return {**cached, "rank": rank, "html_file": f"papers/{candidate['arxiv_id']}.html"}
    return translate_and_save(
        arxiv_id=candidate["arxiv_id"],
        output_dir=_topic_papers_dir(slug, key),
        rank=rank,
        week_str=f"topic/{slug}",
        config=load_api_config(),
        prefetched_meta=candidate,
    )


def _ensure_pdf(arxiv_id):
    if paper_store.pdf_hit(arxiv_id):
        paper_store.update_pdf_status(arxiv_id, "ok")
        return True
    from translate_full import translate_full

    result = translate_full(arxiv_id=arxiv_id, output_dir=PAPER_STORE_DIR, no_cache=False, timeout=3600)
    if result.get("pdf_path"):
        paper_store.update_pdf_status(arxiv_id, "ok")
        return True
    paper_store.update_pdf_status(arxiv_id, "failed")
    return False


def run_topic(slug_or_query, key=None, limit=3, do_full_translate=True, force=False, refresh_terms=False):
    key = key or datetime.now().strftime("%Y-%m-%d")
    profile = ensure_topic(slug_or_query, refresh_terms=refresh_terms)
    slug = profile["slug"]
    print(f"[topic] 开始: {slug} {key}", flush=True)
    candidates = fetch_arxiv_candidates(profile)
    votes = fetch_hf_votes()
    ranked = rank_candidates(
        profile,
        candidates,
        votes_by_id=votes,
        seen_ids=topic_store.load_seen(slug),
        limit=limit,
        force=force,
        key=key,
    )

    papers = []
    for i, cand in enumerate(ranked, 1):
        aid = cand["arxiv_id"]
        print(f"[topic] [{i}/{len(ranked)}] {aid} score={cand['topic_score']}", flush=True)
        try:
            translated = _translate_summary(cand, i, slug, key)
            entry = {**cand, **translated, "rank": i}
        except Exception as e:
            print(f"[topic] 摘要翻译失败 {aid}: {e}", flush=True)
            stored = _paper_store_entry(aid)
            entry = {**cand, **stored, "rank": i, "error": str(e)}
        if do_full_translate:
            try:
                if _ensure_pdf(aid):
                    entry["pdf_zh"] = f"papers/{aid}_zh.pdf"
                else:
                    entry["pdf_zh_failed"] = True
            except Exception as e:
                entry["pdf_zh_failed"] = True
                entry["pdf_error"] = str(e)
                paper_store.update_pdf_status(aid, "failed")
                print(f"[topic] PDF 失败 {aid}: {e}", flush=True)
        else:
            entry.setdefault("pdf_status", "none")
        papers.append(entry)

    topic_store.save_index(
        slug,
        key,
        papers,
        extra={
            "query": profile.get("query", slug),
            "display_name": profile.get("display_name", ""),
            "generated_terms": profile.get("generated_terms", {}),
            "weights": profile.get("weights", topic_store.DEFAULT_WEIGHTS),
        },
    )
    if not force:
        topic_store.mark_seen(slug, [p.get("arxiv_id") for p in papers])
    print(f"[topic] 完成: {slug} {key} total={len(papers)}", flush=True)
    return {"topic": slug, "key": key, "total": len(papers), "papers": papers}


def run_all_topics(key=None, limit=3, do_full_translate=True, force=False):
    results = []
    for profile in topic_store.list_topics(enabled=True):
        results.append(run_topic(profile["slug"], key=key, limit=limit,
                                 do_full_translate=do_full_translate, force=force))
    return results


def _recent_date_keys(days):
    today = datetime.now().date()
    return sorted((today - timedelta(days=d)).strftime("%Y-%m-%d") for d in range(days))


def topic_repair_targets(topic=None, key=None, days=None, scan_all=False):
    """Return (profile, key) pairs for topic repair/retry scans."""
    topic_slug = topic_store.slugify(topic) if topic else ""
    target_key = key
    if target_key and "/" in target_key:
        topic_part, key_part = target_key.split("/", 1)
        if not topic_slug:
            topic_slug = topic_store.slugify(topic_part)
        target_key = key_part

    if topic_slug:
        profile = topic_store.get_topic(topic_slug)
        profiles = [profile] if profile else []
    else:
        profiles = topic_store.list_topics()

    recent = set(_recent_date_keys(days)) if days is not None and not scan_all and not target_key else None
    targets = []
    for profile in profiles:
        slug = profile.get("slug", "")
        keys = [target_key] if target_key else topic_store.list_keys(slug)
        for k in keys:
            if not k:
                continue
            if recent is not None and k not in recent:
                continue
            if not os.path.exists(topic_store.index_path(slug, k)):
                continue
            targets.append((profile, k))
    return targets


def _write_topic_index(slug, key, idx):
    path = topic_store.index_path(slug, key)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    idx["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(idx, f, ensure_ascii=False, indent=2)


def repair_topic(topic=None, key=None, days=None, scan_all=False):
    """
    Repair topic summary translations whose paper store entry lacks title_zh/summary_zh.

    This mirrors run_papers.repair(): metadata/translation are written to the
    shared paper store, while topic slim indexes stay unchanged.
    """
    from translate_arxiv import load_api_config, translate_and_save

    config = load_api_config()
    total_fixed = 0
    targets = topic_repair_targets(topic=topic, key=key, days=days, scan_all=scan_all)
    for profile, k in targets:
        slug = profile.get("slug", "")
        idx = topic_store.load_index(slug, k)
        changed = False
        for slim in idx.get("papers", []):
            aid = slim.get("arxiv_id", "")
            if not aid:
                continue
            stored = paper_store.read_raw(aid)
            if stored.get("title_zh") and stored.get("summary_zh"):
                continue
            print(f"[repair-topic] {slug}/{k} — 重新翻译: {aid}", flush=True)
            try:
                result = translate_and_save(
                    arxiv_id=aid,
                    output_dir=PAPER_STORE_DIR,
                    rank=slim.get("rank", 1),
                    week_str=f"topic/{slug}",
                    config=config,
                )
                if result.get("title_zh") and result.get("summary_zh"):
                    total_fixed += 1
                    changed = True
                    print(f"[repair-topic] ✅ {result['title_zh'][:60]}", flush=True)
                else:
                    print(f"[repair-topic] ❌ 仍无完整中文翻译: {aid}", flush=True)
            except Exception as e:
                print(f"[repair-topic] ❌ {aid}: {e}", flush=True)
        if changed:
            print(f"[repair-topic] 💾 paper store 已更新，topic index 无需改变: {slug}/{k}", flush=True)
    print(f"[repair-topic] 完成，共修复 {total_fixed} 篇", flush=True)
    return total_fixed


def retry_topic_pdf(topic=None, key=None, days=None, scan_all=False):
    """Retry topic pdf_status=failed entries using the same retry logic as daily."""
    from run_papers import retry_failed_pdf_entries

    total_ok = 0
    total_fail = 0
    targets = topic_repair_targets(topic=topic, key=key, days=days, scan_all=scan_all)
    for profile, k in targets:
        slug = profile.get("slug", "")
        idx = topic_store.load_index(slug, k)
        papers = idx.get("papers", [])
        if not papers:
            continue
        result = retry_failed_pdf_entries(papers, label=f"[retry-topic-pdf] {slug}/{k}")
        total_ok += result["ok"]
        total_fail += result["failed"]
        if result["changed"]:
            idx["papers"] = papers
            _write_topic_index(slug, k, idx)
    print(f"[retry-topic-pdf] 完成: 成功={total_ok} 仍失败={total_fail}", flush=True)
    return total_ok
