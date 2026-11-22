import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function BibleEditor({ project, onSaved, notify }) {
  const [form, setForm] = useState(project)
  const [chars, setChars] = useState([])
  const [saving, setSaving] = useState(false)
  const [idea, setIdea] = useState('')
  const [filling, setFilling] = useState(false)

  useEffect(() => { setForm(project); setIdea('') }, [project.id])

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

    setFilling(true)
    try {
      const data = await api.autofillBible(project.id, { idea: text, replace: hasContent || replace })
      applyFill(data)
      notify(`已填好故事圣经和 ${data.characters.length} 个角色`)
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
        <div className="row end" style={{ marginTop: 8 }}>
          <button onClick={() => autofill(false)} disabled={filling || !idea.trim()}>
            {filling ? '填充中…' : '自动填充圣经与角色'}
          </button>
        </div>
        <p className="hint">
          填充时会参考这部作品的癫文宝经。已有圣经或角色时，会先确认再覆盖。
        </p>
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
