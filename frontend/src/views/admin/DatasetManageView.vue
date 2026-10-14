<script setup>
/**
 * 数据集管理（FR-013、UC-07）。
 */
import { onMounted, reactive, ref } from 'vue'
import { adminApi } from '@/api'

const imports = ref([])
const meta = reactive({ page: 1, total: 0, pages: 0, page_size: 20 })
const fileInput = ref(null)
const form = reactive({ source: 'IBTrACS', source_version: 'v04r01', strict: false })
const busy = ref(false)
const errorMessage = ref('')
const successMessage = ref('')

async function load(page = 1) {
  busy.value = true
  errorMessage.value = ''
  try {
    const { data, meta: pageMeta } = await adminApi.datasets({ page, page_size: meta.page_size })
    imports.value = data
    Object.assign(meta, pageMeta || {})
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    busy.value = false
  }
}

async function submit() {
  errorMessage.value = ''
  successMessage.value = ''
  const file = fileInput.value?.files?.[0]
  if (!file) {
    errorMessage.value = '请选择要导入的 CSV 文件。'
    return
  }
  const formData = new FormData()
  formData.append('file', file)
  formData.append('source', form.source)
  formData.append('source_version', form.source_version)
  if (form.strict) formData.append('strict', 'true')

  busy.value = true
  try {
    const { data } = await adminApi.importDataset(formData)
    successMessage.value = `导入完成：${data.import.typhoon_count} 个台风、${data.stats.point_count} 个轨迹点（跳过 ${data.import.skipped_row_count} 行）。`
    if (fileInput.value) fileInput.value.value = ''
    await load(1)
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    busy.value = false
  }
}

onMounted(() => load(1))
</script>

<template>
  <div>
    <div v-if="errorMessage" class="alert alert--error">{{ errorMessage }}</div>
    <div v-if="successMessage" class="alert alert--success">{{ successMessage }}</div>

    <section class="card">
      <div class="card__title"><h2>导入数据集</h2></div>
      <div class="grid grid--3">
        <div class="field">
          <label for="source">数据来源</label>
          <input id="source" v-model="form.source" class="input" placeholder="IBTrACS" />
        </div>
        <div class="field">
          <label for="version">数据版本</label>
          <input id="version" v-model="form.source_version" class="input" placeholder="v04r01" />
        </div>
        <div class="field">
          <label for="file">CSV 文件</label>
          <input id="file" ref="fileInput" class="input" type="file" accept=".csv" />
        </div>
      </div>
      <label class="row" style="gap: 4px">
        <input v-model="form.strict" type="checkbox" />
        严格模式：任一行不合法则整批回滚（默认跳过错误行并统计）
      </label>
      <p class="field__hint">
        支持 IBTrACS v4 原始列名（SID、ISO_TIME、LAT、LON、USA_WIND、USA_PRES，风速按 knot 自动换算为 m/s）
        与本项目标准列名。建议只导入 WP 盆地子集以保证导入速度。
      </p>
      <button class="btn btn--primary" type="button" :disabled="busy" @click="submit">
        {{ busy ? '导入中…' : '开始导入' }}
      </button>
    </section>

    <section class="card">
      <div class="card__title">
        <h2>导入记录</h2>
        <button class="btn btn--sm" type="button" :disabled="busy" @click="load(meta.page)">刷新</button>
      </div>
      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>ID</th>
              <th>来源</th>
              <th>版本</th>
              <th>文件</th>
              <th>台风数</th>
              <th>行数</th>
              <th>跳过</th>
              <th>状态</th>
              <th>导入时间</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="item in imports" :key="item.id">
              <td>{{ item.id }}</td>
              <td>{{ item.source }}</td>
              <td>{{ item.source_version || '—' }}</td>
              <td class="small">{{ item.original_filename || '—' }}</td>
              <td>{{ item.typhoon_count }}</td>
              <td>{{ item.row_count }}</td>
              <td>{{ item.skipped_row_count }}</td>
              <td>
                <span
                  class="badge"
                  :class="item.status === 'imported' ? 'badge--success' : item.status === 'rolled_back' ? 'badge--danger' : 'badge--muted'"
                >
                  {{ item.status }}
                </span>
              </td>
              <td class="mono small">{{ item.imported_at || item.created_at }}</td>
            </tr>
            <tr v-if="!imports.length">
              <td colspan="9" class="empty">暂无导入记录</td>
            </tr>
          </tbody>
        </table>
      </div>
    </section>
  </div>
</template>
