"""hello 模块 — 提供简单的问候函数."""

from __future__ import annotations


def hello() -> str:
    """返回 "Hello, World!" 问候语.

    Returns:
        问候字符串 "Hello, World!"
    """
    return "Hello, World!"


if __name__ == "__main__":
    print(hello())
