import React, { useEffect, useState } from 'react'
import { api } from '../api'

// 景别 / 运镜 预设项（下拉可选，input 仍可自由输入自定义值）
const SHOT_SIZE_OPTIONS = ['大远景', '远景', '全景', '中景', '中近景', '近景', '特写', '大特写']
const CAMERA_OPTIONS = ['固定', '推近', '拉远', '横摇', '竖摇', '跟随', '环绕', '俯拍', '仰拍', '手持晃动']
const SHOT_DURATION_MIN = 5
const SHOT_DURATION_MAX = 15

function normalizeShotDuration(duration) {
  const n = Number(duration)
  if (Number.isNaN(n)) return SHOT_DURATION_MIN
  return Math.min(SHOT_DURATION_MAX, Math.max(SHOT_DURATION_MIN, Math.round(n)))
}

// 视频工作台（地基阶段）：
//  - 角色设定锁定：锁定画风/五官/发色/服饰 → 生成正/侧/斜/全身参考图 → 选首帧基准
//  - 分镜：从已确认章节拆镜头，可逐镜编辑
// 后续阶段接：可灵图生视频、配音、BGM、字幕、ffmpeg 合成。
export default function VideoStudio({ project, notify }) {
  const [tab, setTab] = useState('character') // 'character' | 'scene' | 'storyboard'

  return (
    <div style={{ padding: 20 }}>
      <h2>🎬 视频工作台</h2>
      <p className="hint">
        锁定角色外观 → 生成参考图（全剧统一调用，杜绝变脸）→ 规划场景 → 把章节拆成分镜。
        图生视频 / 配音 / 合成为后续阶段。
      </p>

      <div style={{ display: 'flex', gap: 10, margin: '16px 0', borderBottom: '1px solid var(--border)' }}>
        <button className={tab === 'character' ? 'primary' : ''}
          onClick={() => setTab('character')} style={{ borderRadius: '6px 6px 0 0' }}>
          角色设定锁定
        </button>
        <button className={tab === 'scene' ? 'primary' : ''}
          onClick={() => setTab('scene')} style={{ borderRadius: '6px 6px 0 0' }}>
          场景规划
        </button>
        <button className={tab === 'storyboard' ? 'primary' : ''}
          onClick={() => setTab('storyboard')} style={{ borderRadius: '6px 6px 0 0' }}>
          分镜脚本
        </button>
      </div>

      {tab === 'character' && <CharacterDesignPanel project={project} notify={notify} />}
      {tab === 'scene' && <ScenePanel project={project} notify={notify} />}
      {tab === 'storyboard' && <StoryboardPanel project={project} notify={notify} />}
    </div>
  )
}

const VIEW_LABELS = { face_closeup: '脸部特写', front: '正视图', side: '侧视图', back: '背视图', three_quarter: '斜视图', full_body: '全身立绘' }

// ============ 角色设定锁定 ============
const EMPTY_DESIGN = {
  gender: '', age: '', body_type: '', color_tone: '',
  art_style: '', facial_features: '', hair: '', outfit: '', extra: '',
  kling_voice_id: '',
}

