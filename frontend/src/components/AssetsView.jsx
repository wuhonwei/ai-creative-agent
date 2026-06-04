import React, { useEffect, useState } from 'react'
import { api } from '../api'

export default function AssetsView({ project, notify }) {
  const [tab, setTab] = useState('character') // 'character' | 'scene'
  const [characters, setCharacters] = useState([])
  const [scenes, setScenes] = useState([])
  const [assets, setAssets] = useState([])
  const [generating, setGenerating] = useState(null) // {type, ref_id}
  const [showSceneForm, setShowSceneForm] = useState(false)
  const [sceneName, setSceneName] = useState('')
  const [sceneDesc, setSceneDesc] = useState('')
  const [previewImage, setPreviewImage] = useState(null) // 预览大图

  useEffect(() => {
    loadData()
  }, [project.id, tab])

  async function loadData() {
    try {
      // 加载角色
      const chars = await api.listCharacters(project.id)
      setCharacters(chars)

      // 加载场景
      const scns = await api.listScenes(project.id)
      setScenes(scns)

      // 加载对应类型的图片
      const ast = await api.listAssets(project.id, tab)
      setAssets(ast)
    } catch (e) {
      notify(e.message, true)
    }
  }

  async function generateImage(assetType, refId) {
    setGenerating({ type: assetType, ref_id: refId })
    try {
      await api.generateAsset(project.id, {
        asset_type: assetType,
        ref_id: refId,
        custom_prompt: '', // 可扩展：让用户输入自定义提示词
      })
      notify(`图片生成成功`)
      await loadData()
    } catch (e) {
      notify(e.message, true)
    } finally {
      setGenerating(null)
    }
  }

  async function deleteImage(assetId) {
    if (!confirm('确定删除这张图片？')) return
    try {
      await api.deleteAsset(assetId)
      notify('图片已删除')
      await loadData()
    } catch (e) {
      notify(e.message, true)
    }
  }

  async function createScene() {
    if (!sceneName.trim()) {
      notify('请输入场景名称', true)
      return
    }
    try {
      await api.createScene(project.id, {
        name: sceneName,
        description: sceneDesc,
      })
      notify('场景已创建')
      setShowSceneForm(false)
      setSceneName('')
      setSceneDesc('')
      await loadData()
    } catch (e) {
      notify(e.message, true)
    }
  }

  async function deleteScene(sceneId) {
    if (!confirm('确定删除这个场景？关联的图片也会无法生成。')) return
    try {
      await api.deleteScene(sceneId)
      notify('场景已删除')
      await loadData()
    } catch (e) {
      notify(e.message, true)
    }
  }

  function openInExplorer() {
    // 在浏览器中打开后端assets目录的说明
    notify('图片存储在后端 backend/assets/ 目录中', false)
    // 由于浏览器安全限制，无法直接打开文件资源管理器
    // 用户需要手动导航到项目目录
  }

  function viewImage(imageUrl, assetName) {
    setPreviewImage({ url: imageUrl, name: assetName })
  }

  // 按 ref_id 分组图片
  const groupedAssets = {}
  assets.forEach((asset) => {
    if (!groupedAssets[asset.ref_id]) {
      groupedAssets[asset.ref_id] = []
    }
    groupedAssets[asset.ref_id].push(asset)
  })

  const currentList = tab === 'character' ? characters : scenes

  return (
    <div style={{ padding: 20 }}>
      <h2>🎨 素材库</h2>
      <p className="hint">根据角色或场景描述生成动漫风格图片（通义万相）</p>

      {/* 顶部操作栏 */}
      <div style={{ marginBottom: 20, display: 'flex', gap: 10, alignItems: 'center' }}>
        <button onClick={openInExplorer} style={{ fontSize: 14 }}>
          📁 查看文件位置
        </button>
        <span className="hint" style={{ fontSize: 12 }}>
          图片保存在：backend/assets/ 目录
        </span>
      </div>

      {/* 标签页切换 */}
      <div style={{ display: 'flex', gap: 10, marginBottom: 20, borderBottom: '1px solid #ddd' }}>
        <button
          className={tab === 'character' ? 'primary' : ''}
          onClick={() => setTab('character')}
          style={{ borderRadius: '4px 4px 0 0' }}
        >
          人物图片
        </button>
        <button
          className={tab === 'scene' ? 'primary' : ''}
          onClick={() => setTab('scene')}
          style={{ borderRadius: '4px 4px 0 0' }}
        >
          场景图片
        </button>
      </div>

      {/* 场景标签页：新建场景按钮 */}
      {tab === 'scene' && (
        <div style={{ marginBottom: 20 }}>
          {!showSceneForm ? (
            <button className="primary" onClick={() => setShowSceneForm(true)}>
              + 新建场景
            </button>
          ) : (
            <div className="card" style={{ padding: 15 }}>
              <h3>新建场景</h3>
              <label>
                场景名称
                <input
                  value={sceneName}
                  onChange={(e) => setSceneName(e.target.value)}
                  placeholder="如：雨夜街道、皇宫大殿"
                />
              </label>
              <label>
                场景描述
                <textarea
                  value={sceneDesc}
                  onChange={(e) => setSceneDesc(e.target.value)}
                  rows={4}
                  placeholder="详细描述场景的环境、氛围、光线等..."
                />
              </label>
              <div style={{ display: 'flex', gap: 10 }}>
                <button className="primary" onClick={createScene}>
                  创建
                </button>
                <button onClick={() => setShowSceneForm(false)}>取消</button>
              </div>
            </div>
          )}
        </div>
      )}

      {/* 列表 */}
      {currentList.length === 0 && (
        <div className="empty">
          {tab === 'character'
            ? '还没有角色，请先在「故事圣经」中创建角色。'
            : '还没有场景，点击上方「新建场景」创建。'}
        </div>
      )}

      {currentList.map((item) => {
        const itemAssets = groupedAssets[item.id] || []
        const isGenerating = generating?.type === tab && generating?.ref_id === item.id

        return (
          <div key={item.id} className="card" style={{ marginBottom: 20, padding: 15 }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
              <div>
                <h3>{item.name}</h3>
                <p className="hint">{item.description || item.role}</p>
              </div>
              <div style={{ display: 'flex', gap: 10 }}>
                <button
                  className="primary"
                  onClick={() => generateImage(tab, item.id)}
                  disabled={isGenerating}
                >
                  {isGenerating ? '生成中...' : '生成图片'}
                </button>
                {tab === 'scene' && (
                  <button className="danger small" onClick={() => deleteScene(item.id)}>
                    删除场景
                  </button>
                )}
              </div>
            </div>

            {/* 图片网格 */}
            {itemAssets.length > 0 && (
              <div
                style={{
                  display: 'grid',
                  gridTemplateColumns: 'repeat(auto-fill, minmax(200px, 1fr))',
                  gap: 15,
                  marginTop: 15,
                }}
              >
                {itemAssets.map((asset) => (
                  <div
                    key={asset.id}
                    style={{
                      border: '1px solid #ddd',
                      borderRadius: 8,
                      overflow: 'hidden',
                      position: 'relative',
                    }}
                  >
                    <img
                      src={api.getAssetImageUrl(asset.id)}
                      alt={asset.ref_name}
                      style={{
                        width: '100%',
                        height: 200,
                        objectFit: 'cover',
                        cursor: 'pointer'
                      }}
                      onClick={() => viewImage(api.getAssetImageUrl(asset.id), asset.ref_name)}
                      title="点击查看大图"
                    />
                    <div style={{ padding: 8, fontSize: 12, color: '#666' }}>
                      <div style={{ marginBottom: 4 }}>
                        {asset.width} × {asset.height}
                      </div>
                      <div style={{ display: 'flex', gap: 4 }}>
                        <button
                          className="small"
                          onClick={() => window.open(api.getAssetImageUrl(asset.id), '_blank')}
                          style={{ flex: 1, fontSize: 11 }}
                          title="在新标签页打开"
                        >
                          打开
                        </button>
                        <button
                          className="danger small"
                          onClick={() => deleteImage(asset.id)}
                          style={{ flex: 1, fontSize: 11 }}
                        >
                          删除
                        </button>
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        )
      })}

      {/* 图片预览弹窗 */}
      {previewImage && (
        <div
          style={{
            position: 'fixed',
            top: 0,
            left: 0,
            right: 0,
            bottom: 0,
            backgroundColor: 'rgba(0, 0, 0, 0.85)',
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            zIndex: 1000,
            cursor: 'pointer',
          }}
          onClick={() => setPreviewImage(null)}
        >
          <div style={{ maxWidth: '90%', maxHeight: '90%', position: 'relative' }}>
            <img
              src={previewImage.url}
              alt={previewImage.name}
              style={{
                maxWidth: '100%',
                maxHeight: '90vh',
                objectFit: 'contain',
                borderRadius: 8,
              }}
              onClick={(e) => e.stopPropagation()}
            />
            <div
              style={{
                position: 'absolute',
                top: -40,
                right: 0,
                color: 'white',
                fontSize: 14,
                display: 'flex',
                gap: 15,
                alignItems: 'center',
              }}
            >
              <span>{previewImage.name}</span>
              <button
                onClick={() => window.open(previewImage.url, '_blank')}
                style={{
                  background: 'rgba(255,255,255,0.2)',
                  color: 'white',
                  border: 'none',
                  padding: '5px 10px',
                  borderRadius: 4,
                  cursor: 'pointer',
                }}
              >
                新窗口打开
              </button>
              <button
                onClick={() => setPreviewImage(null)}
                style={{
                  background: 'rgba(255,255,255,0.2)',
                  color: 'white',
                  border: 'none',
                  padding: '5px 10px',
                  borderRadius: 4,
                  cursor: 'pointer',
                  fontSize: 18,
                }}
              >
                ✕
              </button>
            </div>
          </div>
          <p style={{ color: 'white', marginTop: 20, fontSize: 14 }}>
            点击背景关闭 | 点击图片可拖动查看
          </p>
        </div>
      )}
    </div>
  )
}
