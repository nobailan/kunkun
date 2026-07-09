"""异步并发文件下载器.

使用 asyncio + httpx 实现高并发文件批量下载，支持并发控制与错误隔离.

借鉴:
- web_tools.py — httpx.AsyncClient 使用模式、超时/代理风格
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 60
DEFAULT_MAX_CONCURRENCY = 5
DEFAULT_USER_AGENT = "Kunkun/0.7 (DeepSeek-native Agent)"


def _extract_filename(url: str) -> str:
    """从 URL 中提取文件名.

    Args:
        url: 下载地址

    Returns:
        提取的文件名，若无法提取或 URL 以 / 结尾则返回 "index.html"
    """
    path = urlparse(url).path
    if not path or path.endswith("/"):
        return "index.html"
    filename = path.rsplit("/", 1)[-1]
    return filename if filename else "index.html"


async def _download_one(
    client: httpx.AsyncClient,
    url: str,
    dest_path: Path,
    semaphore: asyncio.Semaphore,
) -> str | None:
    """下载单个文件（受信号量控制）.

    Args:
        client: 共享的 httpx 客户端实例
        url: 下载地址
        dest_path: 目标文件完整路径
        semaphore: 并发控制信号量

    Returns:
        成功时返回文件路径字符串，失败时返回 None
    """
    async with semaphore:
        try:
            response = await client.get(url)
            response.raise_for_status()

            dest_path.parent.mkdir(parents=True, exist_ok=True)

            # 流式写入，避免大文件撑爆内存
            with open(dest_path, "wb") as f:
                async for chunk in response.aiter_bytes(chunk_size=65536):
                    f.write(chunk)

            logger.info("下载成功: %s -> %s", url, dest_path)
            return str(dest_path)

        except httpx.HTTPStatusError as e:
            logger.warning("HTTP 错误 %d: %s — %s", e.response.status_code, url, e)
        except httpx.TimeoutException:
            logger.warning("下载超时: %s", url)
        except httpx.RequestError as e:
            logger.warning("网络请求失败: %s — %s", url, e)
        except OSError as e:
            logger.warning("文件写入失败: %s — %s", dest_path, e)
        except Exception:
            logger.exception("下载异常: %s", url)

        return None


async def download_files(
    urls: list[str],
    dest_dir: str,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
) -> list[str]:
    """并发下载多个文件到本地目录.

    使用 asyncio + httpx 并发下载，通过 Semaphore 控制并发数。
    单个文件下载失败不影响整体任务，以日志记录。

    Args:
        urls: 待下载的 URL 列表
        dest_dir: 目标目录路径
        max_concurrency: 最大并发数，默认 5

    Returns:
        成功下载的文件路径列表（仅包含成功的，失败的被静默跳过）
    """
    if not urls:
        return []

    dest = Path(dest_dir)
    semaphore = asyncio.Semaphore(max_concurrency)

    limits = httpx.Limits(max_connections=max_concurrency, max_keepalive_connections=0)
    async with httpx.AsyncClient(
        timeout=DEFAULT_TIMEOUT,
        follow_redirects=True,
        limits=limits,
        headers={"User-Agent": DEFAULT_USER_AGENT},
    ) as client:
        tasks = [
            _download_one(client, url, dest / _extract_filename(url), semaphore)
            for url in urls
        ]
        results = await asyncio.gather(*tasks)

    # 过滤失败的 None
    downloaded = [path for path in results if path is not None]
    logger.info("批量下载完成: %d/%d 成功", len(downloaded), len(urls))
    return downloaded
