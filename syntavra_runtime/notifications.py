from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping

from .util import canonical_json, sha256_bytes


@dataclass(frozen=True)
class Notification:
    channel: str
    severity: str
    title: str
    body: str
    created_at: float
    event_hash: str


class NotificationFeed:
    def __init__(self, state_root: Path):
        self.path = Path(state_root) / "notifications" / "events.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, *, channel: str, severity: str, title: str, body: str) -> Notification:
        base = {"channel": channel, "severity": severity, "title": title, "body": body, "created_at": time.time()}
        item = Notification(**base, event_hash=sha256_bytes(canonical_json(base)))
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(item), ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
        return item

    def recent(self, *, limit: int = 100, severity: str | None = None) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        if self.path.is_file():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if severity is None or row.get("severity") == severity:
                    rows.append(row)
        return rows[-max(1, limit):]

    @staticmethod
    def _post_json(url: str, payload: Mapping[str, Any], *, timeout: float = 10.0) -> dict[str, Any]:
        request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return {"ok": 200 <= response.status < 300, "status": response.status}
        except (urllib.error.URLError, TimeoutError) as exc:
            return {"ok": False, "error": type(exc).__name__}

    def deliver(self, notification: Notification, *, discord_webhook: str = "", telegram_bot_token: str = "", telegram_chat_id: str = "") -> dict[str, Any]:
        results: dict[str, Any] = {}
        text = f"[{notification.severity.upper()}] {notification.title}\n{notification.body}"
        if discord_webhook:
            results["discord"] = self._post_json(discord_webhook, {"content": text})
        if telegram_bot_token and telegram_chat_id:
            url = f"https://api.telegram.org/bot{telegram_bot_token}/sendMessage"
            results["telegram"] = self._post_json(url, {"chat_id": telegram_chat_id, "text": text})
        return results
