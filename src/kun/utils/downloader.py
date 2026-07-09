"""异步文件下载器 — 基于 asyncio + httpx 的并发下载工具.

支持:
- Semaphore 控制并发数
- URL 文件名自动提取
- 下载失败容错，不中断整体任务
- 重定向跟随
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

DEFAULT_MAX_CONCURRENCY = 5
DEFAULT_TIMEOUT = 30.0


def _extract_filename(url: str) -> str:
    """从 URL 中提取文件名.

    Args:
        url: 下载 URL

    Returns:
        提取的文件名；若 URL 以 `/` 结尾或路径无文件名则返回 "index.html"
    """
    parsed = urlparse(url)
    path = parsed.path

    if not path or path.endswith("/"):
        return "index.html"

    # 去除可能的 query string 和 fragment (urlparse 已处理，但保底)
    filename = path.rsplit("/", 1)[-1]
    if not filename:
        return "index.html"

    return filename


async def _download_one(
    client: httpx.AsyncClient,
    url: str,
    dest_path: Path,
    semaphore: asyncio.Semaphore,
) -> str | None:
    """下载单个文件.

    Args:
        client: httpx 异步客户端
        url: 下载 URL
        dest_path: 目标文件完整路径
        semaphore: 并发控制信号量

    Returns:
        成功时返回文件路径字符串，失败时返回 None
    """
    async with semaphore:
        try:
            logger.info(f"开始下载: {url}")
            response = await client.get(url, follow_redirects=True)
            response.raise_for_status()

            # 确保父目录存在
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            dest_path.write_bytes(response.content)

            logger.info(f"下载完成: {url} -> {dest_path}")
            return str(dest_path)

        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP 错误 {e.response.status_code}: {url}")
            return None
        except httpx.RequestError as e:
            logger.error(f"请求错误: {url} - {e}")
            return None
        except Exception:
            logger.exception(f"下载失败 (未知错误): {url}")
            return None


async def download_files(
    urls: list[str],
    dest_dir: str,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
) -> list[str]:
    """并发下载多个文件到本地目录.

    使用 asyncio.Semaphore 控制并发数，单个文件下载失败不影响其他任务。
    文件名从 URL 中提取，如果 URL 以 `/` 结尾则默认使用 "index.html"。

    Args:
        urls: 待下载的 URL 列表
        dest_dir: 目标目录路径
        max_concurrency: 最大并发下载数，默认 5

    Returns:
        成功下载的文件完整路径列表 (str)

    Example:
        >>> urls = [
        ...     "https://example.com/data.csv",
        ...     "https://example.com/api/",
        ... ]
        >>> results = await download_files(urls, "./downloads", max_concurrency=3)
        >>> print(results)
        ['downloads/data.csv', 'downloads/index.html']
    """
    if not urls:
        logger.warning("URL 列表为空，无文件需要下载")
        return []

    dest_path = Path(dest_dir).resolve()
    dest_path.mkdir(parents=True, exist_ok=True)

    semaphore = asyncio.Semaphore(max_concurrency)
    timeout = httpx.Timeout(DEFAULT_TIMEOUT)

    async with httpx.AsyncClient(timeout=timeout) as client:
        tasks = []
        for url in urls:
            filename = _extract_filename(url)
            file_path = dest_path / filename

            # 处理文件名冲突：添加序号
            if file_path.exists():
                stem, suffix = os.path.splitext(filename)
                counter = 1
                while file_path.exists():
                    file_path = dest_path / f"{stem}_{counter}{suffix}"
                    counter += 1

            task = _download_one(client, url, file_path, semaphore)
            tasks.append(task)

        results = await asyncio.gather(*tasks)

    # 过滤掉失败的 (None)
    success_files = [r for r in results if r is not None]

    logger.info(
        f"下载完成: {len(success_files)}/{len(urls)} 成功"
    )

    return success_files
