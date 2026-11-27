import React, { useEffect, useRef, useState } from 'react'
import { api } from '../api'

function synopsisMark(text) {
  let hash = 0
  const value = (text || '').trim()
  for (let i = 0; i < value.length; i++) {
    hash = (Math.imul(hash, 31) + value.charCodeAt(i)) >>> 0
  }
  return hash.toString(16)
}

function readSynopsisScore(raw) {
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw)
    if (!parsed || typeof parsed !== 'object') return null
    return parsed
  } catch {
    return null
  }
}

function scoreText(value) {
  return value == null ? '没评上' : `${value}/10`
}

function SynopsisScore({ text, raw }) {
  const score = readSynopsisScore(raw)
  if (!score) return null
  const edited = score.mark && score.mark !== synopsisMark(text)
  const verbosity = score.verbosity || {}
  const verboseHigh = (verbosity.score ?? 0) >= 6
  return (
    <div className="score-list">
      <p className="hint" style={{ margin: 0 }}>
        啰嗦分高表示越像正文。条例分高表示这条癫文宝经落到了具体情节上。
        {edited ? ' 概述已改过，下面仍是生成时的评分。' : ''}
      </p>
      <div className="score-row">
        <div className={verboseHigh ? 'decision-label bad' : 'decision-label good'}>
          啰嗦 {scoreText(verbosity.score)}
        </div>
        {verbosity.note && <div className="rule">{verbosity.note}</div>}
      </div>
      {(score.rules || []).map((rule) => (
        <div className="score-row" key={rule.index}>
          <div className={(rule.score ?? 0) >= 6 ? 'decision-label good' : 'decision-label bad'}>
            条例 {rule.index} {scoreText(rule.score)}
          </div>
          <div className="rule">{rule.rule}</div>
          {rule.note && <div className="hint">{rule.note}</div>}
        </div>
      ))}
    </div>
  )
}

