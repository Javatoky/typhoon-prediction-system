<script setup>
/**
 * 用户管理（FR-012、UC-06）。
 */
import { onMounted, reactive, ref } from 'vue'
import { adminApi } from '@/api'

const users = ref([])
const meta = reactive({ page: 1, total: 0, pages: 0, page_size: 20 })
const keyword = ref('')
const errorMessage = ref('')
const successMessage = ref('')
const busy = ref(false)

const form = reactive({ username: '', display_name: '', role: 'user', password: '' })
const created = ref(null)

const resetTarget = ref(null)
const resetResult = ref(null)

async function load(page = 1) {
  busy.value = true
  errorMessage.value = ''
  try {
    const { data, meta: pageMeta } = await adminApi.users({
      page,
      page_size: meta.page_size,
      keyword: keyword.value || undefined,
    })
    users.value = data
    Object.assign(meta, pageMeta || {})
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    busy.value = false
  }
}

async function createUser() {
  errorMessage.value = ''
  successMessage.value = ''
  created.value = null
  try {
    const { data } = await adminApi.createUser({
      username: form.username,
      display_name: form.display_name,
      role: form.role,
      password: form.password || undefined,
    })
    created.value = data
    successMessage.value = `用户 ${data.user.username} 创建成功。`
    form.username = ''
    form.display_name = ''
    form.password = ''
    form.role = 'user'
    await load(1)
  } catch (error) {
    errorMessage.value = error.message
  }
}

async function setStatus(user, status) {
  errorMessage.value = ''
  successMessage.value = ''
  try {
    await adminApi.updateUser(user.id, { status })
    successMessage.value = `用户 ${user.username} 状态已改为 ${status}。`
    await load(meta.page)
  } catch (error) {
    errorMessage.value = error.message
  }
}

async function setRole(user, role) {
  errorMessage.value = ''
  try {
    await adminApi.updateUser(user.id, { role })
    successMessage.value = `用户 ${user.username} 角色已改为 ${role}。`
    await load(meta.page)
  } catch (error) {
    errorMessage.value = error.message
  }
}

async function resetPassword(user) {
  errorMessage.value = ''
  resetResult.value = null
  try {
    const { data } = await adminApi.resetPassword(user.id, {})
    resetResult.value = data
    resetTarget.value = user
  } catch (error) {
    errorMessage.value = error.message
  }
}

onMounted(() => load(1))
</script>

<template>
  <div>
    <div v-if="errorMessage" class="alert alert--error">{{ errorMessage }}</div>
    <div v-if="successMessage" class="alert alert--success">{{ successMessage }}</div>
    <div v-if="created?.initial_password" class="alert alert--info">
      初始密码（仅显示一次）：<strong class="mono">{{ created.initial_password }}</strong>
      —— 请通过安全渠道告知用户，并提醒其登录后立即修改。
    </div>
    <div v-if="resetResult" class="alert alert--info">
      已重置 {{ resetResult.user.username }} 的密码（仅显示一次）：
      <strong class="mono">{{ resetResult.new_password }}</strong>
    </div>

    <div class="grid grid--2">
      <section class="card">
        <div class="card__title"><h2>创建用户</h2></div>
        <form @submit.prevent="createUser">
          <div class="field">
            <label for="username">用户名</label>
            <input id="username" v-model="form.username" class="input" placeholder="3~32 位字母数字._-" />
          </div>
          <div class="field">
            <label for="display_name">显示名称</label>
            <input id="display_name" v-model="form.display_name" class="input" placeholder="可留空" />
          </div>
          <div class="field">
            <label for="role">角色</label>
            <select id="role" v-model="form.role" class="select">
              <option value="user">普通用户</option>
              <option value="admin">系统管理员</option>
            </select>
          </div>
          <div class="field">
            <label for="password">初始密码（留空则自动生成）</label>
            <input id="password" v-model="form.password" class="input" type="text" placeholder="留空自动生成随机密码" />
          </div>
          <button class="btn btn--primary" type="submit">创建</button>
        </form>
      </section>

      <section class="card">
        <div class="card__title">
          <h2>用户列表</h2>
          <div class="row">
            <input v-model="keyword" class="input" style="width: 160px" placeholder="搜索用户名" @keyup.enter="load(1)" />
            <button class="btn btn--sm" type="button" :disabled="busy" @click="load(1)">查询</button>
          </div>
        </div>
        <div class="table-wrap">
          <table class="data-table">
            <thead>
              <tr>
                <th>用户名</th>
                <th>显示名称</th>
                <th>角色</th>
                <th>状态</th>
                <th>最后登录</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="user in users" :key="user.id">
                <td class="mono">{{ user.username }}</td>
                <td>{{ user.display_name || '—' }}</td>
                <td>
                  <select class="select" :value="user.role" @change="setRole(user, $event.target.value)">
                    <option value="user">user</option>
                    <option value="admin">admin</option>
                  </select>
                </td>
                <td>
                  <span
                    class="badge"
                    :class="user.status === 'active' ? 'badge--success' : 'badge--danger'"
                  >
                    {{ user.status }}
                  </span>
                </td>
                <td class="mono small">{{ user.last_login_at || '—' }}</td>
                <td class="row">
                  <button
                    class="btn btn--sm"
                    type="button"
                    @click="setStatus(user, user.status === 'active' ? 'disabled' : 'active')"
                  >
                    {{ user.status === 'active' ? '禁用' : '启用' }}
                  </button>
                  <button class="btn btn--sm" type="button" @click="resetPassword(user)">重置密码</button>
                </td>
              </tr>
              <tr v-if="!users.length">
                <td colspan="6" class="empty">暂无用户</td>
              </tr>
            </tbody>
          </table>
        </div>
        <p class="small muted" style="margin-top: 8px">
          系统必须保留至少一个可用管理员账号（UC-06 扩展流程），禁用或降级最后一个管理员会被拒绝。
        </p>
      </section>
    </div>
  </div>
</template>
