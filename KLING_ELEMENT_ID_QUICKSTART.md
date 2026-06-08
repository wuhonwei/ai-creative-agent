# 可灵 Element ID 快速使用指南

## 🎯 什么是 Element ID？

Element ID 是可灵视频生成平台提供的**角色/场景一致性功能**，通过给角色或场景分配唯一的 ID，确保它们在多个镜头中保持视觉一致。

---

## 🚀 快速开始（5 步）

### 步骤 1: 更新配置

编辑 `backend/.env`，确保使用正确的模型：

```env
KLING_MODEL=kling-v2-6
VIDEO_MODE=pro
VIDEO_SOUND=on
```

### 步骤 2: 运行数据库迁移

```bash
cd backend
./.venv/Scripts/python.exe migrate_add_element_id.py
```

### 步骤 3: 在可灵平台生成 Element ID

1. 访问 [可灵官网](https://klingai.com)
2. 上传角色/场景图片
3. 生成 element_id（例如：`girl1`, `boy2`, `place1`）
4. 记录这些 ID

### 步骤 4: 在系统中配置 Element ID

#### 为角色配置（通过前端或 API）

**前端建议**：在角色设定页面添加输入框
```jsx
<Input 
  label="可灵 Element ID"
  value={design.kling_element_id}
  onChange={(e) => updateDesign({kling_element_id: e.target.value})}
  placeholder="例如：girl1"
/>
```

**API 方式**：
```bash
curl -X PATCH http://127.0.0.1:8000/api/character-designs/1 \
  -H "Content-Type: application/json" \
  -d '{"kling_element_id": "girl1"}'
```

#### 为场景配置（通过前端或 API）

**前端建议**：在场景管理页面添加输入框
```jsx
<Input 
  label="可灵 Element ID"
  value={scene.kling_element_id}
  onChange={(e) => updateScene({kling_element_id: e.target.value})}
  placeholder="例如：place1"
/>
```

**API 方式**：
```bash
curl -X PATCH http://127.0.0.1:8000/api/scenes/1 \
  -H "Content-Type: application/json" \
  -d '{"kling_element_id": "place1"}'
```

### 步骤 5: 生成视频

正常生成视频，系统会**自动处理**：

```bash
POST /api/shots/1/video
```

**系统自动完成**：
1. 收集镜头涉及的所有角色和场景
2. 查找它们的 `kling_element_id`
3. 构建带 `<<element_id>>` 标记的 prompt
4. 调用可灵 API（自动传递 `element_list` 参数）

---

## 📝 使用示例

### 场景：乔病病冲进教室

#### 1. 配置数据

```json
// 角色：乔病病
{
  "character_id": 1,
  "name": "乔病病",
  "kling_element_id": "girl1"
}

// 场景：教室
{
  "scene_id": 1,
  "name": "教室",
  "kling_element_id": "place1"
}

// 镜头描述
{
  "shot_id": 1,
  "description": "乔病病冲进教室",
  "character_ids": "1",
  "scene": "教室"
}
```

#### 2. 系统自动处理

```python
# 原始描述
"乔病病冲进教室"

# 系统自动转换为
prompt = "乔病病<<girl1>>冲进教室<<place1>>"
element_list = ["girl1", "place1"]

# 调用可灵 API
{
  "prompt": "乔病病<<girl1>>冲进教室<<place1>>",
  "element_list": [
    {"element_id": "girl1"},
    {"element_id": "place1"}
  ]
}
```

#### 3. 后端日志

```
[Videos] 镜头 1 开始后台出片：video_id=10，出场角色=[乔病病]，参考图 1 张，element_ids=2
[Kling] 提交图生视频任务：mode=pro, duration=5, sound=on, element_list=2, 
        prompt=乔病病<<girl1>>冲进教室<<place1>>
```

---

## 🔧 前端集成建议

### 角色设定页面（CharacterDesign）

```jsx
// 在角色视觉设定表单中添加
<div className="form-section">
  <h3>可灵一致性设置</h3>
  <FormField>
    <Label>Element ID</Label>
    <Input 
      value={design.kling_element_id}
      onChange={(e) => setDesign({
        ...design, 
        kling_element_id: e.target.value
      })}
      placeholder="例如：girl1, boy2"
    />
    <HelpText>
      在可灵平台生成角色后获得的 element_id，用于跨镜头保持角色形象一致。
      <a href="https://klingai.com" target="_blank">前往可灵平台</a>
    </HelpText>
  </FormField>
</div>
```

### 场景管理页面（Scene）

```jsx
// 在场景编辑表单中添加
<div className="form-section">
  <h3>可灵一致性设置</h3>
  <FormField>
    <Label>Element ID</Label>
    <Input 
      value={scene.kling_element_id}
      onChange={(e) => setScene({
        ...scene, 
        kling_element_id: e.target.value
      })}
      placeholder="例如：place1, room2"
    />
    <HelpText>
      在可灵平台生成场景后获得的 element_id，用于跨镜头保持场景一致。
    </HelpText>
  </FormField>
</div>
```

### 镜头预览（可选，显示 Element ID 使用情况）

```jsx
// 在镜头卡片中显示
<ShotCard>
  <ShotDescription>{shot.description}</ShotDescription>
  <ElementBadges>
    {shot.characters.map(char => 
      char.kling_element_id && (
        <Badge key={char.id}>
          {char.name} #{char.kling_element_id}
        </Badge>
      )
    )}
    {shot.scene_element_id && (
      <Badge>场景 #{shot.scene_element_id}</Badge>
    )}
  </ElementBadges>
</ShotCard>
```

---

## ⚙️ API 端点

### 角色 Element ID 管理

**获取角色设定**
```
GET /api/character-designs/{design_id}
```

**更新 Element ID**
```
PATCH /api/character-designs/{design_id}
Body: {"kling_element_id": "girl1"}
```

### 场景 Element ID 管理

**获取场景信息**
```
GET /api/scenes/{scene_id}
```

**更新 Element ID**
```
PATCH /api/scenes/{scene_id}
Body: {"kling_element_id": "place1"}
```

### 视频生成（自动使用 Element ID）

**生成单个镜头**
```
POST /api/shots/{shot_id}/video
```

**批量生成本章镜头**
```
POST /api/chapters/{chapter_id}/shots/generate-videos
```

---

## 💡 最佳实践

### 1. Element ID 命名规范

```
角色：girl1, girl2, boy1, man1
场景：place1, room1, street1
```

**建议**：
- 使用小写字母 + 数字
- 角色按性别/类型分类
- 场景按功能分类
- 保持简短易记

### 2. 批量管理

为项目创建 Element ID 映射表：

```
| 角色名 | Element ID | 用途说明 |
|--------|-----------|---------|
| 乔病病 | girl1     | 女主角  |
| 李明   | boy1      | 男主角  |
| 教室   | place1    | 主场景  |
| 宿舍   | place2    | 副场景  |
```

### 3. 测试验证

生成第一个镜头后：
1. 检查后端日志中的 `element_ids` 数量
2. 确认可灵返回的视频是否保持了角色/场景一致性
3. 对比多个镜头，验证一致性效果

---

## 🐛 常见问题

### Q1: Element ID 没有生效？

**检查清单**：
- [ ] `.env` 中 `KLING_MODEL=kling-v2-6`（v1 不支持）
- [ ] 数据库迁移已完成（`kling_element_id` 字段存在）
- [ ] 角色/场景的 `kling_element_id` 已正确保存
- [ ] 镜头描述中包含角色名/场景名（用于匹配）

### Q2: 如何查看是否使用了 Element ID？

查看后端日志：
```
[Videos] 镜头 1 开始后台出片：...，element_ids=2
[Kling] 提交图生视频任务：element_list=2, prompt=...<<girl1>>...
```

如果 `element_ids=0`，说明没有收集到任何 element_id。

### Q3: 场景 Element ID 没有被识别？

**原因**：场景匹配依赖 `Shot.scene` 字段与 `Scene.name` 的模糊匹配。

**解决**：
- 确保 `Shot.scene` 包含场景名称
- 场景名称要在镜头描述中准确出现
- 该章节已关联场景（通过 `ChapterScene` 表）

### Q4: 可以不配置 Element ID 吗？

**可以**！Element ID 是可选的：
- 不配置：按原有流程生成视频（使用参考图/文生图）
- 配置：额外增强跨镜头一致性

---

## 📊 技术实现细节

### 数据流

```
1. 前端保存 element_id
   ↓
2. CharacterDesign.kling_element_id / Scene.kling_element_id
   ↓
3. 视频生成时查询数据库
   ↓
4. 构建 prompt: "角色<<eid>>做动作<<eid>>"
   ↓
5. 调用可灵 API：{prompt, element_list: [{element_id}]}
   ↓
6. 可灵返回一致性视频
```

### Prompt 构建算法

```python
def build_kling_prompt(shot, characters, scene):
    desc = shot.description  # "乔病病冲进教室"
    
    # 为角色添加标记
    for char in characters:
        if char.kling_element_id:
            desc = desc.replace(
                char.name, 
                f"{char.name}<<{char.kling_element_id}>>",
                1  # 只替换第一次出现
            )
    
    # 为场景添加标记
    if scene and scene.kling_element_id:
        desc = desc.replace(
            scene.name,
            f"{scene.name}<<{scene.kling_element_id}>>",
            1
        )
    
    return desc, element_ids
```

---

## ✅ 集成完成清单

- [x] 数据库字段添加（`kling_element_id`）
- [x] 数据库迁移脚本
- [x] 可灵客户端支持 `element_list` 参数
- [x] 视频生成流程自动收集 element_id
- [x] Prompt 自动添加 `<<element_id>>` 标记
- [x] 配置文件更新（模型/模式/音效）
- [x] 日志记录 element_id 使用情况
- [ ] 前端 UI（element_id 输入框）← **需要前端开发**
- [ ] Element ID 批量导入工具（可选）
- [ ] Element ID 可视化管理页面（可选）

**后端功能已 100% 完成，可直接通过 API 使用！**

---

## 📞 需要帮助？

1. 查看详细文档：`KLING_ELEMENT_ID.md`
2. 检查后端日志：`backend/` 目录下的运行日志
3. 测试 API：使用 curl 或 Postman 测试端点
4. 数据库检查：查看 `characterdesign` 和 `scene` 表的 `kling_element_id` 字段
