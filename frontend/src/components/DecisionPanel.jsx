import React, { useState } from 'react'

// 决策面板：本章生成后，供用户做四个决策动作。
export default function DecisionPanel({ chapter, busy, onConfirm, onRegenerate, onRedirect, onEdit, onReset }) {
  const [instruction, setInstruction] = useState('')
  const [mode, setMode] = useState(null) // 'regenerate' | 'redirect'

  if (!chapter || !chapter.content) return null

  return (
    <div className="panel" style={{ marginTop: 0 }}>
      <h3 style={{ marginTop: 0 }}>关键节点决策</h3>
      {chapter.status === 'confirmed' ? (
        <p className="hint" style={{ color: 'var(--green)' }}>
          ✓ 本章已确认，记忆已更新。可继续下一章。
        </p>
      ) : (
        <p className="hint">本章为草稿，确认后才会写入"记忆"并影响后续生成。</p>
      )}

      <div className="row" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 8 }}>
        <button className="primary" disabled={busy || chapter.status === 'confirmed'}
          onClick={onConfirm}>
          ✅ 确认接受（更新记忆）
        </button>

        <button disabled={busy} onClick={() => setMode(mode === 'regenerate' ? null : 'regenerate')}>
          🔄 重新生成
        </button>
        {mode === 'regenerate' && (
          <div>
            <textarea rows={2} placeholder="可选：这次想怎么改进（如更紧凑、加强冲突）"
              value={instruction} onChange={(e) => setInstruction(e.target.value)} />
            <button className="primary small" style={{ marginTop: 6 }} disabled={busy}
              onClick={() => { onRegenerate(instruction); setMode(null); setInstruction('') }}>
              确定重写
            </button>
          </div>
        )}

        <button disabled={busy} onClick={() => setMode(mode === 'redirect' ? null : 'redirect')}>
          🧭 换个发展方向
        </button>
        {mode === 'redirect' && (
          <div>
            <textarea rows={3} placeholder="描述你想要的新走向（必填），如：主角不接受邀请，转而独自调查"
              value={instruction} onChange={(e) => setInstruction(e.target.value)} />
            <button className="primary small" style={{ marginTop: 6 }}
              disabled={busy || !instruction.trim()}
              onClick={() => { onRedirect(instruction); setMode(null); setInstruction('') }}>
              按新方向重写
            </button>
          </div>
        )}

        <button disabled={busy} onClick={onEdit}>✏️ 手动编辑正文</button>

        <button className="danger" disabled={busy} onClick={onReset}>
          🗑️ 重置章节（删除重新生成）
        </button>
      </div>
    </div>
  )
}
