# Concise Fiction

中文小说精炼写作技能，供章节生成、审查、润色时去除废话与啰嗦表达。

## 何时使用

- 生成章节正文（预防注水）
- 审查章节质量（啰嗦则打回重生成）
- humanize 润色后二次压啰嗦

## 与 humanizer 的关系

| 技能 | 职责 |
|------|------|
| humanizer | 去 AI 味（套话、升华、模糊归因等） |
| concise-fiction | 惜字如金、禁止「是 X 不是 Y」、删废话 |

## 后端集成

`backend/app/llm/prompts.py` 自动读取本目录 `SKILL.md` 中的：

- `## Prompt 注入规则` → 写入生成 prompt
- `## 审查清单` → 写入审查 prompt
- 全文 → 写入 humanize 润色 prompt
