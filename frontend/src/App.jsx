import React, { useEffect, useState, useCallback } from 'react'
import { api } from './api'
import BibleEditor from './components/BibleEditor'
import PlotDecisions from './components/PlotDecisions'
import OutlineView from './components/OutlineView'
import ChapterWorkspace from './components/ChapterWorkspace'
import AssetsView from './components/AssetsView'
import VideoStudio from './components/VideoStudio'
import VoiceManager from './components/VoiceManager'

// 请求系统通知权限
async function requestNotificationPermission() {
  if (!('Notification' in window)) return false
  if (Notification.permission === 'granted') return true
  if (Notification.permission !== 'denied') {
    const permission = await Notification.requestPermission()
    return permission === 'granted'
  }
  return false
}

// 发送系统通知
function sendNotification(title, body) {
  if (Notification.permission === 'granted') {
    const notification = new Notification(title, {
      body,
      icon: '/favicon.ico',
      requireInteraction: true, // 需要用户手动关闭,确保看到
      tag: 'ai-creative-agent', // 同tag会替换旧通知
    })
    // 点击通知时聚焦窗口
    notification.onclick = () => {
      window.focus()
      notification.close()
    }
  }
}

export default function App() {
  const [projects, setProjects] = useState([])
  const [activeId, setActiveId] = useState(null)
  const [tab, setTab] = useState('bible')
  const [health, setHealth] = useState(null)
  const [toast, setToast] = useState(null)

  const notify = useCallback((msg, isErr = false) => {
    setToast({ msg, isErr })
    setTimeout(() => setToast(null), 3500)
  }, [])

  const loadProjects = useCallback(async () => {
    try {
      const list = await api.listProjects()
      setProjects(list)
      return list
    } catch (e) {
      notify(e.message, true)
      return []
    }
  }, [notify])

  useEffect(() => {
    api.health().then(setHealth).catch(() => setHealth({ status: 'down' }))
    loadProjects()
    requestNotificationPermission() // 启动时请求通知权限
  }, [loadProjects])

  async function newProject() {
    const title = prompt('新作品标题：', '未命名作品')
    if (!title) return
    try {
      const p = await api.createProject({ title })
      await loadProjects()
      setActiveId(p.id)
      setTab('bible')
    } catch (e) { notify(e.message, true) }
  }

  async function removeProject(id, e) {
    e.stopPropagation()
    if (!confirm('确定删除该作品及其全部内容？此操作不可恢复。')) return
    try {
      await api.deleteProject(id)
      if (activeId === id) setActiveId(null)
      await loadProjects()
    } catch (e) { notify(e.message, true) }
  }

  const active = projects.find((p) => p.id === activeId)

  return (
    <div className="app">
      <aside className="sidebar">
        <h1>✒️ AI 创作智能体</h1>
        <div style={{ padding: 12 }}>
          <button className="primary" style={{ width: '100%' }} onClick={newProject}>
            + 新建作品
          </button>
          {health && (
            <div className="hint" style={{ marginTop: 8 }}>
              模型：{health.model || '—'}
              {health.llm_configured
                ? <span style={{ color: 'var(--green)' }}>● 已配置</span>
                : <span style={{ color: 'var(--red)' }}>● 未配置 Key</span>}
            </div>
          )}
        </div>
        {projects.length === 0 && <div className="empty">还没有作品，点上方新建。</div>}
        {projects.map((p) => (
          <div
            key={p.id}
            className={`proj-item ${p.id === activeId ? 'active' : ''}`}
            onClick={() => { setActiveId(p.id); setTab('bible') }}
          >
            <div>
              <div className="title">{p.title}</div>
              <div className="meta">{p.mood || '未设氛围'}</div>
            </div>
            <button className="small danger" onClick={(e) => removeProject(p.id, e)}>
              删
            </button>
          </div>
        ))}
      </aside>

      <main className="main">
        {!active ? (
          <div className="empty" style={{ marginTop: 80 }}>
            选择左侧作品，或新建一个开始创作。
          </div>
        ) : (
          <>
            <div className="tabs">
              {[
                ['bible', '📖 故事圣经'],
                ['decisions', '🔀 剧情决策'],
                ['outline', '🗂 大纲'],
                ['workspace', '✍️ 创作工作台'],
                ['assets', '🎨 素材库'],
                ['video', '🎬 视频'],
                ['voices', '🎙 音色'],
              ].map(([k, label]) => (
                <div
                  key={k}
                  className={`tab ${tab === k ? 'active' : ''}`}
                  onClick={() => setTab(k)}
                >
                  {label}
                </div>
              ))}
            </div>
            <div className={`content ${tab === 'workspace' ? 'workspace-container' : ''}`}>
              {tab === 'bible' && (
                <BibleEditor
                  project={active}
                  onSaved={loadProjects}
                  notify={notify}
                />
              )}
              {tab === 'decisions' && (
                <PlotDecisions project={active} onSaved={loadProjects} notify={notify} />
              )}
              {tab === 'outline' && (
                <OutlineView
                  project={active}
                  notify={notify}
                  sendNotification={sendNotification}
                  onSaved={loadProjects}
                />
              )}
              {tab === 'workspace' && (
                <ChapterWorkspace project={active} notify={notify} sendNotification={sendNotification} />
              )}
              {tab === 'assets' && (
                <AssetsView project={active} notify={notify} />
              )}
              {tab === 'video' && (
                <VideoStudio project={active} notify={notify} />
              )}
              {tab === 'voices' && (
                <VoiceManager project={active} notify={notify} />
              )}
            </div>
          </>
        )}
      </main>

      {toast && (
        <div className={`toast ${toast.isErr ? 'err' : ''}`}>{toast.msg}</div>
      )}
    </div>
  )
}
