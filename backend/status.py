"""一次看完 Kikitori 的所有狀況。

    python backend/status.py            # 總覽
    python backend/status.py --logs 40  # 多看一點 App 紀錄
    python backend/status.py --errors   # 只看錯誤

包含：App 回報的執行紀錄、轉錄任務、逐字稿存量、CI 最近狀態，
以及免費憑證還剩幾天（用該裝置第一筆啟動紀錄推算）。
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import db  # noqa: E402

FREE_CERT_DAYS = 7


def section(title: str) -> None:
    print(f"\n{'─' * 66}\n{title}\n{'─' * 66}")


def relative(iso: str) -> str:
    if not iso:
        return ""
    try:
        moment = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso[:19]
    delta = datetime.now(timezone.utc) - moment
    seconds = int(delta.total_seconds())
    if seconds < 60:
        return f"{seconds} 秒前"
    if seconds < 3600:
        return f"{seconds // 60} 分鐘前"
    if seconds < 86400:
        return f"{seconds // 3600} 小時前"
    return f"{seconds // 86400} 天前"


def show_jobs() -> None:
    section("轉錄任務")
    rows = db.run_sql("""
        select show_name, episode_title, status, stage, error, created_at
          from public.kikitori_jobs
         order by created_at desc limit 8
    """)
    if not rows:
        print("  （沒有任務）")
        return
    for r in rows:
        mark = {"done": "✓", "failed": "✗", "running": "…", "queued": "·"}.get(r["status"], "?")
        print(f"  {mark} {r['status']:<8} {relative(r['created_at']):<10} "
              f"{r['show_name'][:16]:<18} {r['episode_title'][:30]}")
        if r["error"]:
            print(f"      錯誤：{r['error'][:100]}")


def show_transcripts() -> None:
    section("已完成的逐字稿")
    rows = db.run_sql("""
        select e.show_name, e.episode_title, t.line_count, t.source_model, t.created_at
          from public.kikitori_episodes e
          join public.kikitori_transcripts t on t.episode_id = e.id
         order by t.created_at desc limit 10
    """)
    if not rows:
        print("  （還沒有）")
        return
    for r in rows:
        print(f"  {r['line_count']:>4} 句  {relative(r['created_at']):<10} "
              f"{r['show_name'][:16]:<18} {r['episode_title'][:32]}")


def show_logs(limit: int, errors_only: bool) -> None:
    section("App 回報的紀錄" + ("（只看錯誤）" if errors_only else ""))
    where = "where level = 'error'" if errors_only else ""
    rows = db.run_sql(f"""
        select level, category, message, app_version, created_at,
               left(device_id, 8) as device
          from public.kikitori_logs
          {where}
         order by created_at desc limit {limit}
    """)
    if not rows:
        print("  （還沒收到，App 要更新到有遙測的版本才會回報）")
        return
    for r in rows:
        mark = {"error": "✗", "warn": "!", "info": "·"}.get(r["level"], "·")
        print(f"  {mark} {relative(r['created_at']):<10} [{r['category'] or '-'}] {r['message'][:70]}")


def show_certificate() -> None:
    section("憑證與裝置")
    rows = db.run_sql("""
        select left(device_id, 8) as device,
               max(app_version) as version,
               min(created_at) as first_seen,
               max(created_at) as last_seen,
               count(*) as events
          from public.kikitori_logs
         group by device_id
         order by max(created_at) desc limit 4
    """)
    if not rows:
        print("  （還沒有裝置回報過）")
        return
    for r in rows:
        first = datetime.fromisoformat(r["first_seen"].replace("Z", "+00:00"))
        days_left = FREE_CERT_DAYS - (datetime.now(timezone.utc) - first).days
        warning = "  ← 快到期了，記得重新匯入" if days_left <= 2 else ""
        print(f"  裝置 {r['device']}  版本 {r['version']}  事件 {r['events']} 筆")
        print(f"    首次啟動 {relative(r['first_seen'])}，最後活動 {relative(r['last_seen'])}")
        print(f"    憑證推估還剩 {max(0, days_left)} 天{warning}")


def show_ci() -> None:
    section("CI 最近狀態")
    try:
        raw = subprocess.run(
            ["gh", "run", "list", "--limit", "6", "--json",
             "name,status,conclusion,createdAt,displayTitle"],
            capture_output=True, text=True, timeout=30, cwd=Path(__file__).parent.parent,
        ).stdout
        runs = json.loads(raw)
    except Exception as exc:
        print(f"  查不到（{exc}）")
        return

    for run in runs:
        mark = {"success": "✓", "failure": "✗"}.get(run.get("conclusion"), "…")
        print(f"  {mark} {run['name'][:20]:<22} {relative(run['createdAt']):<10} "
              f"{run['displayTitle'][:36]}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--logs", type=int, default=15)
    parser.add_argument("--errors", action="store_true")
    args = parser.parse_args()

    show_jobs()
    show_transcripts()
    show_logs(args.logs, args.errors)
    show_certificate()
    show_ci()
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
