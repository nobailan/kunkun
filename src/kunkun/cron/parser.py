"""Cron 表达式解析器 — 纯 Python, 零依赖."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta


# ─── 字段范围 ─────────────────────────────────────────

_FIELD_RANGES = {
    "minute": (0, 59),
    "hour": (0, 23),
    "day": (1, 31),
    "month": (1, 12),
    "weekday": (0, 6),  # 0=Sunday
}

_FIELD_NAMES = ["minute", "hour", "day", "month", "weekday"]


def _parse_field(value: str, lo: int, hi: int) -> set[int]:
    """解析单个 cron 字段, 返回匹配值的集合."""
    result: set[int] = set()

    for part in value.split(","):
        part = part.strip()
        step = 1

        if "/" in part:
            part, step_str = part.split("/", 1)
            step = int(step_str)

        if part == "*":
            vals = set(range(lo, hi + 1))
        elif "-" in part:
            a, b = part.split("-", 1)
            vals = set(range(int(a), int(b) + 1))
        else:
            vals = {int(part)}

        if step > 1:
            vals = {v for v in vals if (v - min(vals)) % step == 0}

        result |= vals

    return result


@dataclass
class CronSchedule:
    """解析后的 cron 表达式."""

    minutes: set[int]
    hours: set[int]
    days: set[int]
    months: set[int]
    weekdays: set[int]

    def next_after(self, dt: datetime | None = None) -> datetime:
        """计算下一次触发时间."""
        dt = (dt or datetime.now()).replace(second=0, microsecond=0) + timedelta(minutes=1)
        for _ in range(366 * 24 * 60):  # 最多查一年
            if (
                dt.minute not in self.minutes
                or dt.hour not in self.hours
                or dt.day not in self.days
                or dt.month not in self.months
                or dt.weekday() not in self.weekdays
            ):
                dt += timedelta(minutes=1)
                continue
            return dt
        raise ValueError("cron: no match within 1 year")


def parse_cron(expression: str) -> CronSchedule:
    """解析 5 字段 cron 表达式.

    Args:
        expression: "分 时 日 月 周", 例如 "0 9 * * 1-5"

    Returns:
        CronSchedule

    Raises:
        ValueError: 格式错误
    """
    fields = expression.strip().split()
    if len(fields) != 5:
        raise ValueError(f"cron: expected 5 fields, got {len(fields)}: '{expression}'")

    parsed = {}
    for name, value in zip(_FIELD_NAMES, fields):
        lo, hi = _FIELD_RANGES[name]
        parsed[name] = _parse_field(value, lo, hi)

    return CronSchedule(
        minutes=parsed["minute"],
        hours=parsed["hour"],
        days=parsed["day"],
        months=parsed["month"],
        weekdays=parsed["weekday"],
    )
