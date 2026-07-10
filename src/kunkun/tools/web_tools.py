"""Web 工具 — 网络搜索 + 网页抓取.

v0.4.1: WebSearch + WebFetch
DSv4 适配: 中文优先搜索，结果结构化返回
"""

from __future__ import annotations

import logging
import re
import json
from typing import Optional

import httpx
from pydantic import BaseModel, Field

from kunkun.core.state import ToolResult
from kunkun.tools.decorators import tool, ToolUseContext

logger = logging.getLogger(__name__)

FETCH_TIMEOUT = 30
MAX_CONTENT_LEN = 50_000


def _get_env(key: str) -> str:
    """读取环境变量."""
    import os
    return os.environ.get(key, "").strip()


def _get_proxy() -> str | None:
    """读取 Kun 专用代理设置."""
    val = _get_env("KUN_PROXY")
    return val if val else None

# ─── WebFetch ──────────────────────────────────────────


class WebFetchInput(BaseModel):
    """webfetch 工具输入参数."""

    url: str = Field(description="要抓取的网页 URL (支持 http/https)")
    prompt: str = Field(
        default="",
        description="针对抓取内容的提取问题, 留空则返回全文",
    )


@tool(
    name="webfetch",
    description=(
        "抓取网页内容并转换为纯文本。用于阅读在线文档、GitHub issue、博客文章等。"
        "返回页面正文（去除 HTML 标签和脚本）。"
    ),
    permission="read",
    input_model=WebFetchInput,
)
async def webfetch_tool(args: WebFetchInput, ctx: ToolUseContext) -> ToolResult:
    """抓取网页."""
    url = args.url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        proxy = _get_proxy()
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=True, proxy=proxy) as client:
            response = await client.get(
                url,
                headers={
                    "User-Agent": "Kunkun/0.4 (DeepSeek-native Agent)",
                    "Accept": "text/html,application/xhtml+xml,*/*",
                },
            )

            if response.status_code != 200:
                return ToolResult(
                    data=f"❌ HTTP {response.status_code}: {response.reason_phrase}",
                    is_error=True,
                )

            content_type = response.headers.get("content-type", "")
            text = response.text

    except httpx.TimeoutException:
        return ToolResult(data=f"❌ 请求超时 ({FETCH_TIMEOUT}s): {url}", is_error=True)
    except httpx.ConnectError as e:
        return ToolResult(data=f"❌ 连接失败: {e}", is_error=True)
    except Exception as e:
        return ToolResult(data=f"❌ 抓取失败: {e}", is_error=True)

    # ── HTML → 纯文本 ──
    if "text/html" in content_type or url.endswith((".html", ".htm")):
        text = _html_to_text(text)

    # 截断
    if len(text) > MAX_CONTENT_LEN:
        text = text[:MAX_CONTENT_LEN] + "\n\n... (内容过长，已截断)"

    # ── LLM 提取 ──
    if args.prompt.strip():
        extracted = await _llm_extract(text[:15000], args.prompt, ctx)
        if extracted:
            return ToolResult(data=f"📄 {url}\n\n{extracted}")
        # fallback to keyword extraction
        relevant = _extract_relevant(text, args.prompt)
        if relevant:
            return ToolResult(data=f"📄 {url}\n\n搜索 '{args.prompt}':\n\n{relevant}")

    return ToolResult(data=f"📄 {url}\n\n{text[:10000]}")


# ─── WebSearch ─────────────────────────────────────────


class WebSearchInput(BaseModel):
    """websearch 工具输入参数."""

    query: str = Field(description="搜索关键词")
    num_results: int = Field(
        default=5,
        description="返回结果数量, 默认 5, 最大 10",
    )


