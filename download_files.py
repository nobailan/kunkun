from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Callable

import aiohttp
from aiohttp import ClientSession


async def _download_one(
    session: ClientSession,
    url: str,
    dest: Path,
    progress: Callable[[str, int, int | None], None] | None = None,
) -> tuple[str, bool, str]:
    """下载单个文件。

    Args:
        session: aiohttp 会话
        url: 文件 URL
        dest: 本地目标路径
        progress: 进度回调 (url, downloaded_bytes, total_bytes_or_None)

    Returns:
        (url, success, error_message)
    """
    try:
        async with session.get(url, timeout=aiohttp.ClientTimeout(total=300)) as resp:
            resp.raise_for_status()

            total = resp.content_length
            downloaded = 0

            dest.parent.mkdir(parents=True, exist_ok=True)

            with open(dest, "wb") as f:
                async for chunk in resp.content.iter_chunked(8192):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if progress:
                        progress(url, downloaded, total)

        return (url, True, "")

    except Exception as e:
        return (url, False, str(e))


async def download_files(
    urls: list[str],
    dest_dir: str | Path,
    *,
    concurrency: int = 5,
    progress: Callable[[str, int, int | None], None] | None = None,
) -> dict[str, str | None]:
    """并发下载多个文件。

    Args:
        urls: 要下载的文件 URL 列表
        dest_dir: 保存目录
        concurrency: 最大并发数
        progress: 进度回调 (url, downloaded_bytes, total_bytes_or_None)

    Returns:
        {url: local_path_or_None} — None 表示下载失败
    """
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)

    semaphore = asyncio.Semaphore(concurrency)

    async def _download_with_limit(
        session: ClientSession,
        url: str,
        dest: Path,
    ) -> tuple[str, str | None]:
        async with semaphore:
            url, ok, err = await _download_one(session, url, dest, progress)
            return url, str(dest) if ok else None

    tasks: list[asyncio.Task[tuple[str, str | None]]] = []

    async with aiohttp.ClientSession() as session:
        for url in urls:
            filename = Path(url.rsplit("/", 1)[-1].split("?")[0]) or "download"
            dest = dest_dir / filename
            tasks.append(
                asyncio.create_task(_download_with_limit(session, url, dest))
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)

    output: dict[str, str | None] = {}
    for result in results:
        if isinstance(result, Exception):
            continue
        url, path = result
        output[url] = path

    return output
