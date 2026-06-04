import React, { useEffect, useRef, useState, useCallback } from 'react'
import { api, generateChapterStream } from '../api'
import DecisionPanel from './DecisionPanel'

export default function ChapterWorkspace({ project, notify }) {
  const [nodes, setNodes] = useState([])      // chapter 级大纲节点
  const [chapters, setChapters] = useState([]) // 已生成章节(摘要信息)
  const [activeNode, setActiveNode] = useState(null)
  const [chapter, setChapter] = useState(null) // 当前节点对应正文
  const [streaming, setStreaming] = useState(false)
  const [streamText, setStreamText] = useState('')
  const [editing, setEditing] = useState(false)
  const [editText, setEditText] = useState('')
  const [chars, setChars] = useState([])
  const [threads, setThreads] = useState([])
  const [stats, setStats] = useState(null)
  const [autoMode, setAutoMode] = useState(false) // 自动生成模式
  const autoModeRef = useRef(false) // 用于在回调中获取最新的autoMode值
  const abortRef = useRef(null)
  const proseRef = useRef(null)

  // 同步 autoMode 到 ref
  useEffect(() => {
    autoModeRef.current = autoMode
  }, [autoMode])

  const reloadSidebar = useCallback(async () => {
    try {
      const [cs, ts, st] = await Promise.all([
        api.listCharacters(project.id),
        api.listThreads(project.id),
        api.stats(project.id),
      ])
      setChars(cs); setThreads(ts); setStats(st)
    } catch (e) { notify(e.message, true) }
  }, [project.id, notify])

  const loadStructure = useCallback(async () => {
    try {
      const [outline, chs] = await Promise.all([
        api.getOutline(project.id),
        api.listChapters(project.id),
      ])
      const vols = outline.filter((n) => n.level === 'volume')
        .sort((a, b) => a.order_index - b.order_index)
      const chapterNodes = []
      for (const v of vols) {
        const kids = outline.filter((n) => n.parent_id === v.id)
          .sort((a, b) => a.order_index - b.order_index)
        chapterNodes.push({ volume: v, chapters: kids })
      }
      setNodes(chapterNodes)
      setChapters(chs)
    } catch (e) { notify(e.message, true) }
  }, [project.id, notify])

  useEffect(() => {
    loadStructure(); reloadSidebar()
    setActiveNode(null); setChapter(null); setStreamText(''); setEditing(false)
  }, [project.id, loadStructure, reloadSidebar])

  // 自动滚动到流式输出底部
  useEffect(() => {
    if (streaming && proseRef.current) proseRef.current.scrollTop = proseRef.current.scrollHeight
  }, [streamText, streaming])

  async function selectNode(node) {
    if (streaming) return
    setActiveNode(node)
    setEditing(false)
    setStreamText('')
    try {
      const ch = await api.getChapterByNode(node.id)
      setChapter(ch)
    } catch (e) { notify(e.message, true); setChapter(null) }
  }

  function runGenerate(action = 'new', instruction = '') {
    if (!activeNode) return
    setStreaming(true)
    setStreamText('')
    setEditing(false)
    abortRef.current = generateChapterStream(
      project.id,
      { outline_node_id: activeNode.id, action, instruction },
      {
        onDelta: (t) => setStreamText((s) => s + t),
        onWordCount: (data) => {
          notify(data.message || `本章字数：${data.word_count} 字`)
        },
        onDone: async ({ chapter_id, word_count }) => {
          setStreaming(false)
          try {
            const ch = await api.getChapter(chapter_id)
            setChapter(ch)
            setStreamText('')
            await loadStructure()
            notify(`生成完成！本章 ${word_count} 字`)
          } catch (e) { notify(e.message, true) }
        },
        onError: (msg) => { setStreaming(false); notify(msg, true) },
      },
    )
  }

  function stopGenerate() {
    abortRef.current?.()
    setStreaming(false)
  }

  async function confirmChapter() {
    if (!chapter) return
    try {
      const res = await api.confirmChapter(chapter.id, { update_memory: true })
      setChapter(res.chapter)
      const r = res.memory_report || {}
      const bits = []
      if (r.character_updates?.length) bits.push(`更新${r.character_updates.length}个角色状态`)
      if (r.new_threads?.length) bits.push(`新增伏笔：${r.new_threads.join('、')}`)
      if (r.resolved_threads?.length) bits.push(`回收伏笔：${r.resolved_threads.join('、')}`)
      notify('已确认。' + (bits.length ? bits.join('；') : '记忆已更新'))
      await Promise.all([loadStructure(), reloadSidebar()])
    } catch (e) { notify(e.message, true) }
  }

  function startEdit() {
    setEditText(chapter?.content || '')
    setEditing(true)
  }

  async function saveEdit() {
    try {
      const ch = await api.editChapter(chapter.id, { content: editText })
      setChapter(ch)
      setEditing(false)
      notify('已保存编辑（状态回到草稿，建议重新确认以更新记忆）')
      await loadStructure()
    } catch (e) { notify(e.message, true) }
  }

  async function resetChapter() {
    if (!chapter) return
    if (!confirm('确定要重置本章吗？章节内容将被删除，可以重新生成。')) return
    try {
      await api.deleteChapter(chapter.id)
      setChapter(null)
      notify('章节已重置，可以重新生成')
      await loadStructure()
    } catch (e) { notify(e.message, true) }
  }

  // 查找下一个未确认的章节（使用传入的数据）
  function findNextUnconfirmedNodeFromData(nodesList, chaptersList) {
    for (const group of nodesList) {
      for (const node of group.chapters) {
        const ch = chaptersList.find(c => c.outline_node_id === node.id)
        // 如果章节不存在或状态不是confirmed，就是下一个目标
        if (!ch || ch.status !== 'confirmed') {
          return node
        }
      }
    }
    return null // 所有章节都已确认
  }

  // 查找下一个未确认的章节
  function findNextUnconfirmedNode() {
    return findNextUnconfirmedNodeFromData(nodes, chapters)
  }

  // 自动生成流程
  async function startAutoGenerate() {
    console.log('[自动生成] 开始自动生成流程')
    console.log('[自动生成] 当前nodes:', nodes)
    console.log('[自动生成] 当前chapters:', chapters)
    setAutoMode(true)
    autoModeRef.current = true // 立即同步更新 ref
    await processNextChapter()
  }

  async function processNextChapter() {
    console.log('[自动生成] processNextChapter 被调用，autoModeRef.current:', autoModeRef.current)
    // 检查是否还在自动模式
    if (!autoModeRef.current) {
      console.log('[自动生成] autoMode 为 false，退出')
      return
    }

    // 重新加载最新的章节列表
    try {
      const [outline, chs] = await Promise.all([
        api.getOutline(project.id),
        api.listChapters(project.id),
      ])
      setChapters(chs)

      // 使用最新的章节列表查找下一个未确认的章节
      const nextNode = findNextUnconfirmedNodeFromData(nodes, chs)
      console.log('[自动生成] 找到下一个节点:', nextNode)

      if (!nextNode) {
        setAutoMode(false)
        console.log('[自动生成] 所有章节已完成')
        notify('🎉 所有章节已生成并确认完毕！')
        return
      }

      console.log('[自动生成] 切换到章节:', nextNode.title)
      // 切换到该章节
      setActiveNode(nextNode)
      setEditing(false)
      setStreamText('')

      try {
        console.log('[自动生成] 尝试获取章节内容, node_id:', nextNode.id)
        const ch = await api.getChapterByNode(nextNode.id)
        console.log('[自动生成] 获取到章节:', ch)
        setChapter(ch)

        // 如果已有内容且已确认，跳到下一个
        if (ch && ch.status === 'confirmed') {
          console.log('[自动生成] 章节已确认，跳到下一个')
          setTimeout(() => processNextChapter(), 500)
          return
        }

        // 如果已有内容但未确认，直接确认
        if (ch && ch.content) {
          console.log('[自动生成] 章节已生成但未确认，开始自动确认')
          notify(`正在自动确认：${nextNode.title}`)
          await confirmChapterAuto(ch.id)
          return
        }
      } catch (e) {
        console.log('[自动生成] 章节不存在，需要生成:', e.message)
        // 章节不存在，需要生成
      }

      // 生成新章节
      console.log('[自动生成] 开始生成新章节')
      notify(`正在自动生成：${nextNode.title}`)
      runGenerateAuto(nextNode)
    } catch (e) {
      console.error('[自动生成] 加载章节列表失败:', e)
      notify('加载章节列表失败：' + e.message, true)
      setAutoMode(false)
    }
  }

  function runGenerateAuto(node) {
    setStreaming(true)
    setStreamText('')
    abortRef.current = generateChapterStream(
      project.id,
      { outline_node_id: node.id, action: 'new', instruction: '' },
      {
        onDelta: (t) => setStreamText((s) => s + t),
        onWordCount: (data) => {
          notify(data.message || `本章字数：${data.word_count} 字`)
        },
        onDone: async ({ chapter_id, word_count }) => {
          setStreaming(false)
          try {
            const ch = await api.getChapter(chapter_id)
            setChapter(ch)
            setStreamText('')
            await loadStructure()
            notify(`生成完成！本章 ${word_count} 字`)

            // 自动确认
            if (autoModeRef.current) {
              setTimeout(() => confirmChapterAuto(chapter_id), 1000)
            }
          } catch (e) {
            notify(e.message, true)
            setAutoMode(false)
          }
        },
        onError: (msg) => {
          setStreaming(false)
          notify(msg, true)
          setAutoMode(false)
        },
      },
    )
  }

  async function confirmChapterAuto(chapterId) {
    if (!autoModeRef.current) {
      return
    }

    try {
      const res = await api.confirmChapter(chapterId, { update_memory: true })
      setChapter(res.chapter)
      const r = res.memory_report || {}
      const bits = []
      if (r.character_updates?.length) bits.push(`更新${r.character_updates.length}个角色状态`)
      if (r.new_threads?.length) bits.push(`新增伏笔：${r.new_threads.join('、')}`)
      if (r.resolved_threads?.length) bits.push(`回收伏笔：${r.resolved_threads.join('、')}`)
      notify('已自动确认。' + (bits.length ? bits.join('；') : '记忆已更新'))
      await Promise.all([loadStructure(), reloadSidebar()])

      // 继续下一个
      if (autoModeRef.current) {
        setTimeout(() => processNextChapter(), 1000)
      }
    } catch (e) {
      notify(e.message, true)
      setAutoMode(false)
    }
  }

  function stopAutoGenerate() {
    setAutoMode(false)
    autoModeRef.current = false // 立即同步更新 ref
    if (streaming) {
      stopGenerate()
    }
    notify('已停止自动生成')
  }

  return <Body
    project={project} nodes={nodes} chapters={chapters} activeNode={activeNode}
    chapter={chapter} streaming={streaming} streamText={streamText}
    editing={editing} editText={editText} chars={chars} threads={threads} stats={stats}
    autoMode={autoMode}
    proseRef={proseRef}
    onSelectNode={selectNode} onGenerate={runGenerate} onStop={stopGenerate}
    onConfirm={confirmChapter} onStartEdit={startEdit} onSaveEdit={saveEdit}
    onCancelEdit={() => setEditing(false)} setEditText={setEditText}
    onReset={resetChapter}
    onStartAuto={startAutoGenerate} onStopAuto={stopAutoGenerate}
  />
}

