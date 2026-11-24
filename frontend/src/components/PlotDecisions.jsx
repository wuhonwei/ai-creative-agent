import React, { useEffect, useRef, useState } from 'react'
import { api } from '../api'

function OptionCard({ item, busy, onChoose }) {
  return (
    <div className="decision-card">
      <h3>{item.title}</h3>
      <p>{item.plot}</p>
      <div className="decision-label">新人物</div>
      <p>{item.new_characters}</p>
      <div className="decision-label">新势力</div>
      <p>{item.new_factions}</p>
      <div className="decision-label good">优点</div>
      <p>{item.merits}</p>
      <div className="decision-label bad">缺点</div>
      <p>{item.drawbacks}</p>
      <button className="primary" disabled={busy} onClick={() => onChoose(item.id)}>
        选择这个方向
      </button>
    </div>
  )
}

export default function PlotDecisions({ project, onSaved, notify }) {
  const [idea, setIdea] = useState('')
  const [chosen, setChosen] = useState([])
  const [options, setOptions] = useState([])
  const [busy, setBusy] = useState(false)

  const ideaRef = useRef(idea)
  const projectIdRef = useRef(null)
  const skipSave = useRef(true)
  ideaRef.current = idea

  function saveIdea(id, text) {
    return api.updateProject(id, { opening_idea: text })
      .then(() => onSaved?.())
      .catch((e) => notify(e.message, true))
  }

  useEffect(() => {
    const prevId = projectIdRef.current
    if (prevId != null && prevId !== project.id) {
      saveIdea(prevId, ideaRef.current)
    }
    projectIdRef.current = project.id
    skipSave.current = true
    const nextIdea = project.opening_idea || ''
    setIdea(nextIdea)
    ideaRef.current = nextIdea
    api.listPlotDecisions(project.id)
      .then((data) => {
        setChosen(data.chosen || [])
        setOptions(data.options || [])
      })
      .catch((e) => notify(e.message, true))
  }, [project.id])

  useEffect(() => {
    if (skipSave.current) {
      skipSave.current = false
      return
    }
    const timer = setTimeout(() => saveIdea(project.id, idea), 400)
    return () => clearTimeout(timer)
  }, [idea])

  function apply(data) {
    setChosen(data.chosen || [])
    setOptions(data.options || [])
  }

  async function generate() {
    const text = idea.trim()
    if (!text) {
      notify('先写下开头或思路', true)
      return
    }
    setBusy(true)
    try {
      apply(await api.generatePlotOptions(project.id, { idea: text }))
    } catch (e) {
      notify(e.message, true)
    } finally {
      setBusy(false)
    }
  }

  async function choose(optionId) {
    setBusy(true)
    try {
      const chosenData = await api.choosePlotOption(project.id, optionId)
      apply(chosenData)
      const next = await api.generatePlotOptions(project.id, { idea: idea.trim() })
      apply(next)
    } catch (e) {
      notify(e.message, true)
    } finally {
      setBusy(false)
    }
  }

  async function undo() {
    if (!confirm('撤回最近一条决策？当前待选的三个方向也会清掉。')) return
    setBusy(true)
    try {
      apply(await api.undoPlotDecision(project.id))
    } catch (e) {
      notify(e.message, true)
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <div className="panel">
        <h2>剧情决策</h2>
        <p className="hint">
          写下开头或思路后生成三个方向。每个方向都会写出新登场的人物、势力，以及优点和缺点。
          选定后进入下面的决策列表；下一步会接上全部已选决策、这段思路和癫文宝经。刷新只换掉还没选的三个方向。
        </p>
        <label>开头或思路</label>
        <textarea rows={4} value={idea}
          placeholder="故事从哪里开始，或者你已经想好的那一步"
          onChange={(e) => setIdea(e.target.value)} />
        <div className="row" style={{ marginTop: 8 }}>
          <button className="primary" onClick={generate} disabled={busy || !idea.trim()}>
            {busy ? '生成中…' : (options.length ? '刷新三个方向' : '生成三个方向')}
          </button>
        </div>
        {options.length > 0 && (
          <div className="decision-grid">
            {options.map((item) => (
              <OptionCard key={item.id} item={item} busy={busy} onChoose={choose} />
            ))}
          </div>
        )}
      </div>

      <div className="panel">
        <div className="row">
          <h2 style={{ margin: 0 }}>已选决策</h2>
          <div className="spacer" />
          {chosen.length > 0 && (
            <button className="small" disabled={busy} onClick={undo}>撤回最近一条</button>
          )}
        </div>
        {chosen.length === 0 && <div className="empty">还没有选定的决策。</div>}
        {chosen.map((item, index) => (
          <div className="decision-card chosen" key={item.id}>
            <h3>第 {index + 1} 步 · {item.title}</h3>
            <p>{item.plot}</p>
            <div className="decision-label">新人物</div>
            <p>{item.new_characters}</p>
            <div className="decision-label">新势力</div>
            <p>{item.new_factions}</p>
            <div className="decision-label good">优点</div>
            <p>{item.merits}</p>
            <div className="decision-label bad">缺点</div>
            <p>{item.drawbacks}</p>
          </div>
        ))}
      </div>
    </>
  )
}
