"""错误恢复 — API 错误分类 + 指数退避重试.

借鉴:
- cc-haha src/services/api/errors.ts — categorizeRetryableAPIError()
- cc-haha src/services/api/withRetry.ts — 指数退避重试
- AgenticRAG 区分错误类型 + 指数退避

设计:
- ErrorClassifier: 分类 HTTP 错误 -> retryable / fatal
- RetryPolicy: 指数退避 + jitter
- async_retry: async 函数重试装饰器
"""

from __future__ import annotations

import asyncio
import logging
import random
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Awaitable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# ─── 错误分类 ──────────────────────────────────────


class ErrorCategory(str, Enum):
    RETRYABLE = "retryable"  # 429, 5xx, timeout → 重试
    FATAL = "fatal"  # 4xx (不含 429), auth error → 不重试


# HTTP 状态码 → 重试策略
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
FATAL_STATUS = {400, 401, 402, 403, 404, 405, 408, 409, 422}

# 可重试的连接错误关键词
RETRYABLE_ERROR_MSGS = [
    "connection reset",
    "timeout",
    "timed out",
    "connection refused",
    "too many requests",
    "service unavailable",
    "internal server error",
    "bad gateway",
    "gateway timeout",
    "server error",
    "econnrefused",
    "econnreset",
    "etimedout",
    "eof",
    "broken pipe",
    "ssl_error_syscall",
]


class ErrorClassifier:
    """API 错误分类器.

    借鉴 cc-haha categorizeRetryableAPIError (src/services/api/errors.ts):
    - 429 → retryable (rate limit)
    - 5xx → retryable (server error)
    - 4xx (不含 429) → fatal (client error)
    - ConnectionError/TimeoutError → retryable
    - 其他 → fatal
    """

    @staticmethod
    def classify(error: Exception) -> ErrorCategory:
        """分类错误."""
        from httpx import HTTPStatusError, TimeoutException, ConnectError, RemoteProtocolError

        # HTTP 状态码错误
        if isinstance(error, HTTPStatusError):
            status = error.response.status_code
            if status in RETRYABLE_STATUS:
                logger.debug("HTTP %d → RETRYABLE", status)
                return ErrorCategory.RETRYABLE
            if status in FATAL_STATUS:
                logger.debug("HTTP %d → FATAL", status)
                return ErrorCategory.FATAL
            # 未知状态码: 5xx 默认重试，4xx 默认不重试
            if 500 <= status < 600:
                return ErrorCategory.RETRYABLE
            return ErrorCategory.FATAL

        # 超时 / 连接错误 → 重试
        if isinstance(error, (TimeoutException, ConnectError, RemoteProtocolError)):
            return ErrorCategory.RETRYABLE

        # 网络层错误
        if isinstance(error, (ConnectionError, TimeoutError, OSError)):
            return ErrorCategory.RETRYABLE

        # asyncio 超时
        if isinstance(error, asyncio.TimeoutError):
            return ErrorCategory.RETRYABLE

        # 字符串匹配 (兜底)
        error_str = str(error).lower()
        for pattern in RETRYABLE_ERROR_MSGS:
            if pattern in error_str:
                return ErrorCategory.RETRYABLE

        # 默认不重试
        return ErrorCategory.FATAL


# ─── 重试策略 ──────────────────────────────────────


@dataclass
class RetryPolicy:
    """指数退避重试策略.

    借鉴 cc-haha withRetry (src/services/api/withRetry.ts):
    - base_delay: 基础等待 (秒)
    - max_delay: 最大等待 (秒)
    - max_retries: 最大重试次数
    - jitter: 随机抖动因子 (0-1)

    延迟公式:
        delay = min(base_delay * 2^attempt + random(0, jitter), max_delay)
    例: base=1.0, max=60, jitter=0.5
        attempt 0: 1.0-1.5s
        attempt 1: 2.0-2.5s
        attempt 2: 4.0-4.5s
        attempt 3: 8.0-8.5s (超过 max_retries)
    """

    base_delay: float = 1.0
    max_delay: float = 60.0
    max_retries: int = 3
    jitter: float = 0.3

    def delay_for(self, attempt: int) -> float:
        """计算第 attempt 次重试前的等待时间."""
        delay = self.base_delay * (2 ** attempt)
        jitter_amount = random.uniform(0, self.jitter)
        return min(delay + jitter_amount, self.max_delay)

    def should_retry(self, attempt: int) -> bool:
        """是否应该继续重试."""
        return attempt < self.max_retries

    def retry_after(self, attempt: int) -> float:
        """返回建议的等待秒数 (供外部显示用)."""
        return self.delay_for(attempt)


# ─── 重试上下文 ────────────────────────────────────


@dataclass
class RetryContext:
    """重试上下文 — 跟踪重试状态."""

    attempts: int = 0
    errors: list[Exception] = field(default_factory=list)
    policy: RetryPolicy = field(default_factory=RetryPolicy)

    @property
    def last_error(self) -> Exception | None:
        return self.errors[-1] if self.errors else None

    @property
    def is_exhausted(self) -> bool:
        return self.attempts >= self.policy.max_retries


# ─── 重试执行器 ────────────────────────────────────


async def async_retry(
    fn: Callable[..., Awaitable[T]],
    *args,
    policy: RetryPolicy | None = None,
    on_retry: Callable[[int, Exception, float], None] | None = None,
    **kwargs,
) -> T:
    """执行 async 函数，失败时按策略重试.

    Args:
        fn: 要执行的 async 函数
        *args: 位置参数
        policy: 重试策略 (默认 RetryPolicy())
        on_retry: 重试回调 (attempt, error, delay_seconds)
        **kwargs: 关键字参数

    Returns:
        函数返回值

    Raises:
        最后一次失败的错误 (所有重试用尽后)
    """
    if policy is None:
        policy = RetryPolicy()

    ctx = RetryContext(policy=policy)
    last_error: Exception | None = None

    while True:
        try:
            return await fn(*args, **kwargs)
        except Exception as e:
            ctx.errors.append(e)
            ctx.attempts += 1
            last_error = e

            category = ErrorClassifier.classify(e)

            if category == ErrorCategory.FATAL:
                logger.warning("Fatal error (attempt %d), not retrying: %s", ctx.attempts, e)
                raise

            if not policy.should_retry(ctx.attempts - 1):
                logger.error(
                    "Retries exhausted (%d attempts). Last error: %s",
                    ctx.attempts, e,
                )
                raise

            delay = policy.retry_after(ctx.attempts - 1)
            logger.warning(
                "Retryable error (attempt %d/%d): %s. Waiting %.1fs...",
                ctx.attempts, policy.max_retries, e, delay,
            )

            if on_retry:
                on_retry(ctx.attempts, e, delay)

            await asyncio.sleep(delay)

    # 不应到达这里
    if last_error:
        raise last_error