@tool(
    name="websearch",
    description=(
        "搜索网页。返回标题、URL 和摘要。用于查找最新文档、API 参考、错误排查等。"
        "中文搜索优先。支持 Tavily (高速, 需 TAVILY_API_KEY) 和 DuckDuckGo (免费, 无需 Key)。"
    ),
    permission="read",
    input_model=WebSearchInput,
)
async def websearch_tool(args: WebSearchInput, ctx: ToolUseContext) -> ToolResult:
    """搜索网页.

    多后端自动降级: Tavily (需 API Key) → DuckDuckGo (免费).
    """
    query = args.query.strip()
    if not query:
        return ToolResult(data="❌ 搜索关键词不能为空", is_error=True)

    num = min(args.num_results, 10)
    proxy = _get_proxy()

    # ── 优先 Tavily (API 直连, 不走代理) ──
    tavily_key = _get_env("TAVILY_API_KEY")
    if tavily_key:
        result = await _tavily_search(query, num, tavily_key, proxy=None)
        if result:
            return result

    # ── Fallback: DuckDuckGo (需代理) ──
    return await _ddg_search(query, num, proxy)


# ─── Tavily ─────────────────────────────────────────────


async def _tavily_search(query: str, num: int, api_key: str, proxy: str | None) -> ToolResult | None:
    """Tavily 搜索 (AI 原生, 中文优化)."""
    try:
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, proxy=proxy) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": api_key,
                    "query": query,
                    "max_results": num,
                    "search_depth": "basic",
                    "include_domains": [],
                },
                headers={"Content-Type": "application/json"},
            )

            if response.status_code != 200:
                logger.debug("Tavily search failed: %s", response.status_code)
                return None

            data = response.json()
            results = data.get("results", [])

            if not results:
                return None

            lines = [f"🔍 Tavily 搜索: {query}\n"]
            for i, r in enumerate(results[:num], 1):
                lines.append(f"{i}. **{r.get('title', '')}**")
                lines.append(f"   {r.get('url', '')}")
                content = r.get("content", "")
                if content:
                    lines.append(f"   {content[:200]}")
                lines.append("")

            return ToolResult(data="\n".join(lines))

    except Exception as e:
        logger.debug("Tavily unavailable, falling back to DDG: %s", e)
        return None


# ─── DuckDuckGo ─────────────────────────────────────────


