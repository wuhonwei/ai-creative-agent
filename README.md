# AI 创作智能体（长篇小说创作）

一个单用户的小说创作 Agent：输入故事背景、角色、氛围，自动规划并逐章生成正文，
在关键节点暂停供你决策（确认 / 微调 / 重写 / 换方向），目标支持千万字量级的长篇连载。

后端 FastAPI + SQLite，前端 React + Vite，生成调用 **DeepSeek**（OpenAI 兼容接口）。

---

## 它如何支撑「千万字一致性」

单个大模型的上下文窗口装不下整部长篇（DeepSeek 约 64K token ≈ 4 万中文字），
所以本项目不靠「把全文喂给模型」，而是**分层记忆 + 人工检查点**：

| 记忆层 | 内容 | 增长方式 |
|--------|------|----------|
| 故事圣经 | 背景 / 氛围 / 文风 / 世界观 + 角色档案 | 恒定，始终全量带上 |
| 短期记忆 | 最近若干章的**正文节选**（字符预算内） | 滚动窗口 |
| 长期记忆 | 更早章节的**压缩摘要** | 亚线性 |
| 结构化记忆 | 角色当前状态、未回收伏笔 | 每章确认后自动抽取更新 |

每确认一章，系统自动：生成该章摘要 → 抽取角色状态变化 / 新伏笔 / 已回收伏笔 → 写回数据库。
于是正文线性变大，但喂给模型的上下文始终可控。

> 扩展点：章节数很多（>100 章）时，长期摘要本身也会变长。
> `backend/app/services/context_builder.py` 已预留接入**向量检索**的位置——
> 届时只召回与「本章梗概」语义相关的历史摘要，而非全部拼接。当前按时间顺序拼接，足够起步与中等体量。

---

## 创作流程（半自动，逐章检查点）

1. **建作品 + 写故事圣经**（背景 / 角色性格 / 氛围 / 世界观）。
2. **生成分层大纲**（卷 → 章）→ 🛑 检查点：可直接编辑、增删、重新生成。
3. **逐章生成正文**（流式实时显示）→ 🛑 检查点，四个决策：
   - ✅ 确认接受（触发记忆更新）
   - 🔄 重新生成（可附改进要求）
   - 🧭 换个发展方向（给一句你想要的走向，按你的意图改写）
   - ✏️ 手动编辑正文
4. 确认后自动更新记忆，继续下一章。
5. 随时**导出** `.txt` / `.md`。

---

## 启动

### 一键启动（Windows）

在项目根目录双击 `start.bat`，或在终端执行：

```powershell
.\start.bat
```

脚本会各自打开一个窗口跑后端（8000）和前端（5173），就绪后打开 http://127.0.0.1:5173 。
再次双击会先关掉占用这两个端口的旧进程再启动。双击 `stop.bat` 可同时停掉两边。

### 1. 配置 DeepSeek API Key

复制 `backend/.env.example` 为 `backend/.env`，填入你的 Key（[platform.deepseek.com](https://platform.deepseek.com) 获取）：

```
DEEPSEEK_API_KEY=sk-你的真实key
```

### 2. 单独启动后端（端口 8000）

依赖已装在 `backend/.venv`。在 `backend` 目录下：

```bash
# Windows (Git Bash)
./.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

打开 http://127.0.0.1:8000/api/health 应返回 `"llm_configured": true`（说明 Key 已生效）。

> 注意：不要加 `--reload`。在 Windows 上它会产生 reloader 父进程 + worker 子进程，
> 修改 `.env` 或代码后用 PID 不易清理干净，反而容易出现「端口已被占用」。
> 改了配置/代码后，用下面的「一键重启」脚本即可。

#### 一键重启后端

每次修改 `.env`（如换模型、调字数）或后端代码后，需要重启后端才能生效。
项目提供了 `backend/restart.sh`：它会自动清理占用 8000 端口的旧进程，再启动新的。

```bash
# 在 backend 目录下
./restart.sh

# 可选：指定其它端口
./restart.sh 8001
```

脚本会在当前终端前台运行后端（能直接看到日志），按 `Ctrl+C` 停止。
若想腾出终端，可在新开的窗口里运行，或在命令末尾加 `&` 放到后台。

### 3. 单独启动前端（端口 5173）

在 `frontend` 目录下：

```bash
npm run dev
```

浏览器打开 http://localhost:5173 即可使用。前端的 `/api` 请求由 Vite 代理到后端，无需改地址。

---

## 目录结构

```
backend/
  app/
    main.py              FastAPI 入口 + CORS + 路由装配
    config.py            读取 .env（API Key、模型、上下文预算等）
    db.py / models.py    SQLite + 数据模型
    schemas.py           请求/响应模型
    llm/client.py        DeepSeek 流式/非流式封装
    llm/prompts.py       中文 Prompt 模板（大纲/正文/摘要/状态抽取）
    services/
      context_builder.py 分层记忆组装（一致性核心，预留向量检索）
      generation.py      章节确认后的摘要+状态回写
    routers/             projects / outline / chapters / generate(SSE) / export
  requirements.txt  .env.example  restart.sh  smoke_test.py
frontend/
  src/
    App.jsx              作品列表 + 标签导航
    api.js               API 封装（含 SSE 流式生成解析）
    components/
      BibleEditor.jsx    故事圣经 + 角色档案
      OutlineView.jsx    大纲生成与编辑（检查点）
      ChapterWorkspace.jsx 章节工作台（流式正文 + 进度/角色/伏笔侧栏）
      DecisionPanel.jsx  四个决策动作
```

---

## 关于成本与时长（千万字量级）

DeepSeek 价格很低，但 1000 万字仍是大体量：粗算需数千章生成、若干小时累计耗时，
API 费用约几十元人民币量级（以官方实时价为准）。逐章检查点的设计正是为了让你
始终掌控这笔花费与质量——不满意的章节在写下一章前就能拦截重写。

## 数据

所有内容存在 `backend/creative_agent.db`（SQLite 单文件），可直接备份/迁移。
删除作品会级联删除其全部章节、大纲、角色与伏笔。
