"""Gemini API key lookup. The key is never printed, logged or copied.
Order: env GEMINI_API_KEY / GEMINI, then the single GEMINI_API_KEY= (or GEMINI=) line of <repo>/.env
(only that line is read)."""
from __future__ import annotations

import os
import re

from common import ROOT

ENV_FILE = ROOT / ".env"
NAMES = ("GEMINI_API_KEY", "GEMINI")
_LINE = re.compile(r"^[ \t]*(?:export[ \t]+)?(GEMINI_API_KEY|GEMINI)[ \t]*=[ \t]*(.+?)[ \t]*$")


def _clean(v):
    v = (v or "").strip().strip('"').strip("'").strip()
    return v or None


def find_key(env_file=ENV_FILE) -> tuple[str | None, str | None]:
    """(key, source name). Only the source name is safe to print."""
    for n in NAMES:
        if _clean(os.environ.get(n)):
            return _clean(os.environ[n]), f"env:{n}"
    if os.path.exists(env_file):
        with open(env_file, encoding="utf-8", errors="ignore") as f:
            for line in f:
                m = _LINE.match(line.rstrip("\r\n"))
                if m and _clean(m.group(2)):
                    return _clean(m.group(2)), f".env:{m.group(1)}"
    return None, None


def get_key() -> str:
    v, _ = find_key()
    if not v:
        raise RuntimeError("Gemini API key not found: set GEMINI_API_KEY or add GEMINI_API_KEY=... to .env")
    return v


def scrub(text, key: str | None = None) -> str:
    s = str(text)
    if key:
        s = s.replace(key, "***")
    return re.sub(r"(key=)[A-Za-z0-9_\-]{20,}", r"\1***", s)


if __name__ == "__main__":
    v, src = find_key()
    print(f"Gemini key {'found' if v else 'NOT found'}" + (f" ({src})" if src else ""))
