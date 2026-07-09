"""Markdown 文件分析器 — Pro 模式 (先分析设计，再逐步实现).

架构:
    CLI → FileCollector → FileAnalyzer → ReportBuilder → 输出

v0.1: 核心功能 — 扫描、解析、汇总、报告
"""

from __future__ import annotations

import argparse
import logging
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Counter

# ─── 日志配置 ──────────────────────────────────────────────

logger = logging.getLogger("md_analyzer")
_handler = logging.StreamHandler(sys.stdout)
_handler.setFormatter(logging.Formatter(
    fmt="[%(levelname)-5s] %(message)s",
))
logger.addHandler(_handler)
logger.setLevel(logging.INFO)


# ─── 数据模型 ──────────────────────────────────────────────

@dataclass
class FileStats:
    """单个 Markdown 文件的统计指标.

    Attributes:
        filepath: 文件路径
        total_lines: 总行数
        blank_lines: 空白行数
        code_lines: 代码块内行数
        text_lines: 纯文本行数 (非空、非代码)
        char_count: 总字符数
        word_count: 总词数 (按空白分割)
        link_count: Markdown 链接数 [text](url)
        image_count: Markdown 图片数 ![alt](url)
        code_block_count: 代码块数量 (``` pairs)
        heading_count: 标题总数
        headings_by_level: 各级标题计数 {level: count}
    """

    filepath: Path
    total_lines: int = 0
    blank_lines: int = 0
    code_lines: int = 0
    text_lines: int = 0
    char_count: int = 0
    word_count: int = 0
    link_count: int = 0
    image_count: int = 0
    code_block_count: int = 0
    heading_count: int = 0
    headings_by_level: dict[int, int] = field(default_factory=dict)


@dataclass
class SummaryReport:
    """多文件汇总报告.

    Attributes:
        files_scanned: 扫描文件总数
        total_lines: 总行数
        total_chars: 总字符数
        total_words: 总词数
        total_links: 总链接数
        total_images: 总图片数
        total_code_blocks: 总代码块数
        total_headings: 总标题数
        per_file: 逐文件详情列表
    """

    files_scanned: int = 0
    total_lines: int = 0
    total_chars: int = 0
    total_words: int = 0
    total_links: int = 0
    total_images: int = 0
    total_code_blocks: int = 0
    total_headings: int = 0
    per_file: list[FileStats] = field(default_factory=list)


# ─── FileCollector: 目录扫描 ───────────────────────────────

class FileCollector:
    """负责递归扫描目录，收集 .md 文件."""

    def __init__(self, root: str | Path) -> None:
        """初始化扫描器.

        Args:
            root: 扫描根目录路径
        """
        self._root = Path(root)
        self._files: list[Path] = []

    @property
    def root(self) -> Path:
        """返回扫描根目录."""
        return self._root

    @property
    def files(self) -> list[Path]:
        """返回收集到的文件列表."""
        return self._files

    def collect(self) -> list[Path]:
        """执行扫描，收集所有 .md 文件.

        Returns:
            按路径排序的 .md 文件列表

        Raises:
            FileNotFoundError: 根目录不存在时抛出
        """
        logger.info("开始扫描目录: %s", self._root)

        if not self._root.exists():
            raise FileNotFoundError(f"目录不存在: {self._root}")

        if not self._root.is_dir():
            raise NotADirectoryError(f"路径不是目录: {self._root}")

        raw_files = list(self._root.rglob("*.md"))
        self._files = sorted(raw_files)

        logger.info("扫描完成: 找到 %d 个 .md 文件", len(self._files))
        for f in self._files:
            logger.info("  → %s", f.relative_to(self._root))

        return self._files


# ─── FileAnalyzer: Markdown 解析 ────────────────────────────