function CharacterDesignPanel({ project, notify }) {
  const [characters, setCharacters] = useState([])
  const [activeId, setActiveId] = useState(null)
  const [design, setDesign] = useState(null)
  const [form, setForm] = useState({ ...EMPTY_DESIGN })
  const [refs, setRefs] = useState([])
  const [generating, setGenerating] = useState(false)
  const [creatingElement, setCreatingElement] = useState(false)
  const [elementId, setElementId] = useState('')
  const [preview, setPreview] = useState(null)

  useEffect(() => {
    api.listCharacters(project.id).then((cs) => {
      setCharacters(cs)
      if (cs.length && !activeId) setActiveId(cs[0].id)
    }).catch((e) => notify(e.message, true))
  }, [project.id])

  useEffect(() => {
    if (!activeId) return
    loadCharacter(activeId)
  }, [activeId])

  async function loadCharacter(cid) {
    try {
      const [d, rs] = await Promise.all([
        api.getDesign(project.id, cid),
        api.listRefImages(project.id, cid),
      ])
      setDesign(d)
      setElementId(d?.kling_element_id || '')
      setForm(d
        ? {
          gender: d.gender || '', age: d.age || '', body_type: d.body_type || '',
          color_tone: d.color_tone || '', art_style: d.art_style || '',
          facial_features: d.facial_features || '', hair: d.hair || '',
          outfit: d.outfit || '', extra: d.extra || '',
          kling_voice_id: d.kling_voice_id || '',
        }
        : { ...EMPTY_DESIGN })
      setRefs(rs)
    } catch (e) { notify(e.message, true) }
  }

  async function saveDesign() {
    try {
      const d = await api.saveDesign(project.id, activeId, form)
      setDesign(d)
      setElementId(d.kling_element_id || '')
      notify('锁定设定已保存')
    } catch (e) { notify(e.message, true) }
  }

  const REQUIRED_REF_VIEWS = ['face_closeup', 'front', 'side', 'back', 'full_body']

  async function createElement() {
    if (!form.kling_voice_id.trim()) {
      notify('请填写音色 ID', true)
      return
    }
    const missing = REQUIRED_REF_VIEWS.filter((v) => !refs.some((r) => r.view === v))
    if (missing.length) {
      notify(
        `合成主体需要完整五视角参考图，缺少：${missing.map((v) => VIEW_LABELS[v] || v).join('、')}。请先一键生成全套。`,
        true,
      )
      return
    }
    setCreatingElement(true)
    try {
      const d = await api.createCharacterElement(project.id, activeId, {
        voice_id: form.kling_voice_id.trim(),
      })
      setDesign(d)
      setElementId(d.kling_element_id || '')
      notify(`主体合成成功，ID：${d.kling_element_id}`)
    } catch (e) { notify(e.message, true) } finally { setCreatingElement(false) }
  }

  const [autofilling, setAutofilling] = useState(false)

  async function autofillDesigns(onlyEmpty) {
    const msg = onlyEmpty
      ? '将结合大纲和角色档案，为还没有设定的角色 AI 填充锁定关键词。继续？'
      : '将结合大纲和角色档案，AI 重新填充所有角色的锁定关键词（覆盖现有）。继续？'
    if (!confirm(msg)) return
    setAutofilling(true)
    try {
      const res = await api.autofillDesigns(project.id, onlyEmpty)
      if (activeId) await loadCharacter(activeId)  // 刷新当前角色表单
      notify(`AI 填充完成：更新 ${res.updated.length} 个角色`
        + (res.skipped.length ? `，跳过 ${res.skipped.length} 个` : ''))
    } catch (e) { notify(e.message, true) } finally { setAutofilling(false) }
  }

  const [scanning, setScanning] = useState(false)

  async function scanFromOutline() {
    if (!confirm('将扫描大纲里所有出现过的人物姓名，自动创建缺失角色并填充其设定。继续？')) return
    setScanning(true)
    try {
      const res = await api.scanCharactersFromOutline(project.id)
      // 扫描可能新建了角色，刷新角色列表
      const cs = await api.listCharacters(project.id)
      setCharacters(cs)
      if (!activeId && cs.length) setActiveId(cs[0].id)
      else if (activeId) await loadCharacter(activeId)
      notify(`扫描完成：发现 ${res.total_found} 人，新建 ${res.created.length} 个角色，`
        + `填充 ${res.filled.length} 个设定`
        + (res.skipped.length ? `，跳过 ${res.skipped.length} 个` : ''))
    } catch (e) { notify(e.message, true) } finally { setScanning(false) }
  }

  async function genRefs(views = []) {
    if (!design || !design.locked_prompt) {
      notify('请先保存锁定设定', true)
      return
    }
    setGenerating(true)
    try {
      const res = await api.generateRefImages(project.id, activeId, views)
      await loadCharacter(activeId)
      const skipped = res?.skipped || []
      if (skipped.length) {
        const detail = skipped.map((s) => `${s.view_label}(${s.reason})`).join('、')
        notify(`部分参考图未生成：${detail}`, true)
      } else {
        notify('参考图生成完成')
      }
    } catch (e) { notify(e.message, true) } finally { setGenerating(false) }
  }

  async function setPrimary(refId) {
    try { await api.setPrimaryRef(refId); await loadCharacter(activeId); notify('已设为首帧基准') }
    catch (e) { notify(e.message, true) }
  }

  async function delRef(refId) {
    if (!confirm('删除这张参考图？')) return
    try { await api.deleteRefImage(refId); await loadCharacter(activeId) }
    catch (e) { notify(e.message, true) }
  }

  if (characters.length === 0) {
    return (
      <div className="empty" style={{ textAlign: 'center' }}>
        <p>还没有角色。可以从大纲一键扫描出所有人物并自动填充设定。</p>
        <button className="primary" disabled={scanning} onClick={scanFromOutline}>
          {scanning ? '扫描中…' : '🔍 从大纲扫描人物'}
        </button>
        <p className="hint" style={{ marginTop: 8 }}>
          或先在「故事圣经」里手动创建角色
        </p>
      </div>
    )
  }

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }))

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '200px 1fr', gap: 16 }}>
      {/* 角色列表 */}
      <div className="panel" style={{ alignSelf: 'start' }}>
        <h3 style={{ marginTop: 0 }}>角色</h3>
        {characters.map((c) => (
          <div key={c.id}
            onClick={() => setActiveId(c.id)}
            className={`chapter-node ${activeId === c.id ? 'active' : ''}`}
            style={{ cursor: 'pointer', marginBottom: 6 }}>
            {c.name}
          </div>
        ))}
        <div style={{ borderTop: '1px solid var(--border)', marginTop: 10, paddingTop: 10 }}>
          <button className="primary small" disabled={scanning}
            style={{ width: '100%', marginBottom: 6 }}
            onClick={scanFromOutline}>
            {scanning ? '扫描中…' : '🔍 从大纲扫描人物'}
          </button>
          <button className="small" disabled={autofilling}
            style={{ width: '100%', marginBottom: 6 }}
            onClick={() => autofillDesigns(true)}>
            {autofilling ? 'AI 填充中…' : '🪄 AI 填充空缺设定'}
          </button>
          <button className="small" disabled={autofilling}
            style={{ width: '100%' }}
            onClick={() => autofillDesigns(false)}>
            重新填充全部
          </button>
          <p className="hint" style={{ marginTop: 6 }}>
            扫描大纲找出所有人物并建档；或对已有角色 AI 填充锁定设定
          </p>
        </div>
      </div>

      {/* 设定 + 参考图 */}
      <div>
        <div className="panel">
          <h3 style={{ marginTop: 0 }}>锁定关键词（全剧统一，保证不变脸）</h3>
          <p className="hint" style={{ marginTop: 0 }}>
            纯白背景、标准 A-Pose、正交无畸变、面无表情、柔和漫射光、全身居中对称等
            技术约束已自动注入，无需填写。
          </p>

          <div className="hint" style={{ fontWeight: 600, margin: '8px 0 4px' }}>人物基础特征</div>
          <div className="row">
            <div style={{ flex: 1 }}>
              <label>性别</label>
              <input value={form.gender} list="design-gender" onChange={set('gender')} placeholder="男 / 女" />
              <datalist id="design-gender">
                <option value="男" /><option value="女" />
              </datalist>
            </div>
            <div style={{ flex: 1 }}>
              <label>年龄</label>
              <input value={form.age} onChange={set('age')} placeholder="如：18岁少年、成熟御姐" />
            </div>
          </div>
          <label>体型与肤色</label>
          <input value={form.body_type} onChange={set('body_type')} placeholder="如：清瘦修长、小麦色皮肤" />
          <div className="row">
            <div style={{ flex: 1 }}>
              <label>风格</label>
              <input value={form.art_style} list="design-style" onChange={set('art_style')}
                placeholder="如：3D精品漫剧、2D、水墨风" />
              <datalist id="design-style">
                <option value="3D精品漫剧" /><option value="3D渲染" /><option value="2D动漫" />
                <option value="水墨风" /><option value="赛博朋克" /><option value="现代极简" />
                <option value="暗黑奇幻" />
              </datalist>
            </div>
            <div style={{ flex: 1 }}>
              <label>色彩基调</label>
              <input value={form.color_tone} onChange={set('color_tone')} placeholder="如：冷色低饱和、暖色高对比" />
            </div>
          </div>

          <div className="hint" style={{ fontWeight: 600, margin: '12px 0 4px' }}>头部细节</div>
          <label>发型 / 发色</label>
          <input value={form.hair} onChange={set('hair')} placeholder="如：黑色短碎发、齐刘海长直发" />
          <label>脸部五官（眉毛 / 鼻子 / 眼神）</label>
          <input value={form.facial_features} onChange={set('facial_features')} placeholder="如：浓眉、挺鼻、锐利眼神" />

          <div className="hint" style={{ fontWeight: 600, margin: '12px 0 4px' }}>身体与穿搭</div>
          <label>服饰穿搭（衣服 / 裤子 / 鞋子 / 配饰）</label>
          <input value={form.outfit} onChange={set('outfit')} placeholder="如：黑色风衣 + 长裤 + 马丁靴" />
          <label>其它锁定词（可选）</label>
          <input value={form.extra} onChange={set('extra')} placeholder="如：银色耳钉、纤细手部" />

          <div className="hint" style={{ fontWeight: 600, margin: '12px 0 4px' }}>可灵主体合成</div>
          <label>
            音色 ID
            <span className="hint" style={{ fontSize: 12, marginLeft: 8 }}>
              （在「音色」页生成或从官方音色库获取）
            </span>
          </label>
          <input
            value={form.kling_voice_id}
            onChange={set('kling_voice_id')}
            placeholder="例如：829824295735410756"
            style={{ fontFamily: 'monospace' }}
          />
          <label style={{ marginTop: 10 }}>主体 ID（Element ID）</label>
          <input
            value={elementId}
            readOnly
            placeholder="合成成功后显示"
            style={{ fontFamily: 'monospace', opacity: elementId ? 1 : 0.6 }}
          />
          <p className="hint" style={{ fontSize: 12, margin: '4px 0 0' }}>
            需本地齐备五视角参考图；提交可灵时按优先级最多传 3 张：正视图 + 侧视图 + 背视图（侧/背缺失时用脸部特写、全身替补）。
          </p>

          <div className="row" style={{ marginTop: 12, gap: 8 }}>
            <button className="primary" onClick={saveDesign}>保存锁定设定</button>
            <button
              className="primary"
              disabled={
                creatingElement
                || REQUIRED_REF_VIEWS.some((v) => !refs.some((r) => r.view === v))
              }
              onClick={createElement}
            >
              {creatingElement ? '合成中…' : '合成主体 ID'}
            </button>
          </div>
          {design?.locked_prompt && (
            <p className="hint" style={{ marginTop: 10 }}>
              固定提示词：{design.locked_prompt}
            </p>
          )}
        </div>

        <div className="panel">
          <div className="row">
            <h3 style={{ margin: 0 }}>参考图（脸部特写 / 正 / 侧 / 背 / 全身）</h3>
            <div className="spacer" />
            <button className="primary" disabled={generating || !design}
              onClick={() => genRefs([])}>
              {generating ? '生成中…' : '一键生成全套'}
            </button>
          </div>
          {!design && <p className="hint">先保存锁定设定，再使用可灵文生图生成参考图。</p>}
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill,minmax(180px,1fr))', gap: 12, marginTop: 12 }}>
            {refs.map((r) => (
              <div key={r.id} style={{ border: r.is_primary ? '2px solid var(--accent)' : '1px solid var(--border)', borderRadius: 8, overflow: 'hidden' }}>
                <img src={api.refImageUrl(r.id)} alt={r.view_label}
                  style={{ width: '100%', height: 180, objectFit: 'cover', cursor: 'pointer' }}
                  onClick={() => setPreview(api.refImageUrl(r.id))} />
                <div style={{ padding: 8, fontSize: 12 }}>
                  <div style={{ marginBottom: 6 }}>
                    {VIEW_LABELS[r.view] || r.view}
                    {r.is_primary && <span className="tag confirmed" style={{ marginLeft: 6 }}>首帧基准</span>}
                  </div>
                  <div className="row" style={{ gap: 4 }}>
                    {!r.is_primary && (
                      <button className="small" style={{ flex: 1 }} onClick={() => setPrimary(r.id)}>设为基准</button>
                    )}
                    <button className="small" style={{ flex: 1 }} onClick={() => genRefs([r.view])}>重生成</button>
                    <button className="small danger" onClick={() => delRef(r.id)}>删</button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      </div>

      {preview && (
        <div onClick={() => setPreview(null)}
          style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.85)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000, cursor: 'pointer' }}>
          <img src={preview} style={{ maxWidth: '90%', maxHeight: '90vh', objectFit: 'contain', borderRadius: 8 }} />
        </div>
      )}
    </div>
  )
}

