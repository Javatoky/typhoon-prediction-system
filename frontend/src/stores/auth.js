import { defineStore } from 'pinia'
import { authApi } from '@/api'

/**
 * 会话状态。
 * 后端使用服务端会话（Cookie），前端只缓存当前用户信息与系统配置，
 * 刷新页面时通过 /auth/me 恢复，避免把权限判断放在前端。
 */
export const useAuthStore = defineStore('auth', {
  state: () => ({
    user: null,
    config: null,
    ready: false,
    loading: false,
  }),
  getters: {
    isLoggedIn: (state) => Boolean(state.user),
    isAdmin: (state) => state.user?.role === 'admin',
    displayName: (state) => state.user?.display_name || state.user?.username || '',
  },
  actions: {
    async login(username, password) {
      this.loading = true
      try {
        const { data } = await authApi.login(username, password)
        this.user = data.user
        return data
      } finally {
        this.loading = false
      }
    },
    async logout() {
      try {
        await authApi.logout()
      } finally {
        this.user = null
      }
    },
    async fetchMe() {
      try {
        const { data } = await authApi.me()
        this.user = data.user
      } catch (error) {
        this.user = null
      } finally {
        this.ready = true
      }
      return this.user
    },
    async ensureReady() {
      if (this.ready) return this.user
      return this.fetchMe()
    },
  },
})

export default useAuthStore
