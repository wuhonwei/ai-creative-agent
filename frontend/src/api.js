// 后端 API 封装。所有请求走 /api（由 Vite 代理到后端）。

const JSON_HEADERS = { 'Content-Type': 'application/json' }

async function req(path, options = {}) {
  const url = `/api${path}`
  console.log(`[API] ${options.method || 'GET'} ${url}`, options.body ? JSON.parse(options.body) : '')

  const resp = await fetch(url, options)
  if (!resp.ok) {
    let msg = `请求失败 (${resp.status})`
    try {
      const data = await resp.json()
      msg = data.detail || msg
    } catch { /* ignore */ }
    console.error(`[API] ${options.method || 'GET'} ${url} 失败:`, msg)
    const err = new Error(msg)
    err.status = resp.status
    throw err
  }
  if (resp.status === 204) {
    console.log(`[API] ${options.method || 'GET'} ${url} -> 204 No Content`)
    return null
  }
  const ct = resp.headers.get('content-type') || ''
  const result = ct.includes('application/json') ? await resp.json() : await resp.text()
  console.log(`[API] ${options.method || 'GET'} ${url} ->`,
    typeof result === 'string' ? `${result.length} 字符` : result)
  return result
}

export const api = {
  health: () => req('/health'),

  // Projects
  listProjects: () => req('/projects'),
  createProject: (body) =>
    req('/projects', { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(body) }),
  getProject: (id) => req(`/projects/${id}`),
  updateProject: (id, body) =>
    req(`/projects/${id}`, { method: 'PATCH', headers: JSON_HEADERS, body: JSON.stringify(body) }),
  autofillBible: (id, body) =>
    req(`/projects/${id}/bible/autofill`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(body) }),
  deleteProject: (id) => req(`/projects/${id}`, { method: 'DELETE' }),

  // Characters
  listCharacters: (pid) => req(`/projects/${pid}/characters`),
  createCharacter: (pid, body) =>
    req(`/projects/${pid}/characters`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(body) }),
  updateCharacter: (id, body) =>
    req(`/characters/${id}`, { method: 'PATCH', headers: JSON_HEADERS, body: JSON.stringify(body) }),
  deleteCharacter: (id) => req(`/characters/${id}`, { method: 'DELETE' }),

  // Threads
  listThreads: (pid) => req(`/projects/${pid}/threads`),

  // Scenes
  listScenes: (pid) => req(`/projects/${pid}/scenes`),
  createScene: (pid, body) =>
    req(`/projects/${pid}/scenes`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(body) }),
  updateScene: (id, body) =>
    req(`/scenes/${id}`, { method: 'PATCH', headers: JSON_HEADERS, body: JSON.stringify(body) }),
  deleteScene: (id) => req(`/scenes/${id}`, { method: 'DELETE' }),
  // 按章节规划场景（变化检测）+ 场景图
  planChapterScenes: (chapterId) =>
    req(`/chapters/${chapterId}/scenes/plan`, { method: 'POST' }),
  getChapterScenes: (chapterId) => req(`/chapters/${chapterId}/scenes`),
  generateSceneImage: (sceneId) => req(`/scenes/${sceneId}/images`, { method: 'POST' }),
  listSceneImages: (sceneId) => req(`/scenes/${sceneId}/images`),
  deleteSceneImage: (imageId) => req(`/scene-images/${imageId}`, { method: 'DELETE' }),
  createSceneElement: (sceneId) => req(`/scenes/${sceneId}/element`, { method: 'POST' }),
  generateAllSceneImages: (pid) =>
    req(`/projects/${pid}/scenes/generate-images`, { method: 'POST' }),
  sceneImageUrl: (sceneId) => `/api/scenes/${sceneId}/image`,
  sceneRefImageUrl: (imageId) => `/api/scene-images/${imageId}/file`,

  // Assets (图片资产)
  listAssets: (pid, assetType = null) => {
    const params = assetType ? `?asset_type=${assetType}` : ''
    return req(`/projects/${pid}/assets${params}`)
  },
  generateAsset: (pid, body) =>
    req(`/projects/${pid}/assets/generate`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(body) }),
  deleteAsset: (id) => req(`/assets/${id}`, { method: 'DELETE' }),
  getAssetImageUrl: (id) => `/api/assets/${id}/image`,

  // Outline
  getOutline: (pid) => req(`/projects/${pid}/outline`),
  updateNode: (id, body) =>
    req(`/outline/nodes/${id}`, { method: 'PATCH', headers: JSON_HEADERS, body: JSON.stringify(body) }),
  deleteNode: (id) => req(`/outline/nodes/${id}`, { method: 'DELETE' }),

  // Chapters
  listChapters: (pid) => req(`/projects/${pid}/chapters`),
  getChapter: (id) => req(`/chapters/${id}`),
  getChapterByNode: (nodeId) => req(`/outline/nodes/${nodeId}/chapter`),
  editChapter: (id, body) =>
    req(`/chapters/${id}`, { method: 'PATCH', headers: JSON_HEADERS, body: JSON.stringify(body) }),
  confirmChapter: (id, body = { update_memory: true }) =>
    req(`/chapters/${id}/confirm`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(body) }),
  deleteChapter: (id) => req(`/chapters/${id}`, { method: 'DELETE' }),

  // Stats / export
  stats: (pid) => req(`/projects/${pid}/stats`),
  exportUrl: (pid, fmt = 'txt', onlyConfirmed = false) =>
    `/api/projects/${pid}/export?fmt=${fmt}&only_confirmed=${onlyConfirmed}`,

  // ---- 视频流水线 ----
  // 角色锁定设定
  getDesign: (pid, cid) => req(`/projects/${pid}/characters/${cid}/design`),
  saveDesign: (pid, cid, body) =>
    req(`/projects/${pid}/characters/${cid}/design`, { method: 'PUT', headers: JSON_HEADERS, body: JSON.stringify(body) }),
  createCharacterElement: (pid, cid, body) =>
    req(`/projects/${pid}/characters/${cid}/element`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(body) }),
  autofillDesigns: (pid, onlyEmpty = false) =>
    req(`/projects/${pid}/characters/designs/autofill`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ only_empty: onlyEmpty }) }),
  scanCharactersFromOutline: (pid) =>
    req(`/projects/${pid}/characters/scan-from-outline`, { method: 'POST' }),
  // 角色参考图
  listRefImages: (pid, cid) => req(`/projects/${pid}/characters/${cid}/ref-images`),
  generateRefImages: (pid, cid, views = []) =>
    req(`/projects/${pid}/characters/${cid}/ref-images/generate`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify({ views }) }),
  setPrimaryRef: (refId) =>
    req(`/character-refs/${refId}/set-primary`, { method: 'POST' }),
  deleteRefImage: (refId) => req(`/character-refs/${refId}`, { method: 'DELETE' }),
  refImageUrl: (refId) => `/api/character-refs/${refId}/image`,
  // 分镜脚本
  getStoryboard: (chapterId) => req(`/chapters/${chapterId}/storyboard`),
  generateStoryboard: (chapterId) =>
    req(`/chapters/${chapterId}/storyboard/generate`, { method: 'POST' }),
  editShot: (shotId, body) =>
    req(`/shots/${shotId}`, { method: 'PATCH', headers: JSON_HEADERS, body: JSON.stringify(body) }),
  deleteShot: (shotId) => req(`/shots/${shotId}`, { method: 'DELETE' }),
  deleteStoryboard: (chapterId) => req(`/chapters/${chapterId}/storyboard`, { method: 'DELETE' }),
  // 镜头图生视频（首尾帧从成片自动提取）
  generateShotVideo: (shotId) => req(`/shots/${shotId}/video`, { method: 'POST' }),
  getShotVideo: (shotId) => req(`/shots/${shotId}/video`),
  videoPosterUrl: (videoId) => `/api/videos/${videoId}/poster`,
  videoLastFrameUrl: (videoId) => `/api/videos/${videoId}/last-frame`,
  generateChapterShotVideos: (chapterId) =>
    req(`/chapters/${chapterId}/shots/generate-videos`, { method: 'POST' }),
  videoFileUrl: (videoId) => `/api/videos/${videoId}/file`,
  deleteVideo: (videoId) => req(`/videos/${videoId}`, { method: 'DELETE' }),
  // 章节合成
  composeChapter: (chapterId) => req(`/chapters/${chapterId}/compose`, { method: 'POST' }),
  getChapterVideo: (chapterId) => req(`/chapters/${chapterId}/compose`),

  // 定制音色
  listPresetVoices: (pageNum = 1, pageSize = 30) =>
    req(`/preset-voices?page_num=${pageNum}&page_size=${pageSize}`),
  listVoices: (pid) => req(`/projects/${pid}/voices`),
  createVoice: (pid, body) =>
    req(`/projects/${pid}/voices`, { method: 'POST', headers: JSON_HEADERS, body: JSON.stringify(body) }),
  getVoice: (id) => req(`/voices/${id}`),
  retryVoice: (id) => req(`/voices/${id}/retry`, { method: 'POST' }),
  deleteVoice: (id) => req(`/voices/${id}`, { method: 'DELETE' }),
}

