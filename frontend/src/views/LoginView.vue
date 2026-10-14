<script setup>
import { onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { systemApi } from '@/api'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()

const username = ref('')
const password = ref('')
const errorMessage = ref('')
const submitting = ref(false)
const config = ref(null)

onMounted(async () => {
  try {
    const { data } = await systemApi.config().catch(() => ({ data: null }))
    if (data) config.value = data
  } catch (error) {
    // 未登录时无权限访问配置接口，忽略
  }
})

async function submit() {
  errorMessage.value = ''
  if (!username.value.trim() || !password.value) {
    errorMessage.value = '请输入用户名和密码。'
    return
  }
  submitting.value = true
  try {
    await auth.login(username.value.trim(), password.value)
    const redirect = route.query.redirect
    router.push(typeof redirect === 'string' && redirect ? redirect : { name: 'workspace' })
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    submitting.value = false
  }
}
</script>

<template>
  <div class="login">
    <form class="login__card" @submit.prevent="submit">
      <h1>台风路径智能预报系统</h1>
      <p class="muted small">
        基于 Transformer 的西北太平洋台风路径与强度多步预测（课程教学原型）
      </p>

      <div v-if="errorMessage" class="alert alert--error">{{ errorMessage }}</div>

      <div class="field">
        <label for="username">用户名</label>
        <input
          id="username"
          v-model="username"
          class="input"
          type="text"
          autocomplete="username"
          placeholder="请输入用户名"
        />
      </div>
      <div class="field">
        <label for="password">密码</label>
        <input
          id="password"
          v-model="password"
          class="input"
          type="password"
          autocomplete="current-password"
          placeholder="请输入密码"
        />
      </div>

      <button class="btn btn--primary login__submit" type="submit" :disabled="submitting">
        {{ submitting ? '登录中…' : '登录' }}
      </button>

      <p class="small muted login__note">
        账号由管理员创建，本系统不提供开放注册入口。
        <template v-if="config?.security?.login_max_failures">
          连续 {{ config.security.login_max_failures }} 次失败将锁定账号
          {{ config.security.login_lock_minutes || 15 }} 分钟。
        </template>
      </p>
      <p class="small muted">{{ config?.disclaimer }}</p>
    </form>
  </div>
</template>

<style scoped>
.login {
  min-height: 100vh;
  display: flex;
  align-items: center;
  justify-content: center;
  background: linear-gradient(160deg, #eaf2fb, #f6f8fa);
  padding: 20px;
}
.login__card {
  width: 100%;
  max-width: 380px;
  background: #fff;
  border: 1px solid var(--color-border);
  border-radius: 12px;
  box-shadow: 0 6px 24px rgba(27, 31, 36, 0.1);
  padding: 26px;
}
.login__card h1 {
  font-size: 19px;
  margin-bottom: 4px;
}
.login__submit {
  width: 100%;
  justify-content: center;
  margin-top: 4px;
}
.login__note {
  margin-top: 14px;
  line-height: 1.6;
}
</style>