// ============ 场景规划 ============
function ScenePanel({ project, notify }) {
  const [chapters, setChapters] = useState([])
  const [activeChapterId, setActiveChapterId] = useState(null)
  const [scenes, setScenes] = useState([])      // 本章关联场景
  const [planning, setPlanning] = useState(false)
  const [batchRunning, setBatchRunning] = useState(false)

  useEffect(() => {
    api.listChapters(project.id).then((cs) => {
      setChapters(cs)
      if (cs.length && !activeChapterId) setActiveChapterId(cs[0].id)
    }).catch((e) => notify(e.message, true))
  }, [project.id])

  useEffect(() => {
    if (!activeChapterId) return
    loadChapterScenes(activeChapterId)
  }, [activeChapterId])

  async function loadChapterScenes(chapterId) {
    try {
      const ss = await api.getChapterScenes(chapterId)
      setScenes(ss)
      for (const s of ss) {
        if (s.status === 'processing' || (s.images || []).some((i) => i.status === 'processing')) {
          pollScene(s.id)
        }
      }
    } catch (e) { notify(e.message, true) }
  }

  function pollScene(sceneId) {
    const timer = setInterval(async () => {
      try {
        const all = await api.getChapterScenes(activeChapterId)
        const s = all.find((x) => x.id === sceneId)
        if (!s) { clearInterval(timer); return }
        setScenes((prev) => prev.map((p) => p.id === sceneId ? s : p))
        const pending = (s.images || []).some((i) => i.status === 'processing')
        if (!pending && s.status !== 'processing') clearInterval(timer)
      } catch { clearInterval(timer) }
    }, 6000)
  }

  async function plan() {
    setPlanning(true)
    try {
      const res = await api.planChapterScenes(activeChapterId)
      setScenes(res.scenes)
      notify(`场景规划完成：复用 ${res.reused.length} 个，新建 ${res.created.length} 个`)
    } catch (e) { notify(e.message, true) } finally { setPlanning(false) }
  }

  async function genImage(sceneId) {
    try {
      await api.generateSceneImage(sceneId)
      const all = await api.getChapterScenes(activeChapterId)
      const s = all.find((x) => x.id === sceneId)
      if (s) setScenes((prev) => prev.map((p) => p.id === sceneId ? s : p))
      notify('已提交可灵文生图，完成后自动显示')
      pollScene(sceneId)
    } catch (e) { notify(e.message, true) }
  }

  async function genAllImages() {
    setBatchRunning(true)
    try {
      const res = await api.generateAllSceneImages(project.id)
      notify(`已批量提交 ${res.queued} 个场景图`)
      await loadChapterScenes(activeChapterId)
    } catch (e) { notify(e.message, true) } finally { setBatchRunning(false) }
  }

  if (chapters.length === 0) {
    return <div className="empty">还没有章节。请先到「创作工作台」生成章节正文。</div>
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '220px 1fr', gap: 16 }}>
      {/* 章节列表 */}
      <div className="panel" style={{ alignSelf: 'start', maxHeight: '70vh', overflowY: 'auto' }}>
        <h3 style={{ marginTop: 0 }}>章节</h3>
        {chapters.map((c) => (
          <div key={c.id} onClick={() => setActiveChapterId(c.id)}
            className={`chapter-node ${activeChapterId === c.id ? 'active' : ''}`}
            style={{ cursor: 'pointer', marginBottom: 6 }}>
            <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.title}</div>
          </div>
        ))}
        <div style={{ borderTop: '1px solid var(--border)', marginTop: 10, paddingTop: 10 }}>
          <button className="small" disabled={batchRunning} style={{ width: '100%' }}
            onClick={genAllImages}>
            {batchRunning ? '提交中…' : '🖼 一键为无图场景各生成一张'}
          </button>
          <p className="hint" style={{ marginTop: 6 }}>
            可灵文生图；同一场景可多次生成，多图合成主体 ID
          </p>
        </div>
      </div>

      {/* 场景内容 */}
      <div>
        <div className="row" style={{ marginBottom: 12 }}>
          <button className="primary" disabled={planning} onClick={plan}>
            {planning ? '规划中…' : scenes.length ? '重新规划本章场景' : '规划本章场景'}
          </button>
          <span className="hint">
            从本章正文抽取场景，自动判断与已有场景是否相同（相同复用，有变化新建）
          </span>
        </div>

        {scenes.length === 0 ? (
          <div className="empty">本章还没有场景。点「规划本章场景」从正文抽取。</div>
        ) : (
          scenes.map((s) => (
            <SceneCard key={s.id} scene={s} notify={notify}
              onGenImage={() => genImage(s.id)}
              onChange={(ns) => setScenes((prev) => prev.map((p) => p.id === ns.id ? ns : p))} />
          ))
        )}
      </div>
    </div>
  )
}

