<script setup>
/**
 * 账号设置：修改本人密码（FR-002）。
 */
import { reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { authApi } from '@/api'
import { useAuthStore } from '@/stores/auth'

const auth = useAuthStore()
const router = useRouter()
const form = reactive({ current_password: '', new_password: '', confirm_password: '' })
const message = ref('')
const errorMessage = ref('')
const busy = ref(false)

async function submit() {
  message.value = ''
  errorMessage.value = ''
  if (form.new_password !== form.confirm_password) {
    errorMessage.value = '两次输入的新密码不一致。'
    return
  }
  busy.value = true
  try {
    await authApi.changePassword(form)
    message.value = '密码修改成功，请重新登录。'
    form.current_password = ''
    form.new_password = ''
    form.confirm_password = ''
    setTimeout(async () => {
      await auth.logout()
      router.push({ name: 'login' })
    }, 1200)
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="grid grid--2">
    <section class="card">
      <div class="card__title"><h2>账号信息</h2></div>
      <table class="data-table">
        <tbody>
          <tr>
            <th style="width: 120px">用户名</th>
            <td class="mono">{{ auth.user?.username }}</td>
          </tr>
          <tr>
            <th>显示名称</th>
            <td>{{ auth.user?.display_name || '—' }}</td>
          </tr>
          <tr>
            <th>角色</th>
            <td>{{ auth.isAdmin ? '系统管理员' : '普通用户' }}</td>
          </tr>
          <tr>
            <th>状态</th>
            <td>{{ auth.user?.status }}</td>
          </tr>
          <tr>
            <th>最后登录</th>
            <td class="mono small">{{ auth.user?.last_login_at || '—' }}</td>
          </tr>
        </tbody>
      </table>
      <p class="small muted" style="margin-top: 8px">
        会话空闲 {{ auth.config?.security?.session_idle_minutes || 30 }} 分钟自动失效（NFR-008）。
      </p>
    </section>

    <section class="card">
      <div class="card__title"><h2>修改密码</h2></div>
      <div v-if="errorMessage" class="alert alert--error">{{ errorMessage }}</div>
      <div v-if="message" class="alert alert--success">{{ message }}</div>
      <form @submit.prevent="submit">
        <div class="field">
          <label for="current">当前密码</label>
          <input id="current" v-model="form.current_password" class="input" type="password" autocomplete="current-password" />
        </div>
        <div class="field">
          <label for="new">新密码</label>
          <input id="new" v-model="form.new_password" class="input" type="password" autocomplete="new-password" />
          <p class="field__hint">
            至少 {{ auth.config?.security?.password_min_length || 8 }} 位，且包含大写字母、小写字母、数字、符号中的两类，不能包含用户名。
          </p>
        </div>
        <div class="field">
          <label for="confirm">确认新密码</label>
          <input id="confirm" v-model="form.confirm_password" class="input" type="password" autocomplete="new-password" />
        </div>
        <button class="btn btn--primary" type="submit" :disabled="busy">
          {{ busy ? '提交中…' : '修改密码' }}
        </button>
      </form>
    </section>
  </div>
</template>
