<script setup>
/**
 * 操作日志（FR-015）。
 */
import { onMounted, reactive, ref } from 'vue'
import { adminApi } from '@/api'

const logs = ref([])
const actions = ref([])
const meta = reactive({ page: 1, total: 0, pages: 0, page_size: 20 })
const filters = reactive({ action: '', actor: '', result: '' })
const busy = ref(false)
const errorMessage = ref('')

async function load(page = 1) {
  busy.value = true
  errorMessage.value = ''
  try {
    const { data, meta: pageMeta } = await adminApi.logs({
      page,
      page_size: meta.page_size,
      action: filters.action || undefined,
      actor: filters.actor || undefined,
      result: filters.result || undefined,
    })
    logs.value = data
    Object.assign(meta, pageMeta || {})
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    busy.value = false
  }
}

onMounted(async () => {
  try {
    const { data } = await adminApi.logActions()
    actions.value = data.actions || []
  } catch (error) {
    // 忽略：动作筛选项加载失败不影响主列表
  }
  await load(1)
})
</script>

<template>
  <div>
    <div v-if="errorMessage" class="alert alert--error">{{ errorMessage }}</div>

    <section class="card">
      <div class="card__title">
        <h2>操作日志</h2>
        <div class="row">
          <select v-model="filters.action" class="select" style="width: 170px" @change="load(1)">
            <option value="">全部动作</option>
            <option v-for="item in actions" :key="item.value" :value="item.value">
              {{ item.label }}
            </option>
          </select>
          <select v-model="filters.result" class="select" style="width: 120px" @change="load(1)">
            <option value="">全部结果</option>
            <option value="success">成功</option>
            <option value="failure">失败</option>
          </select>
          <input v-model="filters.actor" class="input" style="width: 140px" placeholder="操作者" @keyup.enter="load(1)" />
          <button class="btn btn--sm" type="button" :disabled="busy" @click="load(1)">查询</button>
        </div>
      </div>

      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>时间(UTC)</th>
              <th>操作者</th>
              <th>动作</th>
              <th>对象</th>
              <th>结果</th>
              <th>详情</th>
              <th>IP</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="log in logs" :key="log.id">
              <td class="mono small">{{ log.created_at }}</td>
              <td>{{ log.actor_username || '—' }}</td>
              <td class="mono small">{{ log.action }}</td>
              <td class="small">
                {{ log.target_type || '—' }}
                <template v-if="log.target_id">#{{ log.target_id }}</template>
              </td>
              <td>
                <span class="badge" :class="log.result === 'success' ? 'badge--success' : 'badge--danger'">
                  {{ log.result === 'success' ? '成功' : '失败' }}
                </span>
              </td>
              <td class="small">{{ log.detail || '—' }}</td>
              <td class="mono small">{{ log.ip_address || '—' }}</td>
            </tr>
            <tr v-if="!logs.length">
              <td colspan="7" class="empty">暂无日志</td>
            </tr>
          </tbody>
        </table>
      </div>

      <div class="row row--between" style="margin-top: 12px">
        <span class="small muted">共 {{ meta.total }} 条，第 {{ meta.page }} / {{ meta.pages || 1 }} 页</span>
        <div class="row">
          <button class="btn btn--sm" type="button" :disabled="meta.page <= 1" @click="load(meta.page - 1)">
            上一页
          </button>
          <button class="btn btn--sm" type="button" :disabled="meta.page >= meta.pages" @click="load(meta.page + 1)">
            下一页
          </button>
        </div>
      </div>
    </section>
  </div>
</template>
