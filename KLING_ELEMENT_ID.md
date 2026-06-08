# 可灵 Element ID 集成说明

## 概述

已完成可灵 element_id（角色/场景一致性功能）的后端集成，支持跨镜头保持角色和场景的视觉一致性。

---

## 数据库变更

### 新增字段

1. **CharacterDesign.kling_element_id** (TEXT)
   - 存储可灵平台生成的角色一致性 ID
   - 用于跨镜头保持角色形象统一

2. **Scene.kling_element_id** (TEXT)
   - 存储可灵平台生成的场景一致性 ID
   - 用于跨镜头保持场景统一

### 迁移脚本

运行 `backend/migrate_add_element_id.py` 已完成数据库迁移。

---

## 配置变更

### .env 配置更新

```env
# 可灵视频生成配置
KLING_MODEL=kling-v2-6    # 使用 v2-6 模型（支持 element_id）
VIDEO_MODE=pro             # 使用 pro 模式（高表现）
VIDEO_SOUND=on             # 开启音效
```

---

## 功能实现

### 1. 可灵客户端更新

**文件：** `backend/app/llm/kling_client.py`

**新增参数：**
- `sound` - 音效开关（on/off）
- `element_list` - element_id 列表，格式：`[{"element_id": "girl1"}, {"element_id": "place1"}]`

**API 调用示例：**
```python
await kling_client.image_to_video(
    image_path=frame_path,
    prompt="乔病病<<girl1>>冲进了<<place1>>",  # 带 <<element_id>> 标记
    element_list=["girl1", "place1"],
    duration=5,
    save_path=output_path,
)
```

### 2. 视频生成流程更新

**文件：** `backend/app/routers/videos.py`

**核心逻辑：**

1. **收集 element_id**
   - 从 `CharacterDesign.kling_element_id` 获取角色的 element_id
   - 从 `Scene.kling_element_id` 获取场景的 element_id

2. **构建带标记的 prompt**
   - 自动在角色名后添加 `<<element_id>>` 标记
   - 自动在场景名后添加 `<<element_id>>` 标记
   - 例如：`"乔病病冲进了教室"` → `"乔病病<<girl1>>冲进了<<place1>>"`

3. **传递给可灵 API**
   - `prompt`：带 `<<element_id>>` 标记的文本
   - `element_list`：所有 element_id 的列表

---

## 使用流程

### 步骤 1: 在可灵平台生成 element_id

1. 访问可灵官网，上传角色/场景图片
2. 生成 element_id（例如：`girl1`, `place1`）
3. 记录这些 element_id

### 步骤 2: 在系统中配置 element_id

#### 配置角色 element_id

**API 端点：** `PATCH /api/character-designs/{design_id}`

```bash
curl -X PATCH http://127.0.0.1:8000/api/character-designs/1 \
  -H "Content-Type: application/json" \
  -d '{
    "kling_element_id": "girl1"
  }'
```

#### 配置场景 element_id

**API 端点：** `PATCH /api/scenes/{scene_id}`

```bash
curl -X PATCH http://127.0.0.1:8000/api/scenes/1 \
  -H "Content-Type: application/json" \
  -d '{
    "kling_element_id": "place1"
  }'
```

### 步骤 3: 生成视频

正常调用视频生成接口，系统会自动：
1. 收集该镜头涉及的所有角色和场景的 element_id
2. 构建带 `<<element_id>>` 标记的 prompt
3. 调用可灵 API 生成视频

**API 端点：** `POST /api/shots/{shot_id}/video`

```bash
curl -X POST http://127.0.0.1:8000/api/shots/1/video
```

---

## 技术细节

### Prompt 标记逻辑

```python
# 原始镜头描述
shot.description = "乔病病冲进了教室"

# 系统自动处理：
# 1. 查找 "乔病病" 对应的 kling_element_id = "girl1"
# 2. 查找 "教室" 对应的 kling_element_id = "place1"
# 3. 构建标记后的 prompt

kling_prompt = "乔病病<<girl1>>冲进了<<place1>>"
element_list = ["girl1", "place1"]
```

### Element ID 匹配规则

1. **角色匹配**：按 `Shot.character_ids` 中的角色顺序匹配
2. **场景匹配**：通过 `Shot.scene` 字段与 `Scene.name` 模糊匹配
3. **标记替换**：在原描述中查找角色名/场景名，添加 `<<element_id>>` 标记

---

## 前端集成建议

### 角色设定页面

添加 `kling_element_id` 输入框：

```jsx
<FormField>
  <Label>可灵 Element ID（角色一致性）</Label>
  <Input 
    value={design.kling_element_id}
    onChange={(e) => setDesign({...design, kling_element_id: e.target.value})}
    placeholder="例如：girl1, boy2"
  />
  <Description>
    在可灵平台生成角色后获得的 element_id，用于跨镜头保持角色形象一致
  </Description>
</FormField>
```

### 场景管理页面

添加 `kling_element_id` 输入框：

```jsx
<FormField>
  <Label>可灵 Element ID（场景一致性）</Label>
  <Input 
    value={scene.kling_element_id}
    onChange={(e) => setScene({...scene, kling_element_id: e.target.value})}
    placeholder="例如：place1, room2"
  />
  <Description>
    在可灵平台生成场景后获得的 element_id，用于跨镜头保持场景一致
  </Description>
</FormField>
```

---

## 测试验证

### 1. 检查数据库迁移

```bash
cd backend
./.venv/Scripts/python.exe migrate_add_element_id.py
```

预期输出：
```
[OK] characterdesign.kling_element_id added.
[OK] scene.kling_element_id added.
[SUCCESS] Migration completed!
```

### 2. 检查配置

```bash
curl http://127.0.0.1:8000/api/health
```

预期返回包含：
```json
{
  "kling_configured": true
}
```

### 3. 测试视频生成

1. 配置角色和场景的 element_id
2. 创建镜头，确保描述中包含角色名和场景名
3. 调用视频生成接口
4. 查看后端日志，确认 `element_ids` 参数被正确传递

---

## 注意事项

1. **element_id 格式**：纯字母数字，不含特殊字符（建议：`girl1`, `place1` 等）
2. **模型要求**：必须使用 `kling-v2-6` 或更高版本（v1 不支持 element_id）
3. **匹配精度**：角色名和场景名必须在 `shot.description` 中准确出现
4. **空 element_id**：如果某个角色/场景没有配置 element_id，不会影响视频生成（只是不会应用一致性约束）

---

## 下一步优化

- [ ] 前端 UI：element_id 输入框和说明文档
- [ ] 批量导入：支持批量导入多个角色/场景的 element_id
- [ ] 智能匹配：改进场景名匹配算法（模糊匹配、别名支持）
- [ ] 可视化预览：显示哪些镜头使用了 element_id
- [ ] element_id 管理：统一管理页面，查看所有已配置的 element_id

---

## 完成状态

✅ 数据库迁移完成  
✅ 可灵客户端支持 element_id  
✅ 视频生成流程自动收集和传递 element_id  
✅ 配置文件更新（模型/模式/音效）  
✅ 日志记录 element_id 使用情况  

**后端集成已完成，可直接使用！**
