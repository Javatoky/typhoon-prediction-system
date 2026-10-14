import api from './client'

export const authApi = {
  login: (username, password) => api.post('/auth/login', { username, password }),
  logout: () => api.post('/auth/logout', {}),
  me: () => api.get('/auth/me'),
  changePassword: (payload) => api.post('/auth/change-password', payload),
}

export const systemApi = {
  health: () => api.get('/system/health'),
  config: () => api.get('/system/config'),
  modelStatus: () => api.get('/system/model-status'),
}

export const typhoonApi = {
  list: (params) => api.get('/typhoons', params),
  seasons: () => api.get('/typhoons/seasons'),
  detail: (id) => api.get(`/typhoons/${id}`),
  track: (id) => api.get(`/typhoons/${id}/track`),
}

export const uploadApi = {
  list: (params) => api.get('/uploads', params),
  detail: (id) => api.get(`/uploads/${id}`),
  validate: (file) => {
    const form = new FormData()
    form.append('file', file)
    return api.upload('/uploads/track/validate', form)
  },
  upload: (file) => {
    const form = new FormData()
    form.append('file', file)
    return api.upload('/uploads/track', form)
  },
}

export const predictionApi = {
  list: (params) => api.get('/predictions', params),
  detail: (id) => api.get(`/predictions/${id}`),
  create: (payload) => api.post('/predictions', payload),
  statistics: () => api.get('/predictions/statistics'),
  statusChoices: () => api.get('/predictions/status-choices'),
  export: (id) => api.download(`/predictions/${id}/export`),
}

export const adminApi = {
  overview: () => api.get('/admin/overview'),
  users: (params) => api.get('/admin/users', params),
  createUser: (payload) => api.post('/admin/users', payload),
  updateUser: (id, payload) => api.put(`/admin/users/${id}`, payload),
  resetPassword: (id, payload) => api.post(`/admin/users/${id}/reset-password`, payload || {}),
  datasets: (params) => api.get('/admin/datasets', params),
  importDataset: (formData) => api.upload('/admin/datasets/import', formData),
  models: (params) => api.get('/admin/models', params),
  uploadModel: (formData) => api.upload('/admin/models', formData),
  activateModel: (id) => api.put(`/admin/models/${id}/activate`, {}),
  ensureBaseline: () => api.post('/admin/models/baseline', {}),
  runtimeStatus: () => api.get('/admin/models/runtime-status'),
  logs: (params) => api.get('/admin/logs', params),
  logActions: () => api.get('/admin/logs/actions'),
}

export default { authApi, systemApi, typhoonApi, uploadApi, predictionApi, adminApi }
