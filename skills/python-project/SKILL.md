---
name: python-project
description: Python 项目开发规范 — 编码风格、项目结构、类型注解标准
triggers:
  - Python
  - python
  - 写代码
  - 创建项目
  - 新建文件
  - 编码规范
  - 风格
  - pep8
  - 类型注解
---

## Python 编码规范

### 代码风格
- 遵循 PEP 8 规范
- 缩进使用 4 空格
- 字符串优先使用双引号 `"..."` (docstring 使用三双引号 `"""..."""`)
- 行宽不超过 100 字符
- 导入顺序: 标准库 → 第三方库 → 本地模块，每组之间空一行

### 类型注解
- 所有公开函数必须标注参数类型和返回值类型
- 使用 `from __future__ import annotations` 延迟求值
- 复杂类型使用 `typing` 模块 (List, Dict, Optional, Union)
- Python 3.10+ 可使用 `X | None` 语法

### 文档字符串
```python
def function_name(param: str) -> bool:
    """一句话描述函数功能.

    Args:
        param: 参数说明

    Returns:
        返回值说明

    Raises:
        ValueError: 异常条件说明
    """
```

### 项目结构
```
src/package_name/
├── __init__.py
├── core/          # 核心逻辑
├── tools/         # 工具/插件
├── cli/           # 命令行界面
└── utils/         # 工具函数

tests/             # 测试 (镜像 src 结构)
```

### 错误处理
- 使用具体异常类型，不用裸 `except:`
- 自定义异常继承自项目基类
- 异常消息用中文 (面向中文开发者)

### 日志
- 使用 `logging.getLogger(__name__)` 模块级 logger
- 敏感信息 (API Key, Token) 不输出到日志