// 单个场景卡片：8 区块字段可编辑 + 多图场景参考 + 主体合成
function SceneCard({ scene, notify, onGenImage, onChange }) {
  const [form, setForm] = useState(scene)
  const [creatingElement, setCreatingElement] = useState(false)
  const images = form.images || []
  const doneImages = images.filter((i) => i.status === 'done' && i.image_url)
  const processingCount = images.filter((i) => i.status === 'processing').length

  useEffect(() => {
    setForm(scene)
  }, [scene.id, scene.status, scene.image_url, scene.kling_element_id, scene.images])

  const set = (k) => (e) => setForm((f) => ({ ...f, [k]: e.target.value }))

  async function deleteImage(imageId) {
    if (!confirm('删除这张场景图？')) return
    try {
      await api.deleteSceneImage(imageId)
      const imgs = await api.listSceneImages(scene.id)
      const ns = { ...form, images: imgs, status: imgs.some((i) => i.status === 'processing') ? 'processing' : (imgs.some((i) => i.status === 'done') ? 'done' : 'draft') }
      setForm(ns)
      onChange(ns)
      notify('场景图已删除')
    } catch (e) { notify(e.message, true) }
  }

  async function createElement() {
    if (doneImages.length === 0) {
      notify('请先生成至少一张场景图', true)
      return
    }
    setCreatingElement(true)
    try {
      const ns = await api.createSceneElement(scene.id)
      setForm(ns)
      onChange(ns)
      notify(`场景主体合成成功，ID：${ns.kling_element_id}（使用 ${doneImages.length} 张参考图）`)
    } catch (e) { notify(e.message, true) } finally { setCreatingElement(false) }
  }

  async function save() {
    try {
      const ns = await api.updateScene(scene.id, form)
      onChange(ns)
      notify('场景已保存')
    } catch (e) { notify(e.message, true) }
  }

  return (
    <div className="panel" style={{ marginBottom: 12 }}>
      <div className="row" style={{ marginBottom: 8 }}>
        <b>{form.name || '未命名场景'}</b>
        {form.chapter_ids?.length > 1 && (
          <span className="tag confirmed">跨 {form.chapter_ids.length} 章复用</span>
        )}
        <span className="hint">
          {processingCount > 0 ? `生成中 ${processingCount} 张…`
            : doneImages.length > 0 ? `已生成 ${doneImages.length} 张`
            : '待出图'}
        </span>
        <div className="spacer" />
        <button className="small" onClick={save}>保存</button>
        <button className="small primary" onClick={onGenImage}>
          🖼 生成场景图（可灵文生图）
        </button>
      </div>

      {images.length > 0 && (
        <div style={{ display: 'flex', flexWrap: 'wrap', gap: 8, marginBottom: 10 }}>
          {images.map((img) => (
            <div key={img.id} style={{ width: 140, position: 'relative' }}>
              {img.status === 'done' && img.image_url ? (
                <img src={api.sceneRefImageUrl(img.id)} alt=""
                  style={{ width: '100%', height: 100, objectFit: 'cover', borderRadius: 8 }} />
              ) : (
                <div style={{ width: '100%', height: 100, borderRadius: 8, background: 'var(--border)',
                  display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: 12 }}>
                  {img.status === 'processing' ? '生成中…' : img.status === 'failed' ? '失败' : '…'}
                </div>
              )}
              {img.status === 'failed' && img.error && (
                <div className="hint" style={{ fontSize: 11, marginTop: 2 }}>{img.error}</div>
              )}
              {img.status !== 'processing' && (
                <button className="small danger" style={{ marginTop: 4, width: '100%' }}
                  onClick={() => deleteImage(img.id)}>删除</button>
              )}
            </div>
          ))}
        </div>
      )}

      <div className="hint" style={{ fontWeight: 600, margin: '4px 0' }}>核心设定</div>
      <div className="row">
        <div style={{ flex: 1 }}>
          <label>风格</label>
          <input value={form.style || ''} onChange={set('style')} placeholder="废土科幻/中世纪哥特" />
        </div>
        <div style={{ flex: 1 }}>
          <label>时间与天气</label>
          <input value={form.time_weather || ''} onChange={set('time_weather')} placeholder="雨夜/正午烈日" />
        </div>
        <div style={{ flex: 1 }}>
          <label>色彩基调</label>
          <input value={form.color_tone || ''} onChange={set('color_tone')} placeholder="冷色低饱和" />
        </div>
      </div>

      <div className="hint" style={{ fontWeight: 600, margin: '8px 0 4px' }}>空间与结构</div>
      <label>空间描述</label>
      <textarea rows={2} value={form.space_desc || ''} onChange={set('space_desc')} />
      <div className="row">
        <div style={{ flex: 1 }}>
          <label>材质与细节</label>
          <input value={form.materials || ''} onChange={set('materials')} placeholder="生锈管道/破碎玻璃" />
        </div>
        <div style={{ flex: 1 }}>
          <label>远景 / 边界</label>
          <input value={form.vista || ''} onChange={set('vista')} placeholder="窗外景象/视野尽头" />
        </div>
      </div>

      <div className="hint" style={{ fontWeight: 600, margin: '8px 0 4px' }}>光影与镜头</div>
      <div className="row">
        <div style={{ flex: 1 }}>
          <label>光源设定</label>
          <input value={form.light_source || ''} onChange={set('light_source')} placeholder="光从哪来" />
        </div>
        <div style={{ flex: 1 }}>
          <label>光影技术</label>
          <input value={form.light_tech || ''} onChange={set('light_tech')} placeholder="丁达尔/全局光照" />
        </div>
      </div>
      <div className="row">
        <div style={{ flex: 1 }}>
          <label>视角</label>
          <input value={form.viewpoint || ''} onChange={set('viewpoint')} placeholder="广角仰视/无人机俯拍" />
        </div>
        <div style={{ flex: 1 }}>
          <label>构图</label>
          <input value={form.composition || ''} onChange={set('composition')} placeholder="对称/三分法/引导线" />
        </div>
      </div>

      <div className="hint" style={{ fontWeight: 600, margin: '12px 0 4px' }}>可灵场景主体</div>
      <label>主体 ID（Element ID）</label>
      <input
        value={form.kling_element_id || ''}
        readOnly
        placeholder="合成成功后显示"
        style={{ fontFamily: 'monospace', opacity: form.kling_element_id ? 1 : 0.6 }}
      />
      <p className="hint" style={{ fontSize: 12, margin: '4px 0 8px' }}>
        使用上方全部场景图作为参考，调用可灵 API 合成主体（标签：场景 o_106）。
      </p>
      <button
        className="small primary"
        disabled={creatingElement || doneImages.length === 0}
        onClick={createElement}
      >
        {creatingElement ? '合成中…' : form.kling_element_id ? '重新合成主体 ID' : '合成场景主体 ID'}
      </button>

      <p className="hint" style={{ marginTop: 8 }}>
        画质渲染（UE5/Octane/8k）+「画面无角色」已自动注入。
      </p>
    </div>
  )
}

