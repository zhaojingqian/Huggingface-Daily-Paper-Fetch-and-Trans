#!/usr/bin/env python3
"""Persistent storage helpers for topic subscriptions."""

import os
import re
from datetime import datetime

from paperhub.paths import TOPIC_DIR
from paperhub.json_io import read_json, write_json_atomic


TOPICS_FILE = os.path.join(TOPIC_DIR, "topics.json")
DEFAULT_CATEGORIES = ["cs.AI", "cs.LG", "cs.CL", "cs.CV", "cs.RO", "cs.IR", "stat.ML"]
DEFAULT_WEIGHTS = {"relevance": 0.45, "freshness": 0.30, "votes": 0.25}


def slugify(value):
    slug = re.sub(r"[^a-z0-9_-]+", "-", (value or "").strip().lower())
    slug = re.sub(r"-+", "-", slug).strip("-_")
    return slug[:60] or "topic"


def ensure_topic_dir(slug=None):
    base = TOPIC_DIR if slug is None else os.path.join(TOPIC_DIR, slug)
    os.makedirs(base, exist_ok=True)
    return base


def _read_json(path, default):
    return read_json(path, default)


def _write_json(path, payload):
    write_json_atomic(path, payload)


def load_topics():
    data = _read_json(TOPICS_FILE, {"topics": {}})
    data.setdefault("topics", {})
    return data


def save_topics(data):
    data.setdefault("updated_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    data.setdefault("topics", {})
    _write_json(TOPICS_FILE, data)


def default_terms(query):
    q = (query or "").strip()
    return {"must": [q] if q else [], "should": [], "negative": []}


def normalize_display_name(value):
    return re.sub(r"\s+", " ", str(value or "").strip())[:80]


def normalize_profile(profile):
    query = (profile.get("query") or profile.get("slug") or "").strip()
    slug = slugify(profile.get("slug") or query)
    terms = profile.get("generated_terms") or profile.get("terms") or default_terms(query)
    normalized = {
        "slug": slug,
        "query": query or slug,
        "display_name": normalize_display_name(profile.get("display_name", "")),
        "enabled": bool(profile.get("enabled", True)),
        "generated_terms": {
            "must": [str(x).strip() for x in terms.get("must", []) if str(x).strip()],
            "should": [str(x).strip() for x in terms.get("should", []) if str(x).strip()],
            "negative": [str(x).strip() for x in terms.get("negative", []) if str(x).strip()],
        },
        "categories": profile.get("categories") or list(DEFAULT_CATEGORIES),
        "weights": {**DEFAULT_WEIGHTS, **(profile.get("weights") or {})},
        "created_at": profile.get("created_at") or datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    return normalized


def upsert_topic(profile):
    normalized = normalize_profile(profile)
    data = load_topics()
    existing = data["topics"].get(normalized["slug"], {})
    merged = {**existing, **normalized}
    if "display_name" not in profile and existing.get("display_name"):
        merged["display_name"] = existing["display_name"]
    data["topics"][normalized["slug"]] = normalize_profile(merged)
    save_topics(data)
    ensure_topic_dir(normalized["slug"])
    return data["topics"][normalized["slug"]]


def get_topic(slug):
    return load_topics().get("topics", {}).get(slugify(slug))


def list_topics(enabled=None):
    topics = list(load_topics().get("topics", {}).values())
    topics.sort(key=lambda p: p.get("updated_at", ""), reverse=True)
    if enabled is None:
        return topics
    return [p for p in topics if bool(p.get("enabled", True)) is enabled]


def set_topic_enabled(slug, enabled):
    data = load_topics()
    key = slugify(slug)
    if key not in data.get("topics", {}):
        return None
    data["topics"][key]["enabled"] = bool(enabled)
    data["topics"][key]["updated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    save_topics(data)
    return data["topics"][key]


def seen_path(slug):
    return os.path.join(ensure_topic_dir(slugify(slug)), "seen.json")


def load_seen(slug):
    data = _read_json(seen_path(slug), {"arxiv_ids": []})
    return set(data.get("arxiv_ids", []))


def save_seen(slug, arxiv_ids):
    _write_json(seen_path(slug), {"arxiv_ids": sorted(set(arxiv_ids))})


def mark_seen(slug, arxiv_ids):
    seen = load_seen(slug)
    seen.update(a for a in arxiv_ids if a)
    save_seen(slug, seen)


def date_dir(slug, key):
    return os.path.join(ensure_topic_dir(slugify(slug)), key)


def index_path(slug, key):
    return os.path.join(date_dir(slug, key), "index.json")


def save_index(slug, key, papers, extra=None):
    slim = []
    for p in papers:
        item = {
            "arxiv_id": p.get("arxiv_id", ""),
            "rank": p.get("rank", 0),
            "upvotes": p.get("upvotes", 0),
            "topic_score": p.get("topic_score", 0),
            "source": p.get("source", ""),
        }
        if p.get("pdf_zh"):
            item["pdf_status"] = "ok"
        elif p.get("pdf_zh_failed"):
            item["pdf_status"] = "failed"
        elif p.get("pdf_status"):
            item["pdf_status"] = p.get("pdf_status")
        slim.append(item)
    payload = {
        "mode": "topic",
        "key": key,
        "topic": slugify(slug),
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "total": len(slim),
        "papers": slim,
    }
    if extra:
        payload.update(extra)
    path = index_path(slug, key)
    _write_json(path, payload)
    return path


def load_index(slug, key):
    return _read_json(index_path(slug, key), {})


def list_keys(slug):
    base = ensure_topic_dir(slugify(slug))
    keys = []
    for name in os.listdir(base):
        if re.match(r"^\d{4}-\d{2}-\d{2}$", name) and os.path.isfile(index_path(slug, name)):
            keys.append(name)
    return sorted(keys, reverse=True)
