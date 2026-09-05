"""Supabase REST 用戶端。

GitHub Actions 裡沒有 Management API 的權杖，改用 service role key 走 PostgREST。
"""

from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import env  # noqa: E402

# 本機直接跑時從 .env.local 補齊設定；
# CI 上沒有這個檔案，值由 GitHub Secrets 提供，load() 會是空操作。
env.load()


class Supabase:
    def __init__(self, url: str | None = None, key: str | None = None):
        self.url = (url or os.environ["SUPABASE_URL"]).rstrip("/")
        self.key = key or os.environ["SUPABASE_SERVICE_KEY"]

    def _headers(self, extra: dict | None = None) -> dict:
        headers = {
            "apikey": self.key,
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            "User-Agent": "Kikitori/0.1",
        }
        headers.update(extra or {})
        return headers

    def _call(self, method: str, path: str, body=None, headers: dict | None = None):
        request = urllib.request.Request(
            f"{self.url}/rest/v1/{path}",
            method=method,
            headers=self._headers(headers),
            data=json.dumps(body).encode() if body is not None else None,
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            raw = response.read()
        return json.loads(raw) if raw else None

    def select(self, table: str, query: str = "") -> list:
        suffix = f"?{query}" if query else ""
        return self._call("GET", f"{table}{suffix}") or []

    def insert(self, table: str, row: dict) -> list:
        return self._call(
            "POST", table, [row], headers={"Prefer": "return=representation"}
        ) or []

    def upsert(self, table: str, row: dict, on_conflict: str) -> list:
        return self._call(
            "POST",
            f"{table}?on_conflict={urllib.parse.quote(on_conflict)}",
            [row],
            headers={"Prefer": "resolution=merge-duplicates,return=representation"},
        ) or []

    def update(self, table: str, query: str, patch: dict) -> list:
        return self._call(
            "PATCH", f"{table}?{query}", patch, headers={"Prefer": "return=representation"}
        ) or []
