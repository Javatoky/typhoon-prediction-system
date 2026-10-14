<script setup>
/**
 * 我的任务列表（FR-010）。
 */
import { onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { predictionApi } from '@/api'

const router = useRouter()
const tasks = ref([])
const meta = reactive({ page: 1, total: 0, pages: 0, page_size: 20 })
const filters = reactive({ status: '' })
const loading = ref(false)
const errorMessage = ref('')

const STATUS_LABELS = {
  created: ['已创建', 'badge--muted'],
  validating: ['校验中', 'badge--info'],
  running: ['推理中', 'badge--info'],
  succeeded: ['成功', 'badge--success'],
  failed: ['失败', 'badge--danger'],
}

function statusLabel(status) {
  return STATUS_LABELS[status]?.[0] || status
}
function statusClass(status) {
  return `badge ${STATUS_LABELS[status]?.[1] || 'badge--muted'}`
}

async function load(page = 1) {
  loading.value = true
  errorMessage.value = ''
  try {
    const { data, meta: pageMeta } = await predictionApi.list({
      page,
      page_size: meta.page_size,
      status: filters.status || undefined,
    })
    tasks.value = data
    Object.assign(meta, pageMeta || {})
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    loading.value = false
  }
}

onMounted(() => load(1))
</script>

<template>
  <div>
    <div v-if="errorMessage" class="alert alert--error">{{ errorMessage }}</div>

    <section class="card">
      <div class="card__title">
        <h2>我的预测任务</h2>
        <div class="row">
          <select v-model="filters.status" class="select" style="width: 140px" @change="load(1)">
            <option value="">全部状态</option>
            <option value="succeeded">成功</option>
            <option value="failed">失败</option>
            <option value="running">推理中</option>
          </select>
          <button class="btn" type="button" :disabled="loading" @click="load(meta.page)">
            {{ loading ? '加载中…' : '刷新' }}
          </button>
          <router-link class="btn btn--primary" :to="{ name: 'workspace' }">新建预测</router-link>
        </div>
      </div>

      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>任务编号</th>
              <th>输入</th>
              <th>模型</th>
              <th>数据版本</th>
              <th>状态</th>
              <th>耗时(ms)</th>
              <th>创建时间(UTC)</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="task in tasks" :key="task.id">
              <td class="mono">{{ task.task_code }}</td>
              <td>{{ task.typhoon_name || '—' }}</td>
              <td>{{ task.model_label || '持续性基线' }}</td>
              <td class="small">{{ task.data_version || '—' }}</td>
              <td><span :class="statusClass(task.status)">{{ statusLabel(task.status) }}</span></td>
              <td>{{ task.duration_ms ?? '—' }}</td>
              <td class="mono small">{{ task.created_at }}</td>
              <td class="row">
                <button
                  class="btn btn--sm"
                  type="button"
                  @click="router.push({ name: 'task-detail', params: { id: task.id } })"
                >
                  详情
                </button>
              </td>
            </tr>
            <tr v-if="!tasks.length">
              <td colspan="8" class="empty">还没有任务，去“预测工作台”创建第一个预测任务。</td>
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
          <button
            class="btn btn--sm"
            type="button"
            :disabled="meta.page >= meta.pages"
            @click="load(meta.page + 1)"
          >
            下一页
          </button>
        </div>
      </div>
    </section>
  </div>
</template>
