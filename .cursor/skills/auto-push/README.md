# Auto Push

Agent 改完代码后自动 `commit` + `push` 到 `origin`。

## 组成

| 文件 | 作用 |
|------|------|
| `SKILL.md` | 指导 Agent 在改码后确保远程同步 |
| `scripts/auto-push.sh` | 核心脚本（Bash） |
| `scripts/auto-push.ps1` | Windows 手动兜底 |
| `../hooks.json` + `../hooks/auto-push.sh` | Cursor `stop` hook，每轮 Agent 结束自动执行 |

## 启用 Hook

1. 将本仓库文件提交到 git
2. 重启 Cursor（或保存 `hooks.json` 后等待热加载）
3. 在 **Settings → Hooks** 或 **Hooks 输出通道** 确认 hook 已加载

## 手动测试

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .cursor/skills/auto-push/scripts/auto-push.ps1
```

## 不会提交的文件

- `backend/.env`
- `backend/_*` 批处理临时文件
- `.cursor/skills/.skills_store_lock.json`