class FileAnalyzer:
    """负责解析单个 Markdown 文件，提取统计指标.

    借鉴 cc-haha 的 Tokenizer 设计: 用正则预编译 + 状态机解析代码块。
    """

    # v0.1: 预编译正则，提升批量解析性能
    _LINK_RE: re.Pattern[str] = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    _IMAGE_RE: re.Pattern[str] = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
    _HEADING_RE: re.Pattern[str] = re.compile(r"^(#{1,6})\s+")

    def __init__(self, filepath: Path) -> None:
        """初始化分析器.

        Args:
            filepath: 待分析的文件路径
        """
        self._filepath = filepath
        self._stats = FileStats(filepath=filepath)

    @property
    def stats(self) -> FileStats:
        """返回分析结果."""
        return self._stats

    def analyze(self) -> FileStats:
        """执行完整的文件分析.

        Returns:
            包含所有统计指标的 FileStats 对象

        解析流程:
            1. 读取文件内容
            2. 拆分为行，统计基本指标 (行数、字符数、词数)
            3. 状态机解析代码块
            4. 逐行提取 Markdown 结构 (链接、图片、标题)
        """
        logger.info("分析文件: %s", self._filepath.name)

        # ── 1. 读取 ──
        try:
            content = self._filepath.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            logger.warning("  UTF-8 解码失败，尝试 GBK: %s", self._filepath.name)
            content = self._filepath.read_text(encoding="gbk")
        except Exception as exc:
            logger.error("  ✗ 读取失败: %s", exc)
            return self._stats

        # ── 2. 基础统计 ──
        lines = content.split("\n")
        self._stats.total_lines = len(lines)
        self._stats.char_count = len(content)
        self._stats.word_count = len(content.split())

        logger.info("  行数=%d  字符=%d  词数=%d",
                     self._stats.total_lines,
                     self._stats.char_count,
                     self._stats.word_count)

        # ── 3. 状态机解析代码块 + 结构提取 ──
        in_code_block = False
        code_block_lines = 0

        for line in lines:
            stripped = line.strip()

            # 空白行
            if not stripped:
                self._stats.blank_lines += 1
                continue

            # 代码块边界检测
            if stripped.startswith("```"):
                if in_code_block:
                    # 结束代码块
                    in_code_block = False
                    self._stats.code_lines += code_block_lines
                    code_block_lines = 0
                else:
                    # 开始代码块
                    in_code_block = True
                    self._stats.code_block_count += 1
                continue

            if in_code_block:
                code_block_lines += 1
                continue

            # 文本行
            self._stats.text_lines += 1

            # 链接匹配
            self._stats.link_count += len(self._LINK_RE.findall(line))

            # 图片匹配
            self._stats.image_count += len(self._IMAGE_RE.findall(line))

            # 标题匹配
            heading_match = self._HEADING_RE.match(line)
            if heading_match:
                level = len(heading_match.group(1))
                self._stats.heading_count += 1
                self._stats.headings_by_level[level] = (
                    self._stats.headings_by_level.get(level, 0) + 1
                )

        # 处理未闭合代码块 (文件末尾)
        if in_code_block:
            self._stats.code_lines += code_block_lines

        logger.info(
            "  空行=%d  代码行=%d  文本行=%d  链接=%d  图片=%d  "
            "代码块=%d  标题=%d",
            self._stats.blank_lines,
            self._stats.code_lines,
            self._stats.text_lines,
            self._stats.link_count,
            self._stats.image_count,
            self._stats.code_block_count,
            self._stats.heading_count,
        )

        return self._stats


# ─── ReportBuilder: 汇总与输出 ──────────────────────────────