function chapterStatusFor(chapters, nodeId) {
  const c = chapters.find((x) => x.outline_node_id === nodeId)
  return c ? c.status : null
}

function Body(props) {
  const {
    project, nodes, chapters, activeNode, chapter, streaming, streamText,
    editing, editText, chars, threads, stats, autoMode, proseRef,
    onSelectNode, onGenerate, onStop, onConfirm, onStartEdit, onSaveEdit,
    onCancelEdit, setEditText, onReset, onStartAuto, onStopAuto,
  } = props

  const hasOutline = nodes.some((g) => g.chapters.length > 0)

  return (
    <div className="workspace">
      {!hasOutline ? (
        <div className="empty">还没有章级大纲。请先到「大纲」标签生成。</div>
      ) : (
        <>
          {/* 左：章节导航细长列 */}
          <div className="panel" style={{ width: 280, minWidth: 280, overflowY: 'auto', display: 'flex', flexDirection: 'column' }}>
            <div style={{ position: 'sticky', top: 0, background: 'var(--panel-bg)', paddingTop: 16, zIndex: 1, paddingBottom: 12 }}>
              <h3 style={{ marginTop: 0, marginBottom: 8 }}>章节大纲</h3>
              {autoMode ? (
                <button className="danger small" style={{ width: '100%' }} onClick={onStopAuto}>
                  ⏹ 停止自动生成
                </button>
              ) : (
                <button className="primary small" style={{ width: '100%' }} onClick={onStartAuto}>
                  🤖 自动生成全部
                </button>
              )}
            </div>
            {nodes.map((g) => (
              <div key={g.volume.id} className="outline-vol" style={{ marginBottom: 12 }}>
                <div className="vol-title" style={{ marginBottom: 6 }}>{g.volume.title}</div>
                {g.chapters.map((n) => {
                  const st = chapterStatusFor(chapters, n.id)
                  return (
                    <div key={n.id}
                      className={`chapter-node ${activeNode?.id === n.id ? 'active' : ''}`}
                      onClick={() => onSelectNode(n)}
                      style={{ marginBottom: 6, cursor: 'pointer' }}>
                      <div style={{ minWidth: 0 }}>
                        <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontWeight: 500 }}>
                          {n.title}
                        </div>
                        <div className="syn" style={{ fontSize: '0.85em', marginTop: 2 }}>{n.synopsis}</div>
                      </div>
                      {st && <span className={`tag ${st}`} style={{ marginTop: 4 }}>{st === 'confirmed' ? '✓' : '草稿'}</span>}
                    </div>
                  )
                })}
              </div>
            ))}
          </div>

          {/* 中：正文编辑区域 */}
          <div className="panel" style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0, minWidth: 0 }}>
            {!activeNode ? (
              <div className="empty">从左侧选择一章开始创作。</div>
            ) : (
              <>
                <div className="row" style={{ marginBottom: 10 }}>
                  <h2 style={{ margin: 0 }}>{activeNode.title}</h2>
                  {autoMode && <span className="tag" style={{ background: 'rgba(124,108,255,0.18)', color: 'var(--accent)' }}>自动中</span>}
                  {chapter && <span className={`tag ${chapter.status}`}>
                    {chapter.status === 'confirmed' ? '已确认' : '草稿'}</span>}
                  {chapter?.word_count > 0 && (
                    <span className="hint" style={{ margin: 0, marginLeft: 8 }}>
                      {chapter.word_count.toLocaleString()} 字
                    </span>
                  )}
                  <div className="spacer" />
                  {streaming ? (
                    <button className="danger" onClick={onStop}>⏹ 停止</button>
                  ) : !chapter || !chapter.content ? (
                    <button className="primary" onClick={() => onGenerate('new')} disabled={autoMode}>▶ 生成本章正文</button>
                  ) : null}
                </div>

                {editing ? (
                  <>
                    <textarea ref={proseRef} className="prose-editor" style={{ flex: 1 }}
                      value={editText} onChange={(e) => setEditText(e.target.value)} />
                    <div className="row end" style={{ marginTop: 10 }}>
                      <button onClick={onCancelEdit}>取消</button>
                      <button className="primary" onClick={onSaveEdit}>保存编辑</button>
                    </div>
                  </>
                ) : (
                  <div ref={proseRef} className={`prose ${streaming ? 'streaming-cursor' : ''}`}
                    style={{ flex: 1, overflowY: 'auto' }}>
                    {streaming ? streamText : (chapter?.content || '（尚未生成正文）')}
                  </div>
                )}
              </>
            )}
          </div>
        </>
      )}

      {/* 右：决策面板 + 记忆侧栏 */}
      <div style={{ display: 'flex', flexDirection: 'column', overflowY: 'auto' }}>
        {!streaming && !editing && (
          <DecisionPanel
            chapter={chapter} busy={streaming}
            onConfirm={onConfirm}
            onRegenerate={(instr) => onGenerate('regenerate', instr)}
            onRedirect={(instr) => onGenerate('redirect', instr)}
            onEdit={onStartEdit}
            onReset={onReset}
          />
        )}

        <div className="panel">
          <h3 style={{ marginTop: 0 }}>进度</h3>
          {stats ? (
            <div className="hint">
              总字数 <b style={{ color: 'var(--text)' }}>{stats.total_words.toLocaleString()}</b>
              {project.target_words > 0 && ` / ${project.target_words.toLocaleString()}`}<br />
              已确认 {stats.confirmed_chapters} 章（{stats.confirmed_words.toLocaleString()} 字）
            </div>
          ) : <div className="hint">—</div>}
          <div className="row" style={{ marginTop: 10 }}>
            <a href={api.exportUrl(project.id, 'txt')} download>
              <button className="small">导出 .txt</button>
            </a>
            <a href={api.exportUrl(project.id, 'md')} download>
              <button className="small">导出 .md</button>
            </a>
          </div>
        </div>

        <div className="panel">
          <h3 style={{ marginTop: 0 }}>角色状态</h3>
          {chars.length === 0 && <div className="hint">无</div>}
          {chars.map((c) => (
            <div key={c.id} style={{ marginBottom: 8 }}>
              <div className="char-card name">{c.name}</div>
              {c.state
                ? <div className="state">{c.state}</div>
                : <div className="hint">（暂无动态，确认章节后自动更新）</div>}
            </div>
          ))}
        </div>

        <div className="panel">
          <h3 style={{ marginTop: 0 }}>伏笔 / 线索</h3>
          {threads.length === 0 && <div className="hint">尚无</div>}
          {threads.map((t) => (
            <div key={t.id} style={{ marginBottom: 6 }}>
              <span className={`tag ${t.status === 'open' ? 'draft' : 'confirmed'}`}>
                {t.status === 'open' ? '未回收' : '已回收'}
              </span>{' '}
              <b>{t.name}</b>
              {t.detail && <div className="hint">{t.detail}</div>}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