export default function BibleEditor({ project, onSaved, notify }) {
  const [form, setForm] = useState(project)
  const [chars, setChars] = useState([])
  const [saving, setSaving] = useState(false)
  const [idea, setIdea] = useState('')
  const [coreCount, setCoreCount] = useState('')
  const [filling, setFilling] = useState(false)
  const [writingSynopsis, setWritingSynopsis] = useState(false)

  useEffect(() => { setForm(project) }, [project.id])

  const ideaRef = useRef(idea)
  const countRef = useRef(coreCount)
  const synopsisRef = useRef('')
  const projectIdRef = useRef(null)
  const skipSave = useRef(true)
  ideaRef.current = idea
  countRef.current = coreCount
  synopsisRef.current = form.full_synopsis || ''

  function countPayload(raw) {
    const n = Number(raw)
    if (!Number.isFinite(n) || n <= 0) return 0
    return Math.floor(n)
  }

  function saveDraft(id, draftIdea, draftCount, draftSynopsis) {
    return api.updateProject(id, {
      opening_idea: draftIdea,
      core_character_count: countPayload(draftCount),
      full_synopsis: draftSynopsis,
    }).then(() => onSaved?.()).catch((e) => notify(e.message, true))
  }

  useEffect(() => {
    const prevId = projectIdRef.current
    if (prevId != null && prevId !== project.id) {
      saveDraft(prevId, ideaRef.current, countRef.current, synopsisRef.current)
    }
    projectIdRef.current = project.id
    skipSave.current = true
    const nextIdea = project.opening_idea || ''
    const nextCount = project.core_character_count > 0 ? String(project.core_character_count) : ''
    setIdea(nextIdea)
    setCoreCount(nextCount)
    ideaRef.current = nextIdea
    countRef.current = nextCount
  }, [project.id])

  useEffect(() => {
    if (skipSave.current) {
      skipSave.current = false
      return
    }
    const timer = setTimeout(() => {
      saveDraft(project.id, idea, coreCount, form.full_synopsis || '')
    }, 400)
    return () => clearTimeout(timer)
  }, [idea, coreCount, form.full_synopsis])

  useEffect(() => {
    api.listCharacters(project.id).then(setChars).catch((e) => notify(e.message, true))
  }, [project.id])

  function set(k, v) { setForm((f) => ({ ...f, [k]: v })) }

  function applyFill(data) {
    setForm((f) => ({ ...f, ...data.project }))
    setChars(data.characters || [])
    onSaved?.()
  }

  async function autofill(replace = false) {
    const text = idea.trim()
    if (!text) {
      notify('先写下开头思路', true)
      return
    }
    const hasContent = ['premise', 'mood', 'style', 'world_rules'].some((k) => (form[k] || '').trim())
      || chars.length > 0
    if (hasContent && !replace && !confirm(
      '已有故事圣经或角色档案。自动填充会覆盖这些内容，并删除现有角色的形象设定。继续？'
    )) return

    const typed = Number(coreCount)
    const coreCharacterCount = Number.isFinite(typed) && typed > 0 ? Math.floor(typed) : 17
    setFilling(true)
    try {
      const data = await api.autofillBible(project.id, {
        idea: text,
        replace: hasContent || replace,
        core_character_count: coreCharacterCount,
      })
      applyFill(data)
      notify(`已填好故事圣经和 ${data.characters.length} 个角色`)
    } catch (e) {
      notify(e.message, true)
    } finally {
      setFilling(false)
    }
  }

  async function generateSynopsis() {
    const text = idea.trim()
    if (!text) {
      notify('先写下开头或思路', true)
      return
    }
    if ((form.full_synopsis || '').trim() && !confirm('已有全文概述。重新生成会覆盖框里的文字。继续？')) return
    setWritingSynopsis(true)
    try {
      const data = await api.generateFullSynopsis(project.id, { idea: text })
      setForm((f) => ({ ...f, ...data.project }))
      onSaved?.()
      const length = data.length || (data.project.full_synopsis || '').length
      const ready = length >= 8000 ? `共 ${length} 字` : `这次只有 ${length} 字，可以再生成一次`
      notify(data.from_backup ? `已换上备好的全文概述，${ready}` : `全文概述已生成，${ready}`)
    } catch (e) {
      notify(e.message, true)
    } finally {
      setWritingSynopsis(false)
    }
  }

  async function fillFromSynopsis() {
    const synopsis = (form.full_synopsis || '').trim()
    if (!synopsis) {
      notify('先写好全文概述', true)
      return
    }
    const hasContent = ['premise', 'mood', 'style', 'world_rules'].some((k) => (form[k] || '').trim())
      || chars.length > 0
    if (hasContent && !confirm(
      '会按这篇概述重写故事背景、氛围、文风、世界观和角色档案。全文概述保持你改过的文字。已有角色的形象设定会被删除。继续？'
    )) return
    const typed = Number(coreCount)
    const coreCharacterCount = Number.isFinite(typed) && typed > 0 ? Math.floor(typed) : 17
    setFilling(true)
    try {
      const data = await api.bibleFromSynopsis(project.id, {
        synopsis,
        replace: hasContent,
        core_character_count: coreCharacterCount,
      })
      applyFill(data)
      notify(`已按全文概述填好故事圣经和 ${data.characters.length} 个角色`)
    } catch (e) {
      notify(e.message, true)
    } finally {
      setFilling(false)
    }
  }

  async function save() {
    setSaving(true)
    try {
      await api.updateProject(project.id, {
        title: form.title, premise: form.premise, mood: form.mood,
        style: form.style, world_rules: form.world_rules,
        full_synopsis: form.full_synopsis,
        target_words: Number(form.target_words) || 0,
      })
      notify('故事圣经已保存')
      onSaved?.()
    } catch (e) { notify(e.message, true) } finally { setSaving(false) }
  }

  async function addChar() {
    const name = prompt('角色名：')
    if (!name) return
    try {
      const c = await api.createCharacter(project.id, { name })
      setChars((cs) => [...cs, c])
    } catch (e) { notify(e.message, true) }
  }

  async function saveChar(c) {
    try {
      await api.updateCharacter(c.id, {
        name: c.name, role: c.role, description: c.description, state: c.state,
      })
      notify(`已保存角色「${c.name}」`)
    } catch (e) { notify(e.message, true) }
  }

  async function delChar(id) {
    if (!confirm('删除该角色？')) return
    try {
      await api.deleteCharacter(id)
      setChars((cs) => cs.filter((c) => c.id !== id))
    } catch (e) { notify(e.message, true) }
  }

  function setCharField(id, k, v) {
    setChars((cs) => cs.map((c) => (c.id === id ? { ...c, [k]: v } : c)))
  }

  return (
    <>
      <div className="panel">
        <h2>故事圣经</h2>
        <p className="hint">
          这些设定会作为"长期记忆"始终提供给模型，是保持千万字一致性的根基。
        </p>
        <label>开头思路</label>
        <textarea rows={3} value={idea}
          placeholder="用几句话写下故事从哪里开始、你想写的那个人和那件事"
          onChange={(e) => setIdea(e.target.value)} />
        <div className="row" style={{ marginTop: 8, alignItems: 'flex-end' }}>
          <div style={{ width: 160 }}>
            <label>核心角色人数</label>
            <input type="number" min="1" value={coreCount} placeholder="17"
              onChange={(e) => setCoreCount(e.target.value)} />
          </div>
          <button onClick={() => autofill(false)} disabled={filling || writingSynopsis || !idea.trim()}>
            {filling ? '填充中…' : '自动填充圣经与角色'}
          </button>
        </div>
        <p className="hint">
          不填核心角色人数时按 17 人生成。这一步按开头思路填写故事圣经和角色，不改全文概述。已有圣经或角色时，会先确认再覆盖。
        </p>

        <label>全文概述</label>
        <textarea rows={16} value={form.full_synopsis || ''}
          placeholder="给作者看的全书情节：开端、转折、读者会猜错的方向、实际结局。可以自己改。"
          onChange={(e) => set('full_synopsis', e.target.value)} />
        <p className="hint">
          当前 {(form.full_synopsis || '').trim().length} 字。生成目标是 8000 到 10000 字的情节提要，不写对白和情景。依据开头思路和癫文宝经，覆盖一部预计百万字小说的阶段转折。框里的文字可以随意改，改完会自动保存。再点一次生成时，如果后台已经备好下一篇，会立刻换上，并再准备一篇。
        </p>
        <SynopsisScore text={form.full_synopsis || ''} raw={form.synopsis_score} />
        <div className="row">
          <button onClick={generateSynopsis} disabled={writingSynopsis || filling || !idea.trim()}>
            {writingSynopsis ? '概述生成中…' : '生成全文概述'}
          </button>
          <button className="primary" onClick={fillFromSynopsis}
            disabled={filling || writingSynopsis || !(form.full_synopsis || '').trim()}>
            {filling ? '填充中…' : '用这篇概述生成故事圣经'}
          </button>
        </div>

        <label>作品标题</label>
        <input value={form.title || ''} onChange={(e) => set('title', e.target.value)} />

        <label>故事背景 / 核心设定</label>
        <textarea rows={4} value={form.premise || ''}
          placeholder="世界设定、时代、核心冲突、故事的起点……"
          onChange={(e) => set('premise', e.target.value)} />

        <label>整体氛围</label>
        <input value={form.mood || ''}
          placeholder="如：阴郁悬疑 / 热血爽快 / 治愈轻松"
          onChange={(e) => set('mood', e.target.value)} />

        <label>文风要求</label>
        <textarea rows={2} value={form.style || ''}
          placeholder="人称、叙事节奏、语言风格、参考作品等"
          onChange={(e) => set('style', e.target.value)} />

        <label>世界观规则 / 设定约束</label>
        <textarea rows={3} value={form.world_rules || ''}
          placeholder="力量体系、社会结构、不可违背的设定红线……"
          onChange={(e) => set('world_rules', e.target.value)} />

        <label>目标总字数（选填，0=不限）</label>
        <input type="number" value={form.target_words || 0}
          onChange={(e) => set('target_words', e.target.value)} />

        <div className="row end" style={{ marginTop: 14 }}>
          <button className="primary" onClick={save} disabled={saving}>
            {saving ? '保存中…' : '保存圣经'}
          </button>
        </div>
      </div>

      <div className="panel">
        <div className="row">
          <h2 style={{ margin: 0 }}>角色档案</h2>
          <div className="spacer" />
          <button onClick={addChar}>+ 添加角色</button>
        </div>
        <p className="hint">角色的"当前状态"会在每章确认后由系统自动更新。</p>
        {chars.length === 0 && <div className="empty">还没有角色。</div>}
        {chars.map((c) => (
          <div className="char-card" key={c.id}>
            <div className="row">
              <input style={{ maxWidth: 160 }} value={c.name}
                onChange={(e) => setCharField(c.id, 'name', e.target.value)} />
              <input style={{ maxWidth: 140 }} value={c.role || ''} placeholder="角色定位"
                onChange={(e) => setCharField(c.id, 'role', e.target.value)} />
              <div className="spacer" />
              <button className="small" onClick={() => saveChar(c)}>保存</button>
              <button className="small danger" onClick={() => delChar(c.id)}>删除</button>
            </div>
            <label>性格 / 设定</label>
            <textarea rows={2} value={c.description || ''}
              onChange={(e) => setCharField(c.id, 'description', e.target.value)} />
            {c.state && <div className="state">当前状态：{c.state}</div>}
          </div>
        ))}
      </div>
    </>
  )
}
