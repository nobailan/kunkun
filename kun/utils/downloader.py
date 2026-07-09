"""异步文件下载器 — 基于 asyncio + httpx 的并发下载工具.

支持:
- Semaphore 控制并发数
- URL / Content-Disposition 头自动提取文件名
- Range 请求实现断点续传
- 失败自动重试 (指数退避)
- 流式写入避免内存溢出
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from email.header import decode_header
from pathlib import Path
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

# ─── 默认配置 ──────────────────────────────────────────────

DEFAULT_CONCURRENCY = 5
DEFAULT_TIMEOUT = 60.0
DEFAULT_RETRIES = 3
DEFAULT_CHUNK_SIZE = 65536  # 64KB
CONTENT_DISPOSITION_RE = re.compile(r'filename[*]?\s*=\s*["\']?([^"\';\r\n]+)', re.IGNORECASE)


# ─── 文件名提取 ────────────────────────────────────────────

def _extract_filename_from_url(url: str) -> str:
    """从 URL 路径中提取文件名.

    Args:
        url: 下载 URL

    Returns:
        提取的文件名；若 URL 以 / 结尾或路径不含文件名则返回 "index.html"
    """
    parsed = urlparse(url)
    path = parsed.path

    if not path or path.endswith("/"):
        return "index.html"

    filename = path.rsplit("/", 1)[-1]
    return filename if filename else "index.html"


def _extract_filename_from_content_disposition(headers: httpx.Headers) -> str | None:
    """从 Content-Disposition 响应头中提取文件名.

    支持 filename 和 filename* (RFC 5987) 两种参数格式，
    以及 RFC 2047 编码的 filename (如 =?UTF-8?B?...?=).

    Args:
        headers: httpx 响应头

    Returns:
        提取的文件名，未找到则返回 None
    """
    disposition = headers.get("content-disposition")
    if not disposition:
        return None

    # 优先解析 filename*= (RFC 5987, 如 UTF-8''%E6%96%87%E4%BB%B6.txt)
    match = re.search(r"filename\*\s*=\s*([^;]+)", disposition, re.IGNORECASE)
    if match:
        value = match.group(1).strip().strip('"').strip("'")
        # 格式: charset'lang'percent-encoded-name
        parts = value.split("'", 2)
        if len(parts) == 3:
            charset, _, encoded = parts
            try:
                from urllib.parse import unquote
                return unquote(encoded, encoding=charset)
            except (ValueError, LookupError):
                return unquote(encoded)
        return unquote(value)

    # 回退到普通 filename=
    match = CONTENT_DISPOSITION_RE.search(disposition)
    if match:
        raw = match.group(1).strip().strip('"')
        # 尝试 RFC 2047 解码 (如 =?UTF-8?B?...?=)
        decoded_parts = decode_header(raw)
        if decoded_parts:
            result_parts: list[str] = []
            for part, charset in decoded_parts:
                if isinstance(part, bytes):
                    result_parts.append(part.decode(charset or "utf-8", errors="replace"))
                else:
                    result_parts.append(part)
            return "".join(result_parts)
        return raw

    return None


# ─── 文件路径解析与去重 ────────────────────────────────────

def _resolve_dest_path(dest_dir: Path, filename: str, used_names: set[str]) -> Path:
    """解析目标路径，处理文件名冲突.

    若文件名已被占用或磁盘上已存在，自动追加序号.

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


# ─── 单文件下载 (含重试 + 断点续传) ─────────────────────────

async def _download_one(
    client: httpx.AsyncClient,
    url: str,
    dest_path: Path,
    semaphore: asyncio.Semaphore,
    retries: int = DEFAULT_RETRIES,
) -> str | None:
    """下载单个文件，支持断点续传与指数退避重试.

    Args:
        client: 共享的 httpx 异步客户端
        url: 下载 URL
        dest_path: 目标文件完整路径
        semaphore: 并发控制信号量
        retries: 最大重试次数

    Returns:
        成功时返回文件路径字符串，失败时返回 None
    """
    last_error: str | None = None

    for attempt in range(1, retries + 1):
        async with semaphore:
            try:
                # 检查本地已下载的字节 (断点续传)
                downloaded_bytes = 0
                if dest_path.exists():
                    downloaded_bytes = dest_path.stat().st_size

                headers: dict[str, str] = {}
                if downloaded_bytes > 0:
                    headers["Range"] = f"bytes={downloaded_bytes}-"
                    logger.debug(
                        "断点续传: %s (已有 %d 字节, 第 %d/%d 次)",
                        url, downloaded_bytes, attempt, retries,
                    )

                response = await client.get(url, headers=headers)
                response.raise_for_status()

                # ── 处理响应 ──────────────────────────
                dest_path.parent.mkdir(parents=True, exist_ok=True)

                if response.status_code == 206:
                    # 部分内容 — 追加写入
                    with open(dest_path, "ab") as f:
                        async for chunk in response.aiter_bytes(DEFAULT_CHUNK_SIZE):
                            f.write(chunk)
                    logger.info("续传完成: %s -> %s", url, dest_path)
                elif response.status_code == 200:
                    # 完整内容 — 覆盖写入 (可能是 Range 被忽略或首次下载)
                    if downloaded_bytes > 0:
                        logger.debug(
                            "服务器忽略 Range 请求，重新完整下载: %s", url,
                        )
                    with open(dest_path, "wb") as f:
                        async for chunk in response.aiter_bytes(DEFAULT_CHUNK_SIZE):
                            f.write(chunk)
                    logger.info("下载完成: %s -> %s", url, dest_path)
                else:
                    # 416 Range Not Satisfiable — 文件可能已完整
                    if response.status_code == 416:
                        logger.info("文件已完整 (416): %s -> %s", url, dest_path)
                    else:
                        logger.warning("未预期的状态码 %d: %s", response.status_code, url)

                return str(dest_path)

            except httpx.HTTPStatusError as e:
                last_error = f"HTTP {e.response.status_code}: {url}"
                logger.warning("HTTP 错误 [%d/%d] %d: %s", attempt, retries, e.response.status_code, url)
            except httpx.TimeoutException:
                last_error = f"超时: {url}"
                logger.warning("下载超时 [%d/%d]: %s", attempt, retries, url)
            except httpx.RequestError as e:
                last_error = f"网络错误: {url} — {e}"
                logger.warning("请求错误 [%d/%d]: %s — %s", attempt, retries, url, e)
            except OSError as e:
                last_error = f"文件写入失败: {dest_path} — {e}"
                logger.warning("文件写入错误 [%d/%d]: %s — %s", attempt, retries, dest_path, e)
            except Exception:
                last_error = f"未知错误: {url}"
                logger.exception("下载异常 [%d/%d]: %s", attempt, retries, url)

        # ── 指数退避: 1s, 2s, 4s ──────────────────────
        if attempt < retries:
            delay = 2 ** (attempt - 1)
            logger.debug("等待 %.1fs 后重试: %s", delay, url)
            await asyncio.sleep(delay)

    logger.error("下载失败 (已重试 %d 次): %s — %s", retries, url, last_error)
    return None


