---
name: git-conventions
description: Git 提交规范 — Conventional Commits + 中文提交信息
triggers:
  - git
  - commit
  - 提交
  - push
  - 版本
  - changelog
  - 发布
  - release
---

## Git 提交规范

### 提交信息格式

```
<type>(<scope>): <中文描述>

<详细说明 (可选)>
```

### Type 类型

| Type | 说明 | 示例 |
|------|------|------|
| `feat` | 新功能 | `feat(tools): 添加 glob 文件搜索工具` |
| `fix` | Bug 修复 | `fix(agent): 修复 ThinkBlock 死循环问题` |
| `refactor` | 重构 (不改变行为) | `refactor(core): 提取公共错误处理逻辑` |
| `docs` | 文档 | `docs(readme): 更新安装说明` |
| `test` | 测试 | `test(permission): 添加权限 deny list 测试` |
| `chore` | 构建/依赖/工具 | `chore(deps): 升级 httpx 到 0.28` |
| `style` | 格式 (空格、换行等) | `style: 统一缩进为 4 空格` |

### 分支策略

```
main        — 稳定版本，只接受 PR 合并
develop     — 开发分支
feat/xxx    — 功能分支 (从 develop 切出)
fix/xxx     — 修复分支
```

### 提交粒度

- 一个提交只做一件事
- 提交信息用中文描述"做了什么"，而非"怎么做的"
- 破坏性变更在 body 中标注 `BREAKING CHANGE:`
