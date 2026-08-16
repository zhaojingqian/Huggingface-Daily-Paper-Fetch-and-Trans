#!/usr/bin/env python3
"""Send Paper Trans maintenance alerts through the server SMTP config."""

import argparse
from email.header import Header
from email.mime.text import MIMEText
import os
from pathlib import Path
import smtplib
import socket
import ssl
import sys
import time


DEFAULT_CONFIG = "/root/scholar-citation-monitor/config.env"


def parse_bool(value, default=False):
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def load_env_file(path):
    values = {}
    config = Path(path)
    if not config.is_file():
        raise ValueError("SMTP config does not exist: %s" % config)
    for raw_line in config.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            values[key] = value
    return values


def smtp_config(path):
    values = load_env_file(path)
    for key in tuple(values):
        if key in os.environ:
            values[key] = os.environ[key]

    required = ("SMTP_HOST", "SMTP_FROM", "SMTP_TO")
    missing = [key for key in required if not values.get(key, "").strip()]
    if missing:
        raise ValueError("missing SMTP settings: %s" % ", ".join(missing))

    recipients = [item.strip() for item in values["SMTP_TO"].split(",") if item.strip()]
    return {
        "host": values["SMTP_HOST"].strip(),
        "port": int(values.get("SMTP_PORT", "587")),
        "user": values.get("SMTP_USER", "").strip(),
        "password": values.get("SMTP_PASSWORD", ""),
        "from_addr": values["SMTP_FROM"].strip(),
        "recipients": recipients,
        "use_tls": parse_bool(values.get("SMTP_USE_TLS"), True),
        "use_ssl": parse_bool(values.get("SMTP_USE_SSL"), False),
        "timeout": int(values.get("SMTP_TIMEOUT_SECONDS", "20")),
    }


def send(config, subject, body):
    message = MIMEText(body, "plain", "utf-8")
    message["Subject"] = Header(subject, "utf-8")
    message["From"] = config["from_addr"]
    message["To"] = ", ".join(config["recipients"])

    context = ssl.create_default_context()
    server = None
    try:
        if config["use_ssl"]:
            server = smtplib.SMTP_SSL(
                config["host"], config["port"], timeout=config["timeout"], context=context
            )
        else:
            server = smtplib.SMTP(
                config["host"], config["port"], timeout=config["timeout"]
            )
        server.ehlo()
        if config["use_tls"] and not config["use_ssl"]:
            server.starttls(context=context)
            server.ehlo()
        if config["user"]:
            server.login(config["user"], config["password"])
        server.sendmail(config["from_addr"], config["recipients"], message.as_string())
    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:
                # QUIT happens after sendmail has been accepted. A timeout here must
                # not turn a delivered alert into a false failure.
                server.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=os.environ.get("PAPER_TRANS_ALERT_CONFIG", DEFAULT_CONFIG))
    parser.add_argument("--subject", required=True)
    parser.add_argument("--body", required=True)
    args = parser.parse_args(argv)

    subject = "[Paper Trans] %s" % args.subject
    body = "%s\n\nHost: %s" % (args.body, socket.gethostname())
    try:
        config = smtp_config(args.config)
    except Exception as exc:
        print("maintenance alert configuration failed: %s" % exc, file=sys.stderr)
        return 1
    last_error = None
    for attempt in range(1, 4):
        try:
            send(config, subject, body)
            return 0
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                time.sleep(attempt * 2)
    print("maintenance alert failed after 3 attempts: %s" % last_error, file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
