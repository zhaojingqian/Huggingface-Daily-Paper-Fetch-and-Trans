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
KNOWN_TOPIC_HINTS = {
    "opd": {
        "must": ["OPD", "on-policy distillation"],
        "should": [
            "policy distillation",
            "online policy distillation",
            "dual on-policy distillation",
            "student policy",
            "teacher policy",
            "reinforcement learning distillation",
        ],
        "negative": [
            "openable part detection",
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


def generate_terms(query):
    """Generate topic expansion terms with a deterministic fallback."""
    q = (query or "").strip()
    fallback = topic_store.default_terms(q)
    if not q:
        return fallback
    prompt = f"""为 AI/CS/ML 论文检索扩展主题词。输入主题可能是缩写。

主题: {q}

只返回 JSON，不要解释：
{{
  "must": ["强相关且应优先匹配的英文术语，1-3个"],
  "should": ["同义词、全称、相关方法/任务英文术语，4-10个"],
  "negative": ["该缩写的常见无关含义或需要排除的英文术语，0-8个"]
}}"""
    messages = [
        {"role": "system", "content": "你是 AI/ML 论文检索专家，只输出可解析 JSON。"},
        {"role": "user", "content": prompt},
    ]
    def _merge_known_hints(terms):
        hint = KNOWN_TOPIC_HINTS.get(q.lower())
        if not hint:
            return terms
        merged = {}
        negative_lowers = {x.lower() for x in hint.get("negative", [])}
        for field in ("must", "should", "negative"):
            values = []
            for item in hint.get(field, []) + terms.get(field, []):
                lower = item.lower() if item else ""
                if field in ("must", "should") and lower in negative_lowers:
                    continue
                if item and lower not in {x.lower() for x in values}:
                    values.append(item)
            merged[field] = values
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
    term_query = " OR ".join(query_terms[:10])
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
