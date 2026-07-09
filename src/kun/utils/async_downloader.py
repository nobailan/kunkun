"""异步文件下载器 — 基于 asyncio + aiohttp 的并发下载工具.

支持:
- aiohttp 异步并发下载
- 可配置并发数 (Semaphore)
- 失败自动重试 (指数退避)
- rich 进度条实时显示
- 文件名自动提取与去重
- 单文件失败不影响整体任务
- 最终汇总失败信息
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from urllib.parse import urlparse

import aiohttp
from rich.progress import (
    BarColumn,
    DownloadColumn,
    Progress,
    TaskID,
    TextColumn,
    TimeRemainingColumn,
    TransferSpeedColumn,
)

logger = logging.getLogger(__name__)

# ─── 默认配置 ──────────────────────────────────────────────

DEFAULT_CONCURRENCY = 5
DEFAULT_RETRIES = 3
DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=60, connect=10)


def _extract_filename(url: str) -> str:
    """从 URL 中提取文件名.

    借鉴 downloader.py 的同名函数.

    Args:
        url: 下载 URL

    Returns:
        提取的文件名；若 URL 以 `/` 结尾或路径不含文件名则返回 "index.html"
    """
    parsed = urlparse(url)
    path = parsed.path

    if not path or path.endswith("/"):
        return "index.html"

    filename = path.rsplit("/", 1)[-1]
    if not filename:
        return "index.html"

    return filename


def _resolve_dest_path(dest_dir: Path, filename: str, used_names: set[str]) -> Path:
    """解析目标路径，处理文件名冲突.

    若文件名已存在或已被其他任务占用，自动追加序号.

    Args:
        dest_dir: 目标目录
        filename: 原始文件名
        used_names: 已被占用的文件名集合

    Returns:
        无冲突的目标文件路径
    """
    file_path = dest_dir / filename
    if filename not in used_names and not file_path.exists():
        used_names.add(filename)
        return file_path

    stem, suffix = os.path.splitext(filename)
    counter = 1
    while True:
        new_name = f"{stem}_{counter}{suffix}"
        new_path = dest_dir / new_name
        if new_name not in used_names and not new_path.exists():
            used_names.add(new_name)
            return new_path
        counter += 1


async def _download_one(
    session: aiohttp.ClientSession,
    url: str,
    dest_path: Path,
    semaphore: asyncio.Semaphore,
    retries: int,
    progress: Progress,
    task_id: TaskID,
) -> str | None:
    """下载单个文件 (含重试).

    Args:
        session: aiohttp 共享会话
        url: 下载 URL
        dest_path: 目标文件完整路径
        semaphore: 并发控制信号量
        retries: 最大重试次数
        progress: rich Progress 实例
        task_id: 进度条任务 ID

    Returns:
        成功时返回文件路径字符串，失败时返回 None
    """
    last_error: str | None = None

    for attempt in range(1, retries + 1):
        async with semaphore:
            try:
                logger.info(f"下载 [{attempt}/{retries}]: {url}")
                async with session.get(url, timeout=DEFAULT_TIMEOUT) as resp:
                    resp.raise_for_status()

                    dest_path.parent.mkdir(parents=True, exist_ok=True)
                    content = await resp.read()

                dest_path.write_bytes(content)
                logger.info(f"完成: {url} -> {dest_path}")
                progress.update(task_id, advance=1)
                return str(dest_path)

            except aiohttp.ClientResponseError as e:
                last_error = f"HTTP {e.status}: {url}"
                logger.warning(f"HTTP 错误 [{attempt}/{retries}]: {e.status} - {url}")
            except aiohttp.ClientError as e:
                last_error = f"连接错误: {url} - {e}"
                logger.warning(f"请求错误 [{attempt}/{retries}]: {url} - {e}")
            except Exception:
                last_error = f"未知错误: {url}"
                logger.exception(f"下载异常 [{attempt}/{retries}]: {url}")

        # 指数退避: 1s, 2s, 4s, ...
        if attempt < retries:
            delay = 2 ** (attempt - 1)
            logger.debug(f"重试等待 {delay}s: {url}")
            await asyncio.sleep(delay)

    logger.error(f"下载彻底失败 (已重试 {retries} 次): {url} — {last_error}")
    progress.update(task_id, advance=1)
    return None


async def download_files(
    urls: list[str],
    dest_dir: str = "./downloads",
    concurrency: int = DEFAULT_CONCURRENCY,
    retries: int = DEFAULT_RETRIES,
) -> list[str]:
    """并发下载多个文件到本地目录.

    使用 aiohttp + asyncio.Semaphore 控制并发，单个文件失败不影响其他任务。
    文件名从 URL 自动提取，冲突时追加序号去重。

    Args:
        urls: 待下载的 URL 列表
        dest_dir: 目标目录路径，默认 "./downloads"
        concurrency: 最大并发下载数，默认 5
        retries: 单文件最大重试次数，默认 3

    Returns:
        成功下载的文件完整路径列表

    Example:
        >>> urls = [
        ...     "https://example.com/data.csv",
        ...     "https://example.com/api/",
        ... ]
        >>> results = await download_files(urls, "./downloads", concurrency=3)
        >>> print(results)
        ['downloads/data.csv', 'downloads/index.html']
    """
    if not urls:
        logger.warning("URL 列表为空，无文件需要下载")
        return []

    dest_path = Path(dest_dir).resolve()
    dest_path.mkdir(parents=True, exist_ok=True)

    semaphore = asyncio.Semaphore(concurrency)
    used_names: set[str] = set()

    # ─── 预解析所有文件路径 (避免并发冲突) ─────────────────
    file_paths: list[Path] = []
    for url in urls:
        filename = _extract_filename(url)
        fp = _resolve_dest_path(dest_path, filename, used_names)
        file_paths.append(fp)

    # ─── 进度条 ───────────────────────────────────────────
    progress = Progress(
        TextColumn("[bold blue]{task.description}", justify="right"),
        BarColumn(),
        "[progress.percentage]{task.percentage:>3.0f}%",
        "•",
        DownloadColumn(),
        "•",
        TransferSpeedColumn(),
        "•",
        TimeRemainingColumn(),
    )
    task_id = progress.add_task("下载中", total=len(urls))

    # ─── 并发下载 ─────────────────────────────────────────
    async with aiohttp.ClientSession() as session:
        tasks = []
        for url, fp in zip(urls, file_paths):
            task = _download_one(session, url, fp, semaphore, retries, progress, task_id)
            tasks.append(task)

        with progress:
            results = await asyncio.gather(*tasks)

    # ─── 汇总结果 ─────────────────────────────────────────
    success_files = [r for r in results if r is not None]
    failed_count = len(urls) - len(success_files)

    if failed_count > 0:
        logger.warning(
            f"下载完成: {len(success_files)}/{len(urls)} 成功, "
            f"{failed_count} 个失败"
        )
    else:
        logger.info(f"下载完成: 全部 {len(urls)} 个文件成功")

    return success_files