// ============ 分镜脚本 ============
function StoryboardPanel({ project, notify }) {
  const [chapters, setChapters] = useState([])
  const [activeChapterId, setActiveChapterId] = useState(null)
  const [board, setBoard] = useState(null)
  const [loading, setLoading] = useState(false)
  const [videos, setVideos] = useState({}) // shotId -> video record
  const [previewSrc, setPreviewSrc] = useState(null) // 放大预览的图片 URL
  const [chapterVideo, setChapterVideo] = useState(null) // 章节成片
  const [characters, setCharacters] = useState([]) // 项目角色，供"人物"多选
  const [projectScenes, setProjectScenes] = useState([]) // 项目已登记场景（供 scene 输入参考）

  useEffect(() => {
    api.listChapters(project.id).then((cs) => {
      setChapters(cs)
      if (cs.length && !activeChapterId) setActiveChapterId(cs[0].id)
    }).catch((e) => notify(e.message, true))
    api.listCharacters(project.id).then(setCharacters).catch(() => {})
    api.listScenes(project.id).then(setProjectScenes).catch(() => {})
  }, [project.id])

  useEffect(() => {
    if (!activeChapterId) return
    setBoard(null)
    setVideos({})
    setChapterVideo(null)
    api.getStoryboard(activeChapterId).then((b) => {
      setBoard(b)
      if (b) loadShotVideos(b.shots)
    }).catch((e) => notify(e.message, true))
    api.getChapterVideo(activeChapterId).then((cv) => {
      setChapterVideo(cv)
      if (cv && cv.status === 'processing') pollChapterVideo(activeChapterId)
    }).catch(() => {})
  }, [activeChapterId])

  async function loadShotVideos(shots) {
    const entries = await Promise.all(shots.map(async (s) => {
      try { return [s.id, await api.getShotVideo(s.id)] }
      catch { return [s.id, null] }
    }))
    const map = {}
    for (const [id, v] of entries) if (v) map[id] = v
    setVideos(map)
    for (const [id, v] of entries) {
      if (v && v.status === 'processing') pollShotVideo(id)
    }
  }

  function pollShotVideo(shotId) {
    const timer = setInterval(async () => {
      try {
        const v = await api.getShotVideo(shotId)
        if (!v) { clearInterval(timer); return }
        setVideos((m) => ({ ...m, [shotId]: v }))
        if (v.status === 'done' || v.status === 'failed') {
          clearInterval(timer)
          if (v.status === 'done') notify('镜头视频生成完成')
          else notify(`镜头视频失败：${v.error}`, true)
        }
      } catch { clearInterval(timer) }
    }, 8000)
  }

  async function genShotVideo(shotId) {
    const shot = board?.shots.find((s) => s.id === shotId)
    try {
      if (shot) await persistShot(shot)
      const v = await api.generateShotVideo(shotId)
      setVideos((m) => ({ ...m, [shotId]: v }))
      notify('已提交图生视频，约需几分钟，完成后自动显示')
      pollShotVideo(shotId)
    } catch (e) { notify(e.message, true) }
  }

  const [batchRunning, setBatchRunning] = useState(false)

  async function genAllShotVideos() {
    if (!board) return
    setBatchRunning(true)
    try {
      const res = await api.generateChapterShotVideos(activeChapterId)
      // 合并已排队的视频状态
      setVideos((m) => {
        const next = { ...m }
        for (const v of res.queued) if (v.ref_id) next[v.ref_id] = v
        return next
      })
      // 对所有排队的镜头启动轮询
      for (const v of res.queued) if (v.ref_id) pollShotVideo(v.ref_id)
      let msg = `已批量提交 ${res.queued.length} 个镜头`
      if (res.skipped.length) {
        msg += `，跳过 ${res.skipped.length} 个（缺出场角色或基准图）`
      }
      notify(msg, res.skipped.length > 0)
    } catch (e) { notify(e.message, true) } finally { setBatchRunning(false) }
  }

  function pollChapterVideo(chapterId) {
    const timer = setInterval(async () => {
      try {
        const cv = await api.getChapterVideo(chapterId)
        if (!cv) { clearInterval(timer); return }
        setChapterVideo(cv)
        if (cv.status === 'done' || cv.status === 'failed') {
          clearInterval(timer)
          if (cv.status === 'done') notify('章节成片合成完成')
          else notify(`合成失败：${cv.error}`, true)
        }
      } catch { clearInterval(timer) }
    }, 5000)
  }

  async function composeChapter() {
    try {
      const cv = await api.composeChapter(activeChapterId)
      setChapterVideo(cv)
      notify('已开始合成本章成片…')
      pollChapterVideo(activeChapterId)
    } catch (e) { notify(e.message, true) }
  }

  // 已出片的镜头数（供合成按钮判断）
  const doneShotCount = Object.values(videos).filter((v) => v?.status === 'done').length
  const processingCount = Object.values(videos).filter((v) => v?.status === 'processing').length

  async function generate() {
    setLoading(true)
    try {
      const b = await api.generateStoryboard(activeChapterId)
      setBoard(b)
      notify(`分镜生成完成，共 ${b.shots.length} 个镜头`)
    } catch (e) { notify(e.message, true) } finally { setLoading(false) }
  }

  async function persistShot(shot, { notify: showNotify = false } = {}) {
    await api.editShot(shot.id, {
      shot_size: shot.shot_size || '',
      camera: shot.camera || '',
      character_ids: shot.character_ids || [],
      description: shot.description || '',
      sound: shot.sound || '',
      lighting: shot.lighting || '',
      scene: shot.scene || '',
      duration: normalizeShotDuration(shot.duration),
    })
    if (showNotify) notify('镜头已保存')
  }

  async function saveShot(shot) {
    try {
      await persistShot(shot, { notify: true })
    } catch (e) { notify(e.message, true) }
  }

  async function delShot(shotId) {
    if (!confirm('删除这个镜头？')) return
    try {
      await api.deleteShot(shotId)
      setBoard((b) => ({ ...b, shots: b.shots.filter((s) => s.id !== shotId) }))
    } catch (e) { notify(e.message, true) }
  }

  function setShotField(shotId, k, v) {
    setBoard((b) => ({ ...b, shots: b.shots.map((s) => s.id === shotId ? { ...s, [k]: v } : s) }))
  }

  function toggleShotCharacter(shotId, charId) {
    setBoard((b) => ({
      ...b,
      shots: b.shots.map((s) => {
        if (s.id !== shotId) return s
        const ids = s.character_ids || []
        const next = ids.includes(charId)
          ? ids.filter((x) => x !== charId)
          : [...ids, charId]
        return { ...s, character_ids: next }
      }),
    }))
  }

  if (chapters.length === 0) {
    return <div className="empty">还没有章节。请先到「创作工作台」生成章节正文。</div>
  }

  return (
    <div style={{ display: 'grid', gridTemplateColumns: '220px 1fr', gap: 16 }}>
      {/* 章节列表 */}
      <div className="panel" style={{ alignSelf: 'start', maxHeight: '70vh', overflowY: 'auto' }}>
        <h3 style={{ marginTop: 0 }}>章节</h3>
        {chapters.map((c) => (
          <div key={c.id}
            onClick={() => setActiveChapterId(c.id)}
            className={`chapter-node ${activeChapterId === c.id ? 'active' : ''}`}
            style={{ cursor: 'pointer', marginBottom: 6 }}>
            <div style={{ minWidth: 0 }}>
              <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{c.title}</div>
            </div>
            {c.status && <span className={`tag ${c.status}`}>{c.status === 'confirmed' ? '✓' : '草稿'}</span>}
          </div>
        ))}
      </div>

      {/* 分镜内容 */}
      <div>
        <div className="row" style={{ marginBottom: 12 }}>
          <button className="primary" disabled={loading} onClick={generate}>
            {loading ? '拆分镜中…' : board ? '重新拆分镜' : '拆分镜'}
          </button>
          {board && board.shots.length > 0 && (
            <button disabled={batchRunning || processingCount > 0} onClick={genAllShotVideos}>
              {batchRunning ? '提交中…'
                : processingCount > 0 ? `生成中(${processingCount})…`
                : '🎬 一键生成全部镜头'}
            </button>
          )}
          {board && (
            <span className="hint">
              共 {board.shots.length} 个镜头，已出片 {doneShotCount} 个
              {processingCount > 0 && `，生成中 ${processingCount} 个`}
            </span>
          )}
          {board && board.shots.length > 0 && (
            <>
              <div className="spacer" />
              <button disabled={doneShotCount === 0 || chapterVideo?.status === 'processing'}
                onClick={composeChapter}>
                {chapterVideo?.status === 'processing' ? '合成中…' : '🎞 合成本章成片'}
              </button>
            </>
          )}
        </div>

        {/* 章节成片 */}
        {chapterVideo && (
          <div className="panel" style={{ marginBottom: 12 }}>
            <h3 style={{ marginTop: 0 }}>本章成片</h3>
            {chapterVideo.status === 'processing' && (
              <div className="hint">⏳ ffmpeg 合成中…</div>
            )}
            {chapterVideo.status === 'failed' && (
              <span className="error">合成失败：{chapterVideo.error}</span>
            )}
            {chapterVideo.status === 'done' && (
              <div>
                <video src={api.videoFileUrl(chapterVideo.id)} controls
                  style={{ width: '100%', maxWidth: 640, borderRadius: 8 }} />
                <div className="row" style={{ marginTop: 6 }}>
                  <span className="hint">总时长 {chapterVideo.duration}s</span>
                  <a href={api.videoFileUrl(chapterVideo.id)} download>
                    <button className="small">下载成片</button>
                  </a>
                </div>
              </div>
            )}
          </div>
        )}

        {!board ? (
          <div className="empty">还没有分镜。点上方「拆分镜」从本章正文生成镜头。</div>
        ) : (
          board.shots.map((s, i) => (
            <div key={s.id} className="panel" style={{ marginBottom: 12 }}>
              <div className="row" style={{ marginBottom: 8 }}>
                <b>镜头 {i + 1}</b>
                {s.shot_size && <span className="tag draft">{s.shot_size}</span>}
                {s.camera && <span className="tag draft">{s.camera}</span>}
                <span className="hint">{s.duration}s</span>
                <div className="spacer" />
                <button className="small" onClick={() => saveShot(s)}>保存</button>
                <button className="small danger" onClick={() => delShot(s.id)}>删</button>
              </div>

              <div className="row" style={{ marginBottom: 4 }}>
                <div style={{ flex: 1 }}>
                  <label>景别</label>
                  <input value={s.shot_size || ''} list={`shotsize-${s.id}`}
                    placeholder="下拉选或自定义"
                    onChange={(e) => setShotField(s.id, 'shot_size', e.target.value)} />
                  <datalist id={`shotsize-${s.id}`}>
                    {SHOT_SIZE_OPTIONS.map((o) => <option key={o} value={o} />)}
                  </datalist>
                </div>
                <div style={{ flex: 1 }}>
                  <label>运镜</label>
                  <input value={s.camera || ''} list={`camera-${s.id}`}
                    placeholder="下拉选或自定义"
                    onChange={(e) => setShotField(s.id, 'camera', e.target.value)} />
                  <datalist id={`camera-${s.id}`}>
                    {CAMERA_OPTIONS.map((o) => <option key={o} value={o} />)}
                  </datalist>
                </div>
                <div style={{ width: 110 }}>
                  <label>时长(秒)</label>
                  <input
                    type="number"
                    min={SHOT_DURATION_MIN}
                    max={SHOT_DURATION_MAX}
                    step={1}
                    value={s.duration ?? SHOT_DURATION_MIN}
                    onChange={(e) => {
                      const v = e.target.value
                      if (v === '') {
                        setShotField(s.id, 'duration', '')
                        return
                      }
                      const n = Number(v)
                      if (!Number.isNaN(n)) setShotField(s.id, 'duration', n)
                    }}
                    onBlur={() => setShotField(s.id, 'duration', normalizeShotDuration(s.duration))}
                  />
                </div>
              </div>

              <div className="row" style={{ marginBottom: 4 }}>
                <div style={{ flex: 1 }}>
                  <label>场景</label>
                  <input value={s.scene || ''} list={`shot-scene-${s.id}`}
                    placeholder="地点/环境，可自由填写"
                    onChange={(e) => setShotField(s.id, 'scene', e.target.value)} />
                  <datalist id={`shot-scene-${s.id}`}>
                    {projectScenes.map((p) => (
                      <option key={p.id} value={p.name} />
                    ))}
                  </datalist>
                </div>
                <div style={{ flex: 1 }}>
                  <label>光影</label>
                  <input value={s.lighting || ''} placeholder="黄昏暖光/冷色逆光/夜晚月光"
                    onChange={(e) => setShotField(s.id, 'lighting', e.target.value)} />
                </div>
              </div>

              <label>人物</label>
              <div className="row" style={{ flexWrap: 'wrap', gap: 6, marginBottom: 6 }}>
                {characters.length === 0 && <span className="hint">本作还没有登记角色</span>}
                {characters.map((c) => {
                  const on = (s.character_ids || []).includes(c.id)
                  return (
                    <span key={c.id}
                      onClick={() => toggleShotCharacter(s.id, c.id)}
                      className={`tag ${on ? 'confirmed' : 'draft'}`}
                      style={{ cursor: 'pointer', userSelect: 'none' }}>
                      {on ? '✓ ' : ''}{c.name}
                    </span>
                  )
                })}
              </div>

              <label>画面描述（含台词；有主体 ID 时用 名称{'<<<'}主体ID{'>>>'} 三尖括号标记）</label>
              <textarea rows={3} value={s.description}
                placeholder='例：中景，教室<<<123456>>>内，小明<<<308901>>>说："你好"'
                onChange={(e) => setShotField(s.id, 'description', e.target.value)} />
              <label>音效</label>
              <input value={s.sound || ''} placeholder="环境音/拟音，如 雨声、脚步声"
                onChange={(e) => setShotField(s.id, 'sound', e.target.value)} />

              {/* 图生视频 */}
              <ShotMedia
                video={videos[s.id]}
                onGenerateVideo={() => genShotVideo(s.id)}
                onPreviewImage={setPreviewSrc}
              />
            </div>
          ))
        )}
      </div>

      {previewSrc && (
        <ImageLightbox src={previewSrc} onClose={() => setPreviewSrc(null)} />
      )}
    </div>
  )
}

