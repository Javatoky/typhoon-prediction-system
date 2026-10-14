<script setup>
import { computed, onMounted, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '@/stores/auth'
import { systemApi } from '@/api'

const auth = useAuthStore()
const router = useRouter()
const route = useRoute()
const config = ref(null)
const modelStatus = ref(null)

const disclaimer = computed(
  () => config.value?.disclaimer || '结果仅用于教学研究与系统演示，不作为官方防灾决策依据。',
)

onMounted(async () => {
  try {
    const [configResponse, modelResponse] = await Promise.all([
      systemApi.config(),
      systemApi.modelStatus(),
    ])
    config.value = configResponse.data
    modelStatus.value = modelResponse.data
    auth.config = configResponse.data
  } catch (error) {
    // 配置加载失败不阻塞页面：提示信息由具体页面负责
    console.warn('加载系统配置失败：', error.message)
  }
})

async function handleLogout() {
  await auth.logout()
  router.push({ name: 'login' })
}

function isActive(name) {
  return route.name === name || String(route.name || '').startsWith(`${name}-`)
}
</script>

<template>
  <div class="layout">
    <aside class="layout__sidebar">
      <div class="layout__brand">
        <strong>台风路径预报</strong>
        <span class="small muted">Transformer 教学原型</span>
      </div>
      <nav class="layout__nav">
        <router-link :class="{ active: isActive('workspace') }" :to="{ name: 'workspace' }">
          预测工作台
        </router-link>
        <router-link :class="{ active: isActive('typhoons') }" :to="{ name: 'typhoons' }">
          历史台风
        </router-link>
        <router-link :class="{ active: isActive('tasks') || isActive('task-detail') }" :to="{ name: 'tasks' }">
          我的任务
        </router-link>
        <router-link :class="{ active: isActive('profile') }" :to="{ name: 'profile' }">
          账号设置
        </router-link>
        <template v-if="auth.isAdmin">
          <div class="layout__nav-group">管理员</div>
          <router-link :class="{ active: String(route.name || '').startsWith('admin') }" :to="{ name: 'admin-overview' }">
            管理后台
          </router-link>
        </template>
      </nav>
      <div class="layout__model small">
        <div class="muted">当前模型</div>
        <div>{{ modelStatus?.label || '加载中…' }}</div>
        <div v-if="modelStatus?.degraded" class="badge badge--warning" style="margin-top: 6px">
          基线降级中
        </div>
      </div>
    </aside>

    <div class="layout__main">
      <header class="layout__header">
        <div class="layout__title">{{ route.meta.title || '预测工作台' }}</div>
        <div class="spacer" />
        <span class="small muted">{{ auth.displayName }}</span>
        <span v-if="auth.isAdmin" class="badge badge--info">管理员</span>
        <button class="btn btn--sm" type="button" @click="handleLogout">退出登录</button>
      </header>

      <div class="layout__disclaimer">
        <span class="badge badge--warning">教学用途</span>
        <span>{{ disclaimer }}</span>
      </div>

      <main class="layout__content">
        <router-view />
      </main>
    </div>
  </div>
</template>

<style scoped>
.layout {
  display: flex;
  min-height: 100vh;
}
.layout__sidebar {
  width: var(--sidebar-width);
  flex: 0 0 var(--sidebar-width);
  background: #fff;
  border-right: 1px solid var(--color-border);
  display: flex;
  flex-direction: column;
  padding: 16px 0;
}
.layout__brand {
  padding: 0 16px 16px;
  display: flex;
  flex-direction: column;
  gap: 2px;
  border-bottom: 1px solid var(--color-border);
}
.layout__nav {
  display: flex;
  flex-direction: column;
  padding: 12px 8px;
  gap: 2px;
  flex: 1;
}
.layout__nav a {
  padding: 8px 10px;
  border-radius: 6px;
  color: var(--color-text);
}
.layout__nav a:hover {
  background: #f3f4f6;
  text-decoration: none;
}
.layout__nav a.active {
  background: #ddf4ff;
  color: #0969da;
  font-weight: 600;
}
.layout__nav-group {
  margin: 12px 10px 4px;
  font-size: 12px;
  color: var(--color-muted);
}
.layout__model {
  padding: 12px 16px;
  border-top: 1px solid var(--color-border);
}
.layout__main {
  flex: 1;
  min-width: 0;
  display: flex;
  flex-direction: column;
}
.layout__header {
  height: var(--header-height);
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 0 20px;
  background: #fff;
  border-bottom: 1px solid var(--color-border);
}
.layout__title {
  font-size: 16px;
  font-weight: 600;
}
.layout__disclaimer {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 6px 20px;
  background: #fff8c5;
  border-bottom: 1px solid #f2e08a;
  font-size: 12px;
  color: #7d4e00;
}
.layout__content {
  flex: 1;
  min-width: 0;
  padding: 20px;
}
</style>
