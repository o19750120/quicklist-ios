"""透過 Supabase Management API 操作資料庫。

不走 MCP，所以換專案不需要改設定也不用重開 Claude Code。
權杖從 .claude.json 的 supabase MCP 設定裡取，或用環境變數 SUPABASE_ACCESS_TOKEN。
"""

from __future__ import annotations

import io
import json
import os
import urllib.request

# trendrace：使用者授權放 Kikitori 資料的專案（同專案還有 daily 表，不要動它）
DEFAULT_PROJECT = "ouwvxdzuvwfzpdozbaby"

# 公司專案，任何情況都不准碰
FORBIDDEN_PROJECTS = {"jglxgtumcbtquwuuqqep"}

CLAUDE_CONFIG = os.path.expanduser("~/.claude.json")
API_ROOT = "https://api.supabase.com/v1"
USER_AGENT = "Mozilla/5.0 Kikitori/0.1"


def _find_token(node) -> str | None:
    if isinstance(node, dict):
        if str(node.get("url", "")).startswith("https://mcp.supabase.com"):
            auth = node.get("headers", {}).get("Authorization", "")
            return auth.replace("Bearer ", "").strip() or None
        for value in node.values():
            found = _find_token(value)
            if found:
                return found
    elif isinstance(node, list):
        for value in node:
            found = _find_token(value)
            if found:
                return found
    return None


def access_token() -> str:
    token = os.environ.get("SUPABASE_ACCESS_TOKEN")
    if token:
        return token
    with io.open(CLAUDE_CONFIG, encoding="utf-8") as handle:
        token = _find_token(json.load(handle))
    if not token:
        raise RuntimeError("找不到 Supabase access token")
    return token


def _request(method: str, path: str, body: dict | None = None):
    request = urllib.request.Request(
        f"{API_ROOT}{path}",
        method=method,
        headers={
            "Authorization": f"Bearer {access_token()}",
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        data=json.dumps(body).encode() if body else None,
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        raw = response.read()
    return json.loads(raw) if raw else None


def run_sql(sql: str, project: str = DEFAULT_PROJECT):
    if project in FORBIDDEN_PROJECTS:
        raise RuntimeError(f"專案 {project} 被標記為禁止操作")
    return _request("POST", f"/projects/{project}/database/query", {"query": sql})


def list_projects():
    return _request("GET", "/projects")


def api_keys(project: str = DEFAULT_PROJECT):
    if project in FORBIDDEN_PROJECTS:
        raise RuntimeError(f"專案 {project} 被標記為禁止操作")
    return _request("GET", f"/projects/{project}/api-keys?reveal=true")
