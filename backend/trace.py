"""一集逐字稿從按下按鈕到呈現在 App 之間，發生過什麼。

原本這些資訊分散在三個地方而且都留不住：

    kikitori_jobs.stage   每一步覆蓋前一步，跑完只剩「完成」兩個字
    CI 日誌               細節都在，但會過期、也沒辦法查詢
    providers.CALLS       只存在記憶體裡，行程結束就沒了

所以「這集為什麼花了 23 分鐘」「中途換過幾次引擎」「哪個模型翻譯的」
事後都答不出來。這支模組把它們收集起來寫進 `kikitori_jobs.diagnostics`，
變成查得到的紀錄。

每一集會記下：

    整體      開始／結束時間、總秒數、音檔長度、產出幾句幾個詞
    各階段    尋找音檔、轉錄、斷句、翻譯、建詞表各花多久
    模型      轉錄用哪個、翻譯用哪個、讀音覆核用哪些
    換手      哪一家失敗了、失敗原因是什麼
    API       每個模型的成功／失敗次數、用了哪幾把金鑰、失敗原因分佈

金鑰只記索引不記內容。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone


class Trace:
    """一次轉錄的完整紀錄。

    用法是照著流程呼叫 `stage()`，最後 `finish()` 拿到可以寫進資料庫的 dict。
    `stage()` 會自動把前一個階段的耗時結算掉，所以呼叫端不必自己計時。
    """

    def __init__(self) -> None:
        self.started = time.time()
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.stages: list[dict] = []
        self.models: dict[str, object] = {}
        self.fallbacks: list[str] = []
        self.facts: dict[str, object] = {}
        self._current: str | None = None
        self._since = self.started

    def stage(self, name: str) -> None:
        """進入下一個階段，順便結算上一個。"""
        now = time.time()
        if self._current:
            self.stages.append({"name": self._current,
                                "seconds": round(now - self._since, 1)})
        self._current = name
        self._since = now

    def model(self, role: str, name) -> None:
        """記下某個角色用了哪個模型（轉錄／翻譯／讀音覆核）。"""
        self.models[role] = name

    def fallback(self, reason: str) -> None:
        """換手的原因。這是「為什麼這集品質比較差」最直接的答案。"""
        self.fallbacks.append(reason[:300])

    def fact(self, **values) -> None:
        """順手記下的數字：音檔多長、幾句、幾個詞。"""
        self.facts.update(values)

    def finish(self, status: str, error: str = "") -> dict:
        import providers

        self.stage("")          # 結算最後一個階段
        self._current = None

        # 把 providers.CALLS 收斂成「每個模型一列」，
        # 逐次紀錄太細，存進資料庫只會變成沒人看的雜訊
        grouped: dict[tuple, dict] = {}
        for call in providers.CALLS:
            key = (call["provider"], call["model"])
            row = grouped.setdefault(key, {
                "provider": call["provider"], "model": call["model"],
                "ok": 0, "failed": 0, "keys": set(), "seconds": 0.0, "errors": {},
            })
            row["ok" if call["ok"] else "failed"] += 1
            row["keys"].add(call["key"])
            row["seconds"] += call["seconds"]
            if not call["ok"] and call["error"]:
                row["errors"][call["error"]] = row["errors"].get(call["error"], 0) + 1

        api = []
        for row in grouped.values():
            row["keys"] = sorted(row["keys"])
            row["seconds"] = round(row["seconds"], 1)
            api.append(row)

        return {
            "status": status,
            "error": error[:500] or None,
            "started_at": self.started_at,
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "seconds": round(time.time() - self.started, 1),
            "stages": [s for s in self.stages if s["name"]],
            "models": self.models,
            "fallbacks": self.fallbacks,
            "api": api,
            **self.facts,
        }


def summarise(diagnostics: dict) -> str:
    """把一筆紀錄印成人看得懂的樣子，給 status.py 用。"""
    if not diagnostics:
        return "（沒有紀錄）"

    total = diagnostics.get("seconds") or 0
    lines = [f"總共 {total/60:.1f} 分"]

    audio = diagnostics.get("audio_seconds")
    if audio:
        lines[0] += f"（音檔 {audio/60:.1f} 分，比率 {total/audio:.2f}×）"

    models = diagnostics.get("models") or {}
    if models:
        lines.append("  模型：" + "、".join(
            f"{role}={name if isinstance(name, str) else '+'.join(map(str, name))}"
            for role, name in models.items()))

    for stage in diagnostics.get("stages") or []:
        share = stage["seconds"] / total * 100 if total else 0
        bar = "█" * max(1, round(share / 5))
        lines.append(f"  {stage['name']:<10}{stage['seconds']:>7.1f} 秒 "
                     f"{share:>4.0f}%  {bar}")

    for reason in diagnostics.get("fallbacks") or []:
        lines.append(f"  ⚠ 換手：{reason[:88]}")

    for row in diagnostics.get("api") or []:
        note = f"  {row['provider']}/{row['model']}：{row['ok']} 成功"
        if row["failed"]:
            note += f" {row['failed']} 失敗"
        note += f"，金鑰 {row['keys']}，{row['seconds']:.0f} 秒"
        lines.append(note)
        for reason, count in sorted((row.get("errors") or {}).items(),
                                    key=lambda kv: -kv[1])[:3]:
            lines.append(f"      ×{count} {reason}")

    return "\n".join(lines)
