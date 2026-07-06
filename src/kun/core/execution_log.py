"""执行日志 — 完整事件时间线 → JSON 持久化.

借鉴:
- cc-haha session persistence / transcript
- FlowForge 评估引擎的 EventBus 模式
- Hermes 执行日志

设计:
- ExecutionLogger: 收集事件 + 持久化
- 每次会话生成 .kun/reports/{session_id}.json
- 包含完整的事件时间线、token 统计、工具调用记录
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from kun.core.events import Event, EventType

logger = logging.getLogger(__name__)


class ExecutionLogger:
    """执行日志记录器.

    借鉴 cc-haha transcript + FlowForge EventBus:
    - record(): 记录单个事件
    - flush(): 持久化到 JSON
    - load(): 加载历史日志
    - summary(): 生成统计摘要
    """

    def __init__(self, report_dir: str = ".kun/reports", session_id: str = ""):
        self.report_dir = Path(report_dir)
        self.session_id = session_id
        self.events: list[dict] = []
        self.start_time: float = datetime.now().timestamp()
        self._metadata: dict[str, Any] = {}

    def set_metadata(self, key: str, value: Any) -> None:
        """设置会话元数据."""
        self._metadata[key] = value

    def record(self, event: Event) -> None:
        """记录单个事件.

        Args:
            event: Agent Loop 产出的事件
        """
        self.events.append({
            "type": event.type.value,
            "data": event.data,
            "timestamp": event.timestamp,
            "turn": event.turn_number,
        })

    def event_count(self) -> int:
        """已记录事件数."""
        return len(self.events)

    def flush(self) -> Path:
        """持久化到 JSON 文件.

        Returns:
            输出文件路径
        """
        self.report_dir.mkdir(parents=True, exist_ok=True)

        elapsed = datetime.now().timestamp() - self.start_time

        # 统计
        tool_calls = sum(
            1 for e in self.events if e["type"] == EventType.TOOL_USE.value
        )
        errors = sum(
            1 for e in self.events if e["type"] == EventType.ERROR.value
        )

        report = {
            "session_id": self.session_id,
            "started_at": datetime.fromtimestamp(self.start_time).isoformat(),
            "elapsed_seconds": round(elapsed, 2),
            "metadata": self._metadata,
            "summary": {
                "total_events": len(self.events),
                "tool_calls": tool_calls,
                "errors": errors,
            },
            "events": self.events,
        }

        filename = self.report_dir / f"{self.session_id}.json"
        try:
            filename.write_text(
                json.dumps(report, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info("Execution log saved: %s (%d events)", filename, len(self.events))
        except Exception as e:
            logger.error("Failed to write execution log: %s", e)

        return filename

    @staticmethod
    def load(report_dir: str, session_id: str) -> dict | None:
        """加载历史日志.

        Args:
            report_dir: 报告目录
            session_id: 会话 ID

        Returns:
            日志字典，不存在返回 None
        """
        path = Path(report_dir) / f"{session_id}.json"
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning("Failed to load execution log %s: %s", path, e)
            return None

    @staticmethod
    def list_sessions(report_dir: str) -> list[dict]:
        """列出所有历史会话.

        Returns:
            [{"session_id": ..., "started_at": ..., "elapsed": ...}, ...]
        """
        rd = Path(report_dir)
        if not rd.is_dir():
            return []

        sessions = []
        for f in sorted(rd.glob("*.json"), reverse=True):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                sessions.append({
                    "session_id": data.get("session_id", f.stem),
                    "started_at": data.get("started_at", ""),
                    "elapsed_seconds": data.get("elapsed_seconds", 0),
                    "total_events": data.get("summary", {}).get("total_events", 0),
                    "tool_calls": data.get("summary", {}).get("tool_calls", 0),
                    "errors": data.get("summary", {}).get("errors", 0),
                })
            except Exception:
                continue

        return sessions
