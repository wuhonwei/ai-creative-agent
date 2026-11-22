import React, { useEffect, useState } from 'react'
import { api, expandVolumeStream, generateOutlineStream } from '../api'

// 与 backend/app/llm/prompts.py 的 DEFAULT_DIANWEN 保持一致。
// 项目尚未保存过时显示这段；保存后以项目里的文本为准。
export const DEFAULT_DIANWEN = `1.读者阅读时未曾思考过这种情况，想知道下一步如何发展；
2.人物A传达了信息X给人物B，人物B恍然大悟，明白了未明确的道理，但这个道理人物A不知道。
3.在未达到小说尾声时，读者无法猜不到结局，或者根据传统思路猜想的结局是错的。
4.爽点和反差，让读者感到舒爽且惊诧。`

function dianwenOf(project) {
  const saved = (project?.dianwen || '').trim()
  return saved || DEFAULT_DIANWEN
}

export default function OutlineView({ project, notify, sendNotification, onSaved }) {
  const [nodes, setNodes] = useState([])
  const [loading, setLoading] = useState(false)
  const [instruction, setInstruction] = useState('')
  const [numVolumes, setNumVolumes] = useState(0)
  const [chPerVol, setChPerVol] = useState(0)
  const [blocked, setBlocked] = useState(false)  // 命中 409 正文保护时显示强制重生入口
  const [expanding, setExpanding] = useState(null)  // 正在展开章节的卷 id
  const [volCount, setVolCount] = useState({})       // 每卷的目标章数输入
  const [dianwenOpen, setDianwenOpen] = useState(false)
  const [dianwen, setDianwen] = useState(() => dianwenOf(project))
  const [dianwenSaved, setDianwenSaved] = useState(() => dianwenOf(project))
  const [savingDianwen, setSavingDianwen] = useState(false)

  async function load() {
    try { setNodes(await api.getOutline(project.id)) }
    catch (e) { notify(e.message, true) }
  }
  useEffect(() => {
    load()
    setBlocked(false)
    const text = dianwenOf(project)
    setDianwen(text)
    setDianwenSaved(text)
    setDianwenOpen(false)
  }, [project.id])

  async function generate(force = false) {
    if (force && !confirm(
      '强制重新生成会永久删除本项目已写的所有正文章节及抽取的情节线，且不可恢复。\n'
      + '确定要放弃已有正文、整体重建大纲吗？'
    )) return
    setLoading(true)
    setBlocked(false)

    let totalChars = 0
    generateOutlineStream(project.id, {
      instruction,
      num_volumes: Number(numVolumes) || 0,
      chapters_per_volume: Number(chPerVol) || 0,
      dianwen,
    }, true, force, {
      onDelta: (text) => {
        totalChars += text.length
      },
      onDone: (data) => {
        setNodes(data.outline)
        setLoading(false)
        const msg = force
          ? `已放弃原有正文并重建大纲：${data.volumes} 卷 ${data.chapters} 章（流式接收共 ${totalChars} 字符）。请检查后再去工作台逐章创作。`
          : `大纲已生成：${data.volumes} 卷 ${data.chapters} 章（流式接收共 ${totalChars} 字符）。请检查后再去工作台逐章创作。`
        notify(msg)
        sendNotification?.('大纲生成完成', `${data.volumes} 卷 ${data.chapters} 章已生成完毕`)
      },
      onError: (err) => {
        setLoading(false)
        if (err.status === 409) {
          setBlocked(true)
          notify('已有正文章节，已挡下整体替换。可改为局部编辑，或强制重新生成（会删除正文）。', true)
        } else {
          notify(err.message || err, true)
        }
      },
    })
  }

  async function expandVolume(vol) {
    const hasChapters = chaptersOf(vol.id).length > 0
    if (hasChapters && !confirm(
      `「${vol.title}」已有 ${chaptersOf(vol.id).length} 章，重新展开会替换本卷这些章节节点`
      + '（已写正文的章节会拦截，不受影响）。继续？'
    )) return
    setExpanding(vol.id)

    let totalChars = 0
    expandVolumeStream(vol.id, {
      count: Number(volCount[vol.id]) || 0,
      instruction,
      replace: true,
      dianwen,
    }, {
      onDelta: (text) => {
        totalChars += text.length
        // 可以在这里显示实时进度,比如更新一个状态显示已收到多少字符
      },
      onDone: (data) => {
        setNodes(data.outline)
        setExpanding(null)
        const msg = `「${vol.title}」的章节已展开，新增 ${data.chapters_added} 章（流式接收共 ${totalChars} 字符）。`
        notify(msg)
        sendNotification?.('卷章节展开完成', `「${vol.title}」新增 ${data.chapters_added} 章`)
      },
      onError: (msg) => {
        setExpanding(null)
        notify(msg, true)
      },
    })
  }

  async function saveNode(node) {
    try {
      await api.updateNode(node.id, { title: node.title, synopsis: node.synopsis })
      notify('已保存')
    } catch (e) { notify(e.message, true) }
  }

  async function delNode(id) {
    if (!confirm('删除该节点（及其子章节）？')) return
    try { await api.deleteNode(id); await load() }
    catch (e) { notify(e.message, true) }
  }

  async function saveDianwen() {
    setSavingDianwen(true)
    try {
      await api.updateProject(project.id, { dianwen })
      setDianwenSaved(dianwen)
      notify('癫文宝经已保存')
      onSaved?.()
    } catch (e) {
      notify(e.message, true)
    } finally {
      setSavingDianwen(false)
    }
  }

  function setField(id, k, v) {
    setNodes((ns) => ns.map((n) => (n.id === id ? { ...n, [k]: v } : n)))
  }

  const volumes = nodes.filter((n) => n.level === 'volume').sort((a, b) => a.order_index - b.order_index)
  const chaptersOf = (volId) =>
    nodes.filter((n) => n.parent_id === volId).sort((a, b) => a.order_index - b.order_index)

  return (
    <>
      <div className="panel">
        <h2>生成分层大纲</h2>
        <p className="hint">
          这是第一个决策检查点：模型先产出"卷 → 章"结构，你可以直接编辑、删改，
          满意后再到工作台逐章生成正文。重新生成会清空当前大纲（仅在尚无正文时允许）。
        </p>
        <p className="hint">
          大体量提示：单次生成最多稳定产出约 100 章，超过会被截断。要写几百章时，
          建议「每卷章数」填 0 或较小值先生成各卷骨架，再到每一卷下点「展开本卷章节」
          逐卷细化——单卷单次生成不会截断，也能逐卷审阅。
        </p>
        <div className="row">
          <div style={{ flex: 1 }}>
            <label>卷数（0=自动）</label>
            <input type="number" value={numVolumes} onChange={(e) => setNumVolumes(e.target.value)} />
          </div>
          <div style={{ flex: 1 }}>
            <label>每卷章数（0=自动）</label>
            <input type="number" value={chPerVol} onChange={(e) => setChPerVol(e.target.value)} />
          </div>
        </div>
        <div className="row" style={{ marginTop: 12 }}>
          <button type="button" className={dianwenOpen ? 'primary' : ''}
            onClick={() => setDianwenOpen((v) => !v)}>
            {dianwenOpen ? '收起癫文宝经' : '癫文宝经'}
          </button>
          {dianwen !== dianwenSaved && <span className="hint" style={{ margin: 0 }}>未保存</span>}
        </div>
        {dianwenOpen && (
          <div className="dianwen-box">
            <p className="hint" style={{ marginTop: 0 }}>
              通用情节原则，由多条想法组成。生成大纲、展开本卷章节时会参考这里的文本。正文写作不使用。
            </p>
            <textarea rows={6} value={dianwen}
              onChange={(e) => setDianwen(e.target.value)} />
            <div className="row end" style={{ marginTop: 8 }}>
              <button type="button" onClick={() => setDianwen(DEFAULT_DIANWEN)}
                disabled={savingDianwen}>
                恢复默认
              </button>
              <button type="button" className="primary" onClick={saveDianwen}
                disabled={savingDianwen || dianwen === dianwenSaved}>
                {savingDianwen ? '保存中…' : '保存'}
              </button>
            </div>
          </div>
        )}
        <label>额外指引（选填）</label>
        <textarea rows={2} value={instruction}
          placeholder="如：主线偏宫廷权谋，前期慢热，第3章引入主要反派"
          onChange={(e) => setInstruction(e.target.value)} />
        <div className="row end" style={{ marginTop: 12 }}>
          {blocked && (
            <button className="danger" onClick={() => generate(true)} disabled={loading}>
              {loading ? '处理中…' : '强制重新生成（删除已有正文）'}
            </button>
          )}
          <button className="primary" onClick={() => generate(false)} disabled={loading}>
            {loading ? '生成中…（首次可能需十几秒）' : nodes.length ? '重新生成大纲' : '生成大纲'}
          </button>
        </div>
        {blocked && (
          <p className="hint" style={{ color: '#c0392b', marginTop: 8 }}>
            已存在正文章节。整体替换大纲会让已写正文与新结构脱节，因此被拦下。
            建议优先用下方各章的编辑/删除做局部调整；只有确实要推翻重来时，
            才用「强制重新生成」——它会永久删除已有正文，不可恢复。
          </p>
        )}
      </div>

      {volumes.length === 0 ? (
        <div className="empty">还没有大纲。先填好故事圣经，再点上方生成。</div>
      ) : (
        volumes.map((vol) => (
          <div className="panel outline-vol" key={vol.id}>
            <div className="row">
              <input style={{ fontWeight: 600 }} value={vol.title}
                onChange={(e) => setField(vol.id, 'title', e.target.value)} />
              <button className="small" onClick={() => saveNode(vol)}>保存</button>
              <button className="small danger" onClick={() => delNode(vol.id)}>删卷</button>
            </div>
            <textarea rows={1} className="vol-syn" value={vol.synopsis || ''}
              placeholder="本卷主题" style={{ marginTop: 8 }}
              onChange={(e) => setField(vol.id, 'synopsis', e.target.value)} />
            <div className="row" style={{ marginTop: 8, alignItems: 'center' }}>
              <span className="hint" style={{ margin: 0 }}>本卷章数（0=自动）</span>
              <input type="number" style={{ width: 90 }}
                value={volCount[vol.id] ?? ''}
                onChange={(e) => setVolCount((m) => ({ ...m, [vol.id]: e.target.value }))} />
              <button className="small primary" disabled={expanding === vol.id}
                onClick={() => expandVolume(vol)}>
                {expanding === vol.id ? '展开中…'
                  : chaptersOf(vol.id).length ? '重新展开本卷章节' : '展开本卷章节'}
              </button>
            </div>
            <h3>章节（{chaptersOf(vol.id).length}）</h3>
            {chaptersOf(vol.id).map((ch) => (
              <div className="chapter-node" key={ch.id} style={{ flexDirection: 'column', cursor: 'default' }}>
                <div className="row" style={{ width: '100%' }}>
                  <input value={ch.title}
                    onChange={(e) => setField(ch.id, 'title', e.target.value)} />
                  <button className="small" onClick={() => saveNode(ch)}>保存</button>
                  <button className="small danger" onClick={() => delNode(ch.id)}>删</button>
                </div>
                <textarea rows={2} value={ch.synopsis || ''} placeholder="本章梗概"
                  style={{ marginTop: 6 }}
                  onChange={(e) => setField(ch.id, 'synopsis', e.target.value)} />
              </div>
            ))}
          </div>
        ))
      )}
    </>
  )
}
