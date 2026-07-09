"""Markdown 文件分析器 — Flash 模式 (直接实现).

扫描指定目录下的所有 .md 文件，统计字数、行数、链接数、图片数、代码块数，
并生成汇总报告。
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FileStats:
    """单个文件的统计信息."""

    path: Path
    total_lines: int = 0
    blank_lines: int = 0
    code_lines: int = 0   # 代码块内的行
    text_lines: int = 0   # 纯文本行
    char_count: int = 0
    word_count: int = 0
    link_count: int = 0
    image_count: int = 0
    code_block_count: int = 0
    heading_count: int = 0
    headings: dict[int, int] = field(default_factory=dict)


@dataclass
class Summary:
    """汇总报告."""

    files_scanned: int = 0
    total_lines: int = 0
    total_chars: int = 0
    total_words: int = 0
    total_links: int = 0
    total_images: int = 0
    total_code_blocks: int = 0
    total_headings: int = 0
    ext_counts: Counter = field(default_factory=Counter)
    per_file: list[FileStats] = field(default_factory=list)


def scan_directory(root: str) -> list[Path]:
    """第1步: 扫描目录，收集所有 .md 文件."""
    print(f"[步骤1] 扫描目录: {root}")
    root_path = Path(root)
    if not root_path.exists():
        print(f"  ✗ 目录不存在: {root}")
        return []

    md_files = sorted(root_path.rglob("*.md"))
    print(f"  ✓ 找到 {len(md_files)} 个 .md 文件")
    for f in md_files:
        print(f"    - {f.relative_to(root_path)}")
    return md_files


def analyze_file(filepath: Path) -> FileStats:
    """第2步 (逐文件): 读取并分析单个文件."""
    print(f"\n[步骤2] 分析文件: {filepath.name}")

    stats = FileStats(path=filepath)

    try:
        content = filepath.read_text(encoding="utf-8")
    except Exception as e:
        print(f"  ✗ 读取失败: {e}")
        return stats

    lines = content.split("\n")
    stats.total_lines = len(lines)
    stats.char_count = len(content)
    stats.word_count = len(content.split())
    print(f"  ├─ 总行数: {stats.total_lines}")
    print(f"  ├─ 总字符: {stats.char_count}")
    print(f"  ├─ 总词数: {stats.word_count}")

    in_code_block = False
    code_block_lines = 0
    link_pattern = re.compile(r"\[([^\]]+)\]\(([^)]+)\)")
    image_pattern = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")
    heading_pattern = re.compile(r"^(#{1,6})\s+")

    for lineno, line in enumerate(lines, 1):
        stripped = line.strip()

        # 统计空行
        if not stripped:
            stats.blank_lines += 1
            continue

        # 统计代码块
        if stripped.startswith("```"):
            if in_code_block:
                in_code_block = False
                stats.code_lines += code_block_lines
                code_block_lines = 0
            else:
                in_code_block = True
                stats.code_block_count += 1
            continue

        if in_code_block:
            code_block_lines += 1
            continue

        # 统计文本行
        stats.text_lines += 1

        # 统计链接
        links = link_pattern.findall(line)
        stats.link_count += len(links)

        # 统计图片 (排除链接中的图片)
        images = image_pattern.findall(line)
        stats.image_count += len(images)

        # 统计标题
        heading_match = heading_pattern.match(line)
        if heading_match:
            level = len(heading_match.group(1))
            stats.heading_count += 1
            stats.headings[level] = stats.headings.get(level, 0) + 1

    print(f"  ├─ 空行数: {stats.blank_lines}")
    print(f"  ├─ 代码行: {stats.code_lines}")
    print(f"  ├─ 文本行: {stats.text_lines}")
    print(f"  ├─ 链接数: {stats.link_count}")
    print(f"  ├─ 图片数: {stats.image_count}")
    print(f"  ├─ 代码块: {stats.code_block_count}")
    print(f"  └─ 标题数: {stats.heading_count}")

    return stats


def generate_summary(stats_list: list[FileStats], root: str) -> Summary:
    """第3步: 汇总所有文件统计."""
    print(f"\n[步骤3] 生成汇总报告...")

    summary = Summary()
    summary.files_scanned = len(stats_list)
    summary.per_file = stats_list

    for s in stats_list:
        summary.total_lines += s.total_lines
        summary.total_chars += s.char_count
        summary.total_words += s.word_count
        summary.total_links += s.link_count
        summary.total_images += s.image_count
        summary.total_code_blocks += s.code_block_count
        summary.total_headings += s.heading_count
        summary.ext_counts[s.path.suffix] += 1

    print(f"  ✓ 汇总完成: {summary.files_scanned} 个文件")
    return summary


def print_report(summary: Summary) -> None:
    """第4步: 打印格式化报告."""
    print(f"\n{'='*60}")
    print(f"  📊 Markdown 文件分析报告")
    print(f"{'='*60}")
    print(f"  扫描文件数:     {summary.files_scanned:>6}")
    print(f"  总行数:         {summary.total_lines:>6}")
    print(f"  总字符数:       {summary.total_chars:>6}")
    print(f"  总词数:         {summary.total_words:>6}")
    print(f"  总链接数:       {summary.total_links:>6}")
    print(f"  总图片数:       {summary.total_images:>6}")
    print(f"  总代码块数:     {summary.total_code_blocks:>6}")
    print(f"  总标题数:       {summary.total_headings:>6}")
    print(f"{'='*60}")

    if summary.per_file:
        print(f"\n  📁 逐文件详情:")
        for s in summary.per_file:
            print(f"    [{s.path.name}]")
            print(f"      行:{s.total_lines}  字:{s.char_count}  "
                  f"链接:{s.link_count}  图片:{s.image_count}  "
                  f"代码块:{s.code_block_count}  标题:{s.heading_count}")

    print(f"\n{'='*60}")
    print(f"  ✅ 分析完成!")
    print(f"{'='*60}")


def main() -> None:
    """主入口: 串联所有步骤."""
    import sys

    target = sys.argv[1] if len(sys.argv) > 1 else "."

    print("=" * 60)
    print("  [启动] Markdown 分析器 (Flash 模式)")
    print("=" * 60)

    # Step 1
    md_files = scan_directory(target)
    if not md_files:
        print("\n  ⚠ 没有找到 .md 文件，退出。")
        return

    # Step 2
    all_stats: list[FileStats] = []
    for f in md_files:
        stats = analyze_file(f)
        all_stats.append(stats)

    # Step 3
    summary = generate_summary(all_stats, target)

    # Step 4
    print_report(summary)


if __name__ == "__main__":
    main()
