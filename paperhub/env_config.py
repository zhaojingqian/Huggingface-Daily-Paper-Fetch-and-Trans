#!/usr/bin/env python3
"""Load the shared workspace environment and PaperHub's scoped settings."""

import os

from paperhub.paths import ROOT_DIR


_LOADED = False

# The generated gpt-academic runtime config is authoritative in production;
# this fallback keeps standalone metadata translation on the same model.
DEFAULT_TRANSLATION_MODEL = "deepseek-v4-flash-0731"
DEFAULT_HTTP_PROXY = "http://127.0.0.1:7890"


def _load_env_file(path):
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip("'\"")
                if key and key not in os.environ:
                    os.environ[key] = value
    except FileNotFoundError:
        return


def load_env():
    """Load common paths first, then PaperHub secrets, without overwrites."""
    global _LOADED
    if _LOADED:
        return
    _LOADED = True

    workspace_root = os.environ.get(
        "WORKSPACE_ROOT",
        os.path.dirname(os.path.dirname(ROOT_DIR)),
    )
    _load_env_file(os.path.join(workspace_root, ".env"))
    paper_env = os.environ.get(
        "PAPER_TRANS_ENV_FILE",
        os.path.join(workspace_root, ".env.d", "paper.env"),
    )
    _load_env_file(paper_env)


def get_env(name, default=""):
    load_env()
    return os.environ.get(name, default)


def http_proxies(use_proxy=True):
    """Return the shared requests proxy mapping for host-side integrations."""
    if not use_proxy:
        return {"http": "", "https": ""}
    proxy = get_env("PAPER_TRANS_PROXY", DEFAULT_HTTP_PROXY) or DEFAULT_HTTP_PROXY
    return {"http": proxy, "https": proxy}


def admin_token():
    return get_env("TOPIC_ADMIN_TOKEN", "")