class ReportBuilder:
    """负责汇总多个 FileStats 并生成格式化报告."""

    def __init__(self) -> None:
        """初始化报告构建器."""
        self._report = SummaryReport()

    @property
    def report(self) -> SummaryReport:
        """返回汇总报告."""
        return self._report

    def aggregate(self, stats_list: list[FileStats]) -> SummaryReport:
        """汇总所有文件的统计数据.

        Args:
            stats_list: 逐文件统计列表

        Returns:
            汇总报告
        """
        logger.info("汇总 %d 个文件的统计数据...", len(stats_list))

        self._report.files_scanned = len(stats_list)
        self._report.per_file = stats_list

        for s in stats_list:
            self._report.total_lines += s.total_lines
            self._report.total_chars += s.char_count
            self._report.total_words += s.word_count
            self._report.total_links += s.link_count
            self._report.total_images += s.image_count
            self._report.total_code_blocks += s.code_block_count
            self._report.total_headings += s.heading_count

        logger.info("汇总完成: %d 个文件", self._report.files_scanned)
        return self._report

    def format_report(self) -> str:
        """格式化报告为可打印字符串.

        Returns:
            格式化的多行报告文本
        """
        r = self._report
        sep = "=" * 60
        lines: list[str] = [
            "",
            sep,
            "  📊 Markdown 文件分析报告",
            sep,
            f"  扫描文件数:     {r.files_scanned:>6}",
            f"  总行数:         {r.total_lines:>6}",
            f"  总字符数:       {r.total_chars:>6}",
            f"  总词数:         {r.total_words:>6}",
            f"  总链接数:       {r.total_links:>6}",
            f"  总图片数:       {r.total_images:>6}",
            f"  总代码块数:     {r.total_code_blocks:>6}",
            f"  总标题数:       {r.total_headings:>6}",
            sep,
        ]

        if r.per_file:
            lines.append("")
            lines.append("  📁 逐文件详情:")
            for s in r.per_file:
                lines.append(
                    f"    [{s.filepath.name}]  "
                    f"行:{s.total_lines}  字:{s.char_count}  "
                    f"链接:{s.link_count}  图片:{s.image_count}  "
                    f"代码块:{s.code_block_count}  标题:{s.heading_count}"
                )

        lines.extend([
            "",
            sep,
            "  ✅ 分析完成!",
            sep,
        ])

        return "\n".join(lines)

    def print_report(self) -> None:
        """将报告输出到控制台."""
        print(self.format_report())


# ─── CLI ────────────────────────────────────────────────────

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """解析命令行参数.

    Args:
        argv: 参数列表，默认从 sys.argv 获取

    Returns:
        解析后的命名空间
    """
    parser = argparse.ArgumentParser(
        description="Markdown 文件分析器 — 扫描目录并生成统计报告",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python md_analyzer_pro.py                    # 扫描当前目录
  python md_analyzer_pro.py ./docs              # 扫描 docs 目录
  python md_analyzer_pro.py --verbose ./src      # 详细日志模式
        """,
    )
    parser.add_argument(
        "directory",
        nargs="?",
        default=".",
        help="要扫描的目录路径 (默认: 当前目录)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="启用 DEBUG 级别日志输出",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """主入口: 串联 FileCollector → FileAnalyzer → ReportBuilder.

    Args:
        argv: 命令行参数列表

    Returns:
        退出码 (0=成功, 1=错误)
    """
    args = parse_args(argv)

    if args.verbose:
        logger.setLevel(logging.DEBUG)
        logger.debug("详细日志模式已启用")

    logger.info("=" * 60)
    logger.info("  🚀 Markdown 分析器启动 (Pro 模式)")
    logger.info("=" * 60)

    # ── 1. 收集文件 ──
    collector = FileCollector(args.directory)
    try:
        md_files = collector.collect()
    except (FileNotFoundError, NotADirectoryError) as exc:
        logger.error("扫描失败: %s", exc)
        return 1

    if not md_files:
        logger.warning("未找到 .md 文件，退出。")
        return 0

    # ── 2. 逐文件分析 ──
    all_stats: list[FileStats] = []
    for filepath in md_files:
        analyzer = FileAnalyzer(filepath)
        stats = analyzer.analyze()
        all_stats.append(stats)

    # ── 3. 汇总 ──
    builder = ReportBuilder()
    builder.aggregate(all_stats)

    # ── 4. 输出报告 ──
    builder.print_report()

    return 0


if __name__ == "__main__":
    sys.exit(main())