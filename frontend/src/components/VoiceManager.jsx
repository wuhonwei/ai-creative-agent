import React, { useEffect, useState, useRef } from 'react'
import { api } from '../api'

const EMPTY_FORM = { voice_name: '', voice_url: '' }

const STATUS_LABEL = {
  pending: '等待中',
  processing: '生成中…',
  done: '已完成',
  failed: '失败',
}

const PRESET_PAGE_SIZE = 30

function PresetVoicesPanel({ notify }) {
  const [voices, setVoices] = useState([])
  const [pageNum, setPageNum] = useState(1)
  const [hasMore, setHasMore] = useState(true)
  const [total, setTotal] = useState(null)
  const [loading, setLoading] = useState(false)
  const [playingId, setPlayingId] = useState(null)
  const audioRef = useRef(null)

  useEffect(() => {
    loadPresets(1)
    return () => {
      if (audioRef.current) {
        audioRef.current.pause()
      }
    }
  }, [])

  async function loadPresets(page) {
    setLoading(true)
    if (audioRef.current) {
      audioRef.current.pause()
      setPlayingId(null)
    }
    try {
      const result = await api.listPresetVoices(page, PRESET_PAGE_SIZE)
      if (result.voices.length === 0 && page > 1) {
        notify('已是最后一页', false)
        setHasMore(false)
        return
      }
      setVoices(result.voices)
      setPageNum(result.page_num)
      setTotal(result.total ?? null)
      // 后端未返回 has_more 时，有数据则允许尝试翻页，由下一页空结果兜底
      setHasMore(
        result.voices.length > 0
          && (result.has_more !== undefined ? result.has_more : true)
      )
    } catch (e) {
      notify(e.message, true)
    } finally {
      setLoading(false)
    }
  }

  function handlePlay(voice) {
    if (!voice.trial_url) {
      notify('该音色暂无试听音频', true)
      return
    }
    if (playingId === voice.voice_id && audioRef.current && !audioRef.current.paused) {
      audioRef.current.pause()
      setPlayingId(null)
      return
    }
    if (audioRef.current) {
      audioRef.current.pause()
      audioRef.current.src = voice.trial_url
      audioRef.current.play().catch(() => notify('播放失败，请检查网络', true))
      setPlayingId(voice.voice_id)
    }
  }

  function handleAudioEnded() {
    setPlayingId(null)
  }

  return (
    <div>
      <p className="hint" style={{ marginBottom: 16 }}>
        可灵官方预置音色库，点击播放按钮可试听。
      </p>
      {loading ? (
        <div className="empty">加载中…</div>
      ) : voices.length === 0 ? (
        <div className="empty">暂无官方音色数据</div>
      ) : (
        <div style={{
          background: 'var(--panel)',
          border: '1px solid var(--border)',
          borderRadius: 8,
          overflow: 'hidden',
        }}>
          <table style={{ width: '100%', borderCollapse: 'collapse' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid var(--border)', textAlign: 'left' }}>
                <th style={{ padding: '10px 16px', color: 'var(--muted)', fontWeight: 500 }}>名称</th>
                <th style={{ padding: '10px 16px', color: 'var(--muted)', fontWeight: 500 }}>音色 ID</th>
                <th style={{ padding: '10px 16px', color: 'var(--muted)', fontWeight: 500 }}>来源</th>
                <th style={{ padding: '10px 16px', color: 'var(--muted)', fontWeight: 500 }}>试听 URL</th>
                <th style={{ padding: '10px 16px', color: 'var(--muted)', fontWeight: 500, width: 80 }}>试听</th>
              </tr>
            </thead>
            <tbody>
              {voices.map((v) => (
                <tr key={v.voice_id} style={{ borderBottom: '1px solid var(--border)' }}>
                  <td style={{ padding: '10px 16px' }}>{v.voice_name}</td>
                  <td style={{ padding: '10px 16px', color: 'var(--muted)', fontSize: 12, wordBreak: 'break-all' }}>
                    {v.voice_id}
                  </td>
                  <td style={{ padding: '10px 16px', color: 'var(--muted)', fontSize: 12 }}>
                    {v.owned_by === 'kling' ? '官方' : v.owned_by}
                  </td>
                  <td style={{ padding: '10px 16px', fontSize: 12, wordBreak: 'break-all' }}>
                    {v.trial_url ? (
                      <a
                        href={v.trial_url}
                        target="_blank"
                        rel="noreferrer"
                        style={{ color: 'var(--accent)' }}
                        title={v.trial_url}
                      >
                        {v.trial_url}
                      </a>
                    ) : (
                      <span className="hint">—</span>
                    )}
                  </td>
                  <td style={{ padding: '10px 16px' }}>
                    {v.trial_url ? (
                      <button
                        className="small"
                        onClick={() => handlePlay(v)}
                        title={playingId === v.voice_id ? '暂停' : '播放'}
                      >
                        {playingId === v.voice_id ? '⏸' : '▶'}
                      </button>
                    ) : (
                      <span className="hint">—</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div style={{ marginTop: 16, display: 'flex', gap: 8, alignItems: 'center' }}>
        <button
          className="small"
          disabled={pageNum <= 1 || loading}
          onClick={() => loadPresets(pageNum - 1)}
        >
          上一页
        </button>
        <span className="hint">
          第 {pageNum} 页 · 本页 {voices.length} 条
          {total != null ? ` · 共约 ${total} 条` : ''}
        </span>
        <button
          className="small"
          disabled={loading || !hasMore}
          onClick={() => loadPresets(pageNum + 1)}
        >
          下一页
        </button>
      </div>
      <audio ref={audioRef} onEnded={handleAudioEnded} style={{ display: 'none' }} />
    </div>
  )
}

function CustomVoicesPanel({ project, notify }) {
  const [voices, setVoices] = useState([])
  const [selectedId, setSelectedId] = useState(null)
  const [form, setForm] = useState(EMPTY_FORM)
  const [creating, setCreating] = useState(false)
  const pollRef = useRef(null)
  const previewAudioRef = useRef(null)

  useEffect(() => {
    loadVoices()
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [project.id])

  async function loadVoices() {
    try {
      const list = await api.listVoices(project.id)
      setVoices(list)
      if (list.length > 0 && !selectedId) {
        setSelectedId(list[0].id)
      }
      const processing = list.find((v) => v.status === 'processing' || v.status === 'pending')
      if (processing) startPolling(processing.id)
    } catch (e) {
      notify(e.message, true)
    }
  }

  function startPolling(voiceId) {
    if (pollRef.current) clearInterval(pollRef.current)
    pollRef.current = setInterval(async () => {
      try {
        const v = await api.getVoice(voiceId)
        setVoices((prev) => prev.map((item) => (item.id === v.id ? v : item)))
        if (v.status === 'done') {
          clearInterval(pollRef.current)
          pollRef.current = null
          notify(`音色「${v.voice_name}」生成成功，ID：${v.voice_id}`)
        } else if (v.status === 'failed') {
          clearInterval(pollRef.current)
          pollRef.current = null
          notify(v.error || '音色生成失败', true)
        }
      } catch (e) {
        clearInterval(pollRef.current)
        pollRef.current = null
        notify(e.message, true)
      }
    }, 3000)
  }

  async function handleCreate() {
    if (!form.voice_name.trim()) {
      notify('请填写声音名称', true)
      return
    }
    if (!form.voice_url.trim()) {
      notify('请填写声音 URL', true)
      return
    }
    setCreating(true)
    try {
      const v = await api.createVoice(project.id, {
        voice_name: form.voice_name.trim(),
        voice_url: form.voice_url.trim(),
      })
      setVoices((prev) => [v, ...prev])
      setSelectedId(v.id)
      setForm(EMPTY_FORM)
      notify('已提交音色生成任务，请稍候…')
      startPolling(v.id)
    } catch (e) {
      notify(e.message, true)
    } finally {
      setCreating(false)
    }
  }

  async function handleRetry(voiceId) {
    try {
      const v = await api.retryVoice(voiceId)
      setVoices((prev) => prev.map((item) => (item.id === v.id ? v : item)))
      notify('已重新提交音色生成任务')
      startPolling(v.id)
    } catch (e) {
      notify(e.message, true)
    }
  }

  async function handleDelete(voiceId) {
    if (!confirm('确定删除这条音色记录？')) return
    try {
      await api.deleteVoice(voiceId)
      setVoices((prev) => prev.filter((v) => v.id !== voiceId))
      if (selectedId === voiceId) setSelectedId(null)
      notify('音色记录已删除')
    } catch (e) {
      notify(e.message, true)
    }
  }

  const selected = voices.find((v) => v.id === selectedId)

  return (
    <div style={{ display: 'flex', height: 'calc(100vh - 120px)', overflow: 'hidden' }}>
      <div style={{ width: 260, flexShrink: 0, borderRight: '1px solid var(--border)', overflowY: 'auto' }}>
        <div style={{ padding: '12px 16px', borderBottom: '1px solid var(--border)', fontWeight: 600 }}>
          定制音色列表
        </div>
        {voices.length === 0 && (
          <div className="empty" style={{ padding: 20 }}>还没有音色，右侧创建。</div>
        )}
        {voices.map((v) => (
          <div
            key={v.id}
            className={`proj-item ${v.id === selectedId ? 'active' : ''}`}
            onClick={() => setSelectedId(v.id)}
            style={{ flexDirection: 'column', alignItems: 'flex-start', gap: 4 }}
          >
            <div className="title">{v.voice_name}</div>
            <div className="meta">
              <span style={{
                color: v.status === 'done' ? 'var(--green)'
                  : v.status === 'failed' ? 'var(--red)'
                  : 'var(--amber)',
              }}>
                {STATUS_LABEL[v.status] || v.status}
              </span>
              {v.voice_id && <span> · {v.voice_id}</span>}
            </div>
          </div>
        ))}
      </div>

      <div style={{ flex: 1, overflowY: 'auto', padding: 24 }}>
        <div style={{
          background: 'var(--panel)',
          border: '1px solid var(--border)',
          borderRadius: 8,
          padding: 20,
          marginBottom: 24,
        }}>
          <h3 style={{ margin: '0 0 12px', fontSize: 15 }}>新建定制音色</h3>
          <label>声音名称</label>
          <input
            value={form.voice_name}
            onChange={(e) => setForm({ ...form, voice_name: e.target.value })}
            placeholder="例如：定制人声"
          />
          <label>声音 URL</label>
          <input
            value={form.voice_url}
            onChange={(e) => setForm({ ...form, voice_url: e.target.value })}
            placeholder="https://example.com/voice.mp3"
          />
          <p className="hint" style={{ marginTop: 6 }}>
            音频需清晰无噪、仅含单人声，时长 5~30 秒。
          </p>
          <button
            className="primary"
            style={{ marginTop: 12 }}
            onClick={handleCreate}
            disabled={creating}
          >
            {creating ? '提交中…' : '生成音色 ID'}
          </button>
        </div>

        {selected && (
          <div style={{
            background: 'var(--panel)',
            border: '1px solid var(--border)',
            borderRadius: 8,
            padding: 20,
          }}>
            <h3 style={{ margin: '0 0 12px', fontSize: 15 }}>音色详情</h3>
            <table style={{ width: '100%', borderCollapse: 'collapse' }}>
              <tbody>
                {[
                  ['声音名称', selected.voice_name],
                  ['声音 URL', selected.voice_url],
                  ['状态', STATUS_LABEL[selected.status] || selected.status],
                  ['任务 ID', selected.task_id || '—'],
                  ['音色 ID', selected.voice_id || '—'],
                  ['错误信息', selected.error || '—'],
                ].map(([label, value]) => (
                  <tr key={label} style={{ borderBottom: '1px solid var(--border)' }}>
                    <td style={{ padding: '8px 0', color: 'var(--muted)', width: 100 }}>{label}</td>
                    <td style={{ padding: '8px 0', wordBreak: 'break-all' }}>{value}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {selected.trial_url && (
              <div style={{ marginTop: 16 }}>
                <label>试听</label>
                <audio
                  ref={previewAudioRef}
                  controls
                  src={selected.trial_url}
                  style={{ width: '100%', marginTop: 4 }}
                />
              </div>
            )}
            <div style={{ marginTop: 16, display: 'flex', gap: 8 }}>
              {selected.status === 'failed' && (
                <button className="primary" onClick={() => handleRetry(selected.id)}>
                  重新生成
                </button>
              )}
              <button className="danger" onClick={() => handleDelete(selected.id)}>
                删除
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

export default function VoiceManager({ project, notify }) {
  const [tab, setTab] = useState('preset')

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <div style={{ padding: '16px 24px 0', borderBottom: '1px solid var(--border)' }}>
        <h2 style={{ margin: '0 0 8px', fontSize: 18 }}>音色管理</h2>
        <div style={{ display: 'flex', gap: 4, marginBottom: -1 }}>
          {[
            ['preset', '官方音色'],
            ['custom', '定制音色'],
          ].map(([k, label]) => (
            <button
              key={k}
              className={tab === k ? 'primary' : ''}
              style={{
                borderRadius: '6px 6px 0 0',
                borderBottom: tab === k ? '2px solid var(--accent)' : '2px solid transparent',
              }}
              onClick={() => setTab(k)}
            >
              {label}
            </button>
          ))}
        </div>
      </div>
      <div style={{ flex: 1, overflow: 'auto', padding: tab === 'preset' ? 24 : 0 }}>
        {tab === 'preset' ? (
          <PresetVoicesPanel notify={notify} />
        ) : (
          <CustomVoicesPanel project={project} notify={notify} />
        )}
      </div>
    </div>
  )
}
