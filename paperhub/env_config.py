#!/usr/bin/env python3
"""Small .env loader for local Paper Hub runtime settings."""

import os

from paperhub.paths import ROOT_DIR


_LOADED = False


def load_env():
    """Load KEY=VALUE pairs from .env without overwriting real environment."""
    global _LOADED
    if _LOADED:
        return
    _LOADED = True

    path = os.path.join(ROOT_DIR, ".env")
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


def get_env(name, default=""):
    load_env()
    return os.environ.get(name, default)


def admin_token():
    return get_env("TOPIC_ADMIN_TOKEN", "")
