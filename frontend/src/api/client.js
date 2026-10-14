/**
 * 统一 API 客户端。
 *
 * 约定（与后端 app/core/responses.py 一致）：
 *   成功 {"ok": true,  "data": ..., "meta": ...}
 *   失败 {"ok": false, "error": {"code", "message", "details"}}
 *
 * 所有请求都带 credentials: 'include'，因为会话基于 Cookie（FR-001）。
 */
const BASE = import.meta.env.VITE_API_BASE || '/api'

export class ApiError extends Error {
  constructor(message, { code = 'error', status = 0, details = null } = {}) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
    this.details = details
  }
}

function buildQuery(params = {}) {
  const search = new URLSearchParams()
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null || value === '') return
    search.append(key, value)
  })
  const query = search.toString()
  return query ? `?${query}` : ''
}

async function parse(response) {
  const contentType = response.headers.get('Content-Type') || ''
  if (!contentType.includes('application/json')) {
    if (!response.ok) {
      throw new ApiError(`请求失败（HTTP ${response.status}）`, { status: response.status })
    }
    return response
  }
  const payload = await response.json()
  if (!response.ok || payload.ok === false) {
    const error = payload.error || {}
    throw new ApiError(error.message || `请求失败（HTTP ${response.status}）`, {
      code: error.code || 'error',
      status: response.status,
      details: error.details || null,
    })
  }
  return payload
}

async function request(method, path, { body, params, formData } = {}) {
  const options = {
    method,
    credentials: 'include',
    headers: {},
  }
  if (formData) {
    options.body = formData
  } else if (body !== undefined) {
    options.headers['Content-Type'] = 'application/json'
    options.body = JSON.stringify(body)
  }
  const response = await fetch(`${BASE}${path}${buildQuery(params)}`, options)
  return parse(response)
}

export const api = {
  get: (path, params) => request('GET', path, { params }),
  post: (path, body) => request('POST', path, { body }),
  put: (path, body) => request('PUT', path, { body }),
  upload: (path, formData) => request('POST', path, { formData }),
  /** 下载 CSV：返回 Blob 与文件名（FR-011） */
  async download(path, params) {
    const response = await fetch(`${BASE}${path}${buildQuery(params)}`, {
      credentials: 'include',
    })
    if (!response.ok) {
      await parse(response)
      throw new ApiError('导出失败', { status: response.status })
    }
    const disposition = response.headers.get('Content-Disposition') || ''
    const match = /filename="?([^";]+)"?/.exec(disposition)
    return {
      blob: await response.blob(),
      filename: match ? match[1] : 'export.csv',
    }
  },
}

export default api
