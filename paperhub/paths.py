#!/usr/bin/env python3
"""Central path and runtime-name constants for Paper Hub.

The top-level scripts still expose their historical module constants; this
module only keeps the values from drifting apart as the project grows.
"""

import os


ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATA_DIR = os.path.join(ROOT_DIR, "data")
PAPER_STORE_DIR = os.path.join(DATA_DIR, "papers")
LOGS_DIR = os.path.join(ROOT_DIR, "logs")
LOCK_DIR = os.path.join(ROOT_DIR, "locks")

MANUAL_DIR = os.path.join(DATA_DIR, "manual")
TOPIC_DIR = os.path.join(DATA_DIR, "topic")
BOOKMARKS_FILE = os.path.join(DATA_DIR, "bookmarks.json")
SUBMIT_JOBS_FILE = os.path.join(MANUAL_DIR, "jobs.json")

TEX_BACKUP_DIR = os.path.join(DATA_DIR, "tex_backup")
TEX_FAILED_BACKUP_DIR = os.path.join(DATA_DIR, "tex_backup_failed")

DEFAULT_GPT_ACADEMIC_CONTAINER = "gpt-academic-latex-slim"


def gpt_academic_container():
    """Return the configured gpt-academic Docker container name."""
    return os.environ.get("GPT_ACADEMIC_CONTAINER", DEFAULT_GPT_ACADEMIC_CONTAINER)


def paper_store_json_path(arxiv_id):
    return os.path.join(PAPER_STORE_DIR, f"{arxiv_id}.json")


def paper_store_pdf_path(arxiv_id):
    return os.path.join(PAPER_STORE_DIR, f"{arxiv_id}_zh.pdf")


def mode_key_dir(mode, key):
    return os.path.join(DATA_DIR, mode, key)


def mode_papers_dir(mode, key):
    return os.path.join(mode_key_dir(mode, key), "papers")


def mode_dir(mode):
    return os.path.join(DATA_DIR, mode)


def mode_index_path(mode, key):
    return os.path.join(mode_key_dir(mode, key), "index.json")