// 流式生成正文。回调：onDelta(text)、onDone({chapter_id, word_count})、onError(msg)、
// onWordCount({word_count, message})、onStatus({event, ...data})、onReplace(text)。
// 返回一个 abort 函数。
export function generateChapterStream(projectId, body, { onDelta, onDone, onError, onWordCount, onStatus, onReplace }) {
  const controller = new AbortController()
  console.log(`[SSE] 开始流式生成：project_id=${projectId}, body=`, body)

  ;(async () => {
    try {
      const resp = await fetch(`/api/projects/${projectId}/generate`, {
        method: 'POST',
        headers: JSON_HEADERS,
        body: JSON.stringify(body),
        signal: controller.signal,
      })
      if (!resp.ok || !resp.body) {
        let msg = `生成请求失败 (${resp.status})`
        try { msg = (await resp.json()).detail || msg } catch { /* ignore */ }
        console.error(`[SSE] 生成请求失败:`, msg)
        onError?.(msg)
        return
      }

      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let totalChars = 0

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        // 按 SSE 事件块（\n\n 分隔）解析
        let idx
        while ((idx = buffer.indexOf('\n\n')) !== -1) {
          const rawEvent = buffer.slice(0, idx)
          buffer = buffer.slice(idx + 2)
          parseEvent(rawEvent, { onDelta, onDone, onError, onWordCount, onStatus, onReplace }, (chars) => { totalChars += chars })
        }
      }
      console.log(`[SSE] 流式生成完成，总计 ${totalChars} 字符`)
    } catch (e) {
      if (e.name !== 'AbortError') {
        console.error(`[SSE] 连接中断:`, e)
        onError?.(e.message || '连接中断')
      } else {
        console.log(`[SSE] 用户中止`)
      }
    }
  })()

  return () => controller.abort()
}

