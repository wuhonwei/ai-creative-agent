---
name: auto-push
version: 1.0.0
description: |
  代码修改完成后自动提交并推送到远程仓库。Agent 完成实现、修复、重构等会改动代码的任务后应确保变更已 push。
  项目已配置 stop hook 在每次 Agent 回合结束时自动执行；若 hook 未生效或用户要求立即同步，则手动运行推送脚本。
allowed-tools:
  - Shell
---

# Auto Push：代码改动自动同步远程

## 目标

只要本回合**实际修改了仓库内代码**，最终都应出现在远程分支上，无需用户每次手动说「提交推送」。

## 自动机制（优先）

项目根目录已配置 Cursor Hook：

- 文件：`.cursor/hooks.json`
- 事件：`stop`（Agent 一轮对话结束）
- 脚本：`.cursor/hooks/auto-push.sh`

Hook 会在回合结束时检查工作区；若有可提交的改动，则 `git add` → `commit` → `push`。

## Agent 手动兜底

在以下情况，Agent **必须**主动执行推送（不能只改代码就结束）：

1. 用户明确要求「推送到远程 / push」
2. 用户反馈远程没有最新代码
3. 刚完成一批代码修改，但不确定 hook 是否已执行
4. Hook 推送失败（权限、冲突、无网络）

### 推荐命令（Windows）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .cursor/skills/auto-push/scripts/auto-push.ps1
```

### 推荐命令（Git Bash / macOS / Linux）

```bash
bash .cursor/skills/auto-push/scripts/auto-push.sh
```

脚本与 hook 共用同一套规则，**不要**手写与脚本冲突的 git 命令（例如 `git push --force`）。

## 安全规则（硬性）

1. **禁止** `git push --force` / `git push -f`，尤其禁止对 `main` / `master`
2. **禁止**提交 `backend/.env` 及任何含密钥、token 的文件
3. **禁止** `git commit --amend` 除非用户明确要求且未推送到远程
4. **禁止** `git reset --hard` 等破坏性操作
5. **不要**提交临时/日志文件：`backend/_*.txt`、`backend/_*.log`、`backend/_*.json`（批处理进度）、`.cursor/skills/.skills_store_lock.json`
6. **不要**修改 `git config`

## 提交信息

自动提交使用脚本生成的摘要信息；Agent 手动提交时应写清「为什么」，1–2 句完整句子。

## 推送失败时

1. 查看脚本输出的错误信息
2. 若是合并冲突：告知用户，**不要** force push
3. 若是认证失败：提示用户检查 SSH / GitHub 凭据
4. 将未推送的 commit hash 和分支名告知用户

## 验证

推送后执行：

```bash
git status
git log -1 --oneline
```

应显示 `Your branch is up to date with 'origin/<branch>'`。