async def _ddg_search(query: str, num: int, proxy: str | None) -> ToolResult:
    """DuckDuckGo 搜索 (免费, 无需 Key)."""
    try:
        from urllib.parse import quote

        search_url = f"https://html.duckduckgo.com/html/?q={quote(query)}"

        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=True, proxy=proxy) as client:
            response = await client.get(
                search_url,
                headers={
                    "User-Agent": "Kunkun/0.4 (DeepSeek-native Agent)",
                    "Accept": "text/html",
                },
            )

            if response.status_code != 200:
                return ToolResult(
                    data=f"❌ 搜索失败: HTTP {response.status_code}。"
                         f"可设置 TAVILY_API_KEY 启用高速搜索。",
                    is_error=True,
                )

            results = _parse_ddg_results(response.text, num)

    except Exception as e:
        return ToolResult(
            data=f"❌ DuckDuckGo 搜索失败: {e}。可设置 TAVILY_API_KEY 启用高速搜索。",
            is_error=True,
        )

    if not results:
        return ToolResult(data=f"🔍 未找到 '{query}' 的搜索结果。")

    lines = [f"🔍 搜索: {query}\n"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. **{r['title']}**")
        lines.append(f"   {r['url']}")
        if r.get("snippet"):
            lines.append(f"   {r['snippet'][:200]}")
        lines.append("")

    return ToolResult(data="\n".join(lines))


# ─── HTML 处理 ─────────────────────────────────────────


async def _llm_extract(text: str, prompt: str, ctx: ToolUseContext) -> str | None:
    """使用 flash 模型从文本中提取相关信息.

    借鉴 Claude Code WebFetch: 用轻模型回答 prompt, 而非返回全文.
    """
    api_key = ctx.metadata.get("api_key", "")
    base_url = ctx.metadata.get("base_url", "https://api.deepseek.com")
    light_model = ctx.metadata.get("light_model", "deepseek-v4-flash")

    if not api_key:
        return None  # 静默降级

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            response = await client.post(
                f"{base_url}/v1/chat/completions",
                json={
                    "model": light_model,
                    "messages": [{
                        "role": "user",
                        "content": (
                            f"根据以下网页内容回答问题。用中文回答，简洁准确。\n\n"
                            f"问题: {prompt}\n\n"
                            f"网页内容:\n{text}"
                        ),
                    }],
                    "max_tokens": 1024,
                    "temperature": 0.3,
                },
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )

            if response.status_code != 200:
                return None

            data = response.json()
            return data["choices"][0]["message"]["content"]

    except Exception as e:
        logger.debug("LLM extract failed (fallback to keyword): %s", e)
        return None


def _html_to_text(html: str) -> str:
    """简单 HTML → 纯文本."""
    # 移除 script/style
    html = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<style[^>]*>.*?</style>', '', html, flags=re.DOTALL | re.IGNORECASE)
    html = re.sub(r'<head[^>]*>.*?</head>', '', html, flags=re.DOTALL | re.IGNORECASE)

    # 替换常见标签为换行
    html = re.sub(r'<br\s*/?>', '\n', html)
    html = re.sub(r'</?p[^>]*>', '\n', html)
    html = re.sub(r'</?div[^>]*>', '\n', html)
    html = re.sub(r'</?h[1-6][^>]*>', '\n', html)
    html = re.sub(r'</?li[^>]*>', '\n- ', html)
    html = re.sub(r'</?tr[^>]*>', '\n', html)
    html = re.sub(r'</?td[^>]*>', ' ', html)

    # 移除所有剩余标签
    html = re.sub(r'<[^>]+>', '', html)

    # 解码 HTML 实体
    html = html.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    html = html.replace('&quot;', '"').replace('&#x27;', "'").replace('&nbsp;', ' ')

    # 压缩连续空行
    html = re.sub(r'\n\s*\n\s*\n', '\n\n', html)
    html = re.sub(r' +', ' ', html)

    return html.strip()


def _extract_relevant(text: str, query: str) -> str:
    """从文本中提取与查询相关的段落."""
    paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
    if not paragraphs:
        return ""

    query_lower = query.lower()
    relevant = []

    for p in paragraphs:
        p_lower = p.lower()
        score = 0
        for word in query_lower.split():
            if word in p_lower:
                score += 1
        if score > 0:
            relevant.append((score, p))

    relevant.sort(key=lambda x: x[0], reverse=True)

    if not relevant:
        # 返回开头几段
        return '\n\n'.join(paragraphs[:3])

    return '\n\n'.join(p for _, p in relevant[:5])


def _parse_ddg_results(html: str, max_results: int) -> list[dict]:
    """解析 DuckDuckGo HTML 搜索结果."""
    results = []

    # 匹配每个结果块: 标题链接 + 摘要
    # DDG HTML 搜索结果用 class="result" 包裹
    result_blocks = re.split(r'class="result', html)[1:]

    for block in result_blocks[:max_results]:
        # 提取链接和标题
        link_match = re.search(r'<a[^>]*href="([^"]*)"[^>]*class="result__a"[^>]*>(.*?)</a>', block, re.DOTALL)
        if not link_match:
            link_match = re.search(r'<a[^>]*href="([^"]*)"[^>]*>(.*?)</a>', block, re.DOTALL)

        if not link_match:
            continue

        url = _clean_ddg_url(link_match.group(1))
        title = _html_to_text(link_match.group(2)).strip()

        # 提取摘要
        snippet_match = re.search(r'class="result__snippet"[^>]*>(.*?)</a>', block, re.DOTALL)
        if not snippet_match:
            snippet_match = re.search(r'class="result__snippet"[^>]*>(.*?)</(?:a|td)', block, re.DOTALL)

        snippet = _html_to_text(snippet_match.group(1)).strip() if snippet_match else ""

        if title and url:
            results.append({"title": title, "url": url, "snippet": snippet})

    return results


def _clean_ddg_url(url: str) -> str:
    """清理 DDG 重定向 URL."""
    # DDG 用 //duckduckgo.com/l/?uddg=REAL_URL 包装
    uddg_match = re.search(r'uddg=([^&]+)', url)
    if uddg_match:
        from urllib.parse import unquote
        return unquote(uddg_match.group(1))
    return url