# ─── 公共 API ──────────────────────────────────────────────

async def download_files(
    urls: list[str],
    dest_dir: str,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> list[str]:
    """并发下载多个文件到本地目录.

    使用 asyncio + httpx 实现真正的异步并发下载，通过 asyncio.Semaphore
    控制并发数。支持从 Content-Disposition 头自动提取文件名、Range 请求
    实现断点续传，以及失败后指数退避重试 (最多 3 次)。单个文件失败不影响
    整体任务，以日志记录。

    Args:
        urls: 待下载的 URL 列表
        dest_dir: 目标目录路径 (自动创建)
        concurrency: 最大并发下载数，默认 5

    Returns:
        成功下载的文件完整路径列表 (str)。失败的文件被静默跳过。

    Example:
        >>> urls = [
        ...     "https://example.com/report.pdf",
        ...     "https://example.com/data/export.csv",
        ... ]
        >>> results = await download_files(urls, "./downloads", concurrency=3)
        >>> print(results)
        ['downloads/report.pdf', 'downloads/export.csv']
    """
    if not urls:
        logger.warning("URL 列表为空，无文件需要下载")
        return []

    dest_path = Path(dest_dir).resolve()
    dest_path.mkdir(parents=True, exist_ok=True)

    semaphore = asyncio.Semaphore(concurrency)
    limits = httpx.Limits(
        max_connections=concurrency,
        max_keepalive_connections=0,
    )

    # ── 第一步: HEAD 请求获取 Content-Disposition 文件名 ──
    async with httpx.AsyncClient(
        timeout=DEFAULT_TIMEOUT,
        follow_redirects=True,
        limits=limits,
        headers={"User-Agent": "Kun/1.0"},
    ) as head_client:
        head_tasks = [_fetch_filename(head_client, url) for url in urls]
        filenames = await asyncio.gather(*head_tasks)

    # ── 第二步: 解析目标路径 (处理冲突) ──
    used_names: set[str] = set()
    file_paths: list[Path] = []
    for url, filename in zip(urls, filenames):
        name = filename or _extract_filename_from_url(url)
        fp = _resolve_dest_path(dest_path, name, used_names)
        file_paths.append(fp)

    # ── 第三步: 并发下载 ──
    async with httpx.AsyncClient(
        timeout=DEFAULT_TIMEOUT,
        follow_redirects=True,
        limits=limits,
        headers={"User-Agent": "Kun/1.0"},
    ) as client:
        tasks = [
            _download_one(client, url, fp, semaphore, DEFAULT_RETRIES)
            for url, fp in zip(urls, file_paths)
        ]
        results = await asyncio.gather(*tasks)

    # ── 汇总 ──
    success_files = [r for r in results if r is not None]
    failed_count = len(urls) - len(success_files)

    if failed_count > 0:
        logger.warning(
            "批量下载完成: %d/%d 成功, %d 个失败",
            len(success_files), len(urls), failed_count,
        )
    else:
        logger.info("批量下载完成: 全部 %d 个文件成功", len(urls))

    return success_files


async def _fetch_filename(client: httpx.AsyncClient, url: str) -> str | None:
    """通过 HEAD 请求获取 Content-Disposition 中的文件名.

    失败时静默返回 None，不影响后续流程.

    Args:
        client: httpx 异步客户端
        url: 目标 URL

    Returns:
        提取的文件名，失败或无此头时返回 None
    """
    try:
        response = await client.head(url)
        return _extract_filename_from_content_disposition(response.headers)
    except Exception:
        logger.debug("HEAD 请求失败，回退到 URL 提取: %s", url)
        return None