function parseEvent(raw, { onDelta, onDone, onError, onWordCount, onStatus, onReplace }, onCharCount) {
  let event = 'message'
  let dataStr = ''
  for (const line of raw.split('\n')) {
    if (line.startsWith('event:')) event = line.slice(6).trim()
    else if (line.startsWith('data:')) dataStr += line.slice(5).trim()
  }
  if (!dataStr) return
  let data
  try { data = JSON.parse(dataStr) } catch { return }

  if (event === 'delta') {
    const text = data.text || ''
    onCharCount?.(text.length)
    onDelta?.(text)
  } else if (event === 'replace') {
    onReplace?.(data.text || '')
  } else if (event === 'word_count') {
    console.log(`[SSE] word_count 事件:`, data)
    onWordCount?.(data)
  } else if (event === 'done') {
    console.log(`[SSE] done 事件:`, data)
    onDone?.(data)
  } else if (event === 'error') {
    console.error(`[SSE] error 事件:`, data.message)
    onError?.(data.message || '生成出错')
  } else if (['review', 'retry', 'humanize', 'humanize_done', 'parallel', 'candidate', 'select'].includes(event)) {
    onStatus?.({ event, ...data })
  }
}

// 流式展开卷章节。回调：onDelta(text)、onDone({outline, chapters_added})、onError(msg)。
// 返回一个 abort 函数。
export function expandVolumeStream(volumeId, body, { onDelta, onDone, onError }) {
  const controller = new AbortController()
  console.log(`[SSE] 开始流式展开卷：volume_id=${volumeId}, body=`, body)

  ;(async () => {
    try {
      const resp = await fetch(`/api/outline/nodes/${volumeId}/expand`, {
        method: 'POST',
        headers: JSON_HEADERS,
        body: JSON.stringify(body),
        signal: controller.signal,
      })
      if (!resp.ok || !resp.body) {
        let msg = `展开请求失败 (${resp.status})`
        try { msg = (await resp.json()).detail || msg } catch { /* ignore */ }
        console.error(`[SSE] 展开请求失败:`, msg)
        onError?.(msg)
        return
      }

      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let totalChars = 0

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        let idx
        while ((idx = buffer.indexOf('\n\n')) !== -1) {
          const rawEvent = buffer.slice(0, idx)
          buffer = buffer.slice(idx + 2)
          parseEvent(rawEvent, { onDelta, onDone, onError }, (chars) => { totalChars += chars })
        }
      }
      console.log(`[SSE] 流式展开完成，总计 ${totalChars} 字符`)
    } catch (e) {
      if (e.name !== 'AbortError') {
        console.error(`[SSE] 连接中断:`, e)
        onError?.(e.message || '连接中断')
      } else {
        console.log(`[SSE] 用户中止`)
      }
    }
  })()

  return () => controller.abort()
}

