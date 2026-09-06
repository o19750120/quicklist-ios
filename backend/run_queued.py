"""把資料庫裡排隊中的轉錄任務跑完。

正常情況下 App 排隊後，Supabase 觸發器會立刻叫醒 GitHub，
這支腳本是兜底用的：萬一觸發器沒送到（網路問題、pg_net 佇列塞住），
排程會定時進來把漏掉的撿起來。
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from supabase_client import Supabase  # noqa: E402

# 一次 CI 執行最多處理幾集。
# 限制的原因不是效能，是 GitHub Actions 單次 job 有 6 小時上限，
# 而 Gemini verbatim 轉錄一集可能要一小時以上（比 Deepgram 慢十倍）。
# 排隊中的其餘集數會等下一次排程。
MAX_PER_RUN = 3


def main() -> int:
    supabase = Supabase()
    jobs = supabase.select(
        "kikitori_jobs",
        f"status=eq.queued&order=created_at.asc&limit={MAX_PER_RUN}",
    )

    if not jobs:
        print("沒有排隊中的任務")
        return 0

    print(f"撿到 {len(jobs)} 個任務")
    failed = 0

    for job in jobs:
        print(f"--- {job['id']}：{job['show_name']} / {job['episode_title']}")
        result = subprocess.run(
            [sys.executable, str(Path(__file__).parent / "transcribe.py"), "--job", job["id"]]
        )
        if result.returncode != 0:
            failed += 1

    # 全部失敗才算這次執行失敗，部分失敗不必讓整個工作流變紅
    return 1 if failed == len(jobs) else 0


if __name__ == "__main__":
    sys.exit(main())