function ImageLightbox({ src, onClose }) {
  return (
    <div
      role="dialog"
      aria-modal="true"
      onClick={onClose}
      style={{
        position: 'fixed', inset: 0, zIndex: 9999,
        background: 'rgba(0,0,0,0.85)', display: 'flex',
        alignItems: 'center', justifyContent: 'center', padding: 24,
        cursor: 'zoom-out',
      }}
    >
      <img
        src={src}
        alt="预览"
        onClick={(e) => e.stopPropagation()}
        style={{
          maxWidth: '95vw', maxHeight: '95vh', objectFit: 'contain',
          borderRadius: 8, cursor: 'default',
        }}
      />
    </div>
  )
}

function FrameThumb({ label, src, onPreview }) {
  if (!src) return null
  return (
    <div style={{ flex: 1, minWidth: 0 }}>
      <div className="hint" style={{ marginBottom: 4 }}>{label}</div>
      <img
        src={src}
        alt={label}
        title="点击放大"
        onClick={() => onPreview(src)}
        style={{
          width: '100%', maxWidth: 240, borderRadius: 8, display: 'block',
          cursor: 'zoom-in', border: '1px solid var(--border)',
        }}
      />
    </div>
  )
}

// 单个镜头的视频区（首尾帧从成片自动提取，只读可放大）
function ShotMedia({ video, onGenerateVideo, onPreviewImage }) {
  const videoStatus = video?.status

  return (
    <div style={{ marginTop: 10, paddingTop: 10, borderTop: '1px solid var(--border)' }}>
      <div>
        <div className="row" style={{ marginBottom: 6 }}>
          <b>镜头视频</b>
          <div className="spacer" />
          {videoStatus !== 'processing' && (
            <button className="small primary" onClick={onGenerateVideo}>
              🎬 {videoStatus === 'done' ? '重新生成视频' : '生成镜头视频'}
            </button>
          )}
        </div>
        {videoStatus !== 'done' && (
          <div className="hint" style={{ marginBottom: 6 }}>
            直接图生视频；首镜用角色参考图作种子，后续镜头优先衔接上一镜尾帧。
          </div>
        )}
        {videoStatus === 'processing' && (
          <div className="hint">⏳ 视频生成中（约几分钟，可切走，完成后自动提取首尾帧）…</div>
        )}
        {videoStatus === 'failed' && (
          <div className="row">
            <span className="error">生成失败：{video.error}</span>
            <button className="small" onClick={onGenerateVideo}>重试</button>
          </div>
        )}
        {videoStatus === 'done' && (
          <div>
            <video src={api.videoFileUrl(video.id)} controls
              style={{ width: '100%', maxWidth: 480, borderRadius: 8 }} />
            <div className="row" style={{ marginTop: 6, marginBottom: 10 }}>
              <span className="hint">时长 {video.duration}s</span>
            </div>
            {(video.poster_url || video.last_frame_url) && (
              <div>
                <div className="hint" style={{ marginBottom: 6 }}>成片首尾帧（只读，点击放大）</div>
                <div className="row" style={{ gap: 12, alignItems: 'flex-start', flexWrap: 'wrap' }}>
                  <FrameThumb label="首帧" src={video.poster_url} onPreview={onPreviewImage} />
                  <FrameThumb label="尾帧" src={video.last_frame_url} onPreview={onPreviewImage} />
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