// 流式生成整体大纲。回调：onDelta(text)、onDone({outline, volumes, chapters})、onError(msg)。
// 返回一个 abort 函数。
export function generateOutlineStream(projectId, body, replace, force, { onDelta, onDone, onError }) {
  const controller = new AbortController()
  console.log(`[SSE] 开始流式生成大纲：project_id=${projectId}, replace=${replace}, force=${force}, body=`, body)

  ;(async () => {
    try {
      const resp = await fetch(`/api/projects/${projectId}/outline/generate?replace=${replace}&force=${force}`, {
        method: 'POST',
        headers: JSON_HEADERS,
        body: JSON.stringify(body),
        signal: controller.signal,
      })
      if (!resp.ok || !resp.body) {
        let msg = `生成大纲请求失败 (${resp.status})`
        try { msg = (await resp.json()).detail || msg } catch { /* ignore */ }
        console.error(`[SSE] 生成大纲请求失败:`, msg)
        const err = new Error(msg)
        err.status = resp.status
        onError?.(err)
        return
      }

      const reader = resp.body.getReader()
      const decoder = new TextDecoder()
      let buffer = ''
      let totalChars = 0

      while (true) {
        const { done, value } = await reader.read()
        if (done) break
        buffer += decoder.decode(value, { stream: true })

        let idx
        while ((idx = buffer.indexOf('\n\n')) !== -1) {
          const rawEvent = buffer.slice(0, idx)
          buffer = buffer.slice(idx + 2)
          parseEvent(rawEvent, { onDelta, onDone, onError }, (chars) => { totalChars += chars })
        }
      }
      console.log(`[SSE] 流式生成大纲完成，总计 ${totalChars} 字符`)
    } catch (e) {
      if (e.name !== 'AbortError') {
        console.error(`[SSE] 连接中断:`, e)
        onError?.(e.message || '连接中断')
      } else {
        console.log(`[SSE] 用户中止`)
      }
    }
  })()

  return () => controller.abort()
}
