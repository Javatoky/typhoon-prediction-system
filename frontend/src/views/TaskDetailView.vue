<script setup>
/**
 * 任务详情：地图 + 表格 + 误差 + 导出（FR-008/009/011、UC-05）。
 */
import { computed, onMounted, ref } from 'vue'
import { useRoute } from 'vue-router'
import { predictionApi, systemApi, typhoonApi } from '@/api'
import TrackMap from '@/components/TrackMap.vue'
import ForecastTable from '@/components/ForecastTable.vue'

const route = useRoute()
const task = ref(null)
const actual = ref([])
const config = ref(null)
const loading = ref(true)
const errorMessage = ref('')

const keyHours = computed(() => config.value?.key_forecast_hours || [6, 12, 24, 48, 72])

async function load() {
  loading.value = true
  errorMessage.value = ''
  try {
    const [taskResponse, configResponse] = await Promise.all([
      predictionApi.detail(route.params.id),
      systemApi.config().catch(() => ({ data: null })),
    ])
    task.value = taskResponse.data
    config.value = configResponse.data

    if (task.value.typhoon_id) {
      const { data } = await typhoonApi.track(task.value.typhoon_id)
      actual.value = data.points
    }
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    loading.value = false
  }
}

async function exportCsv() {
  try {
    const { blob, filename } = await predictionApi.export(route.params.id)
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    document.body.appendChild(link)
    link.click()
    link.remove()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = `导出失败：${error.message}`
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div v-if="errorMessage" class="alert alert--error">{{ errorMessage }}</div>
    <div v-if="loading" class="loading">加载任务详情中…</div>

    <template v-if="task">
      <section class="card">
        <div class="card__title">
          <h2>
            任务
            <span class="mono">{{ task.task_code }}</span>
          </h2>
          <div class="row">
            <span
              class="badge"
              :class="{
                'badge--success': task.status === 'succeeded',
                'badge--danger': task.status === 'failed',
                'badge--info': task.status === 'running' || task.status === 'validating',
                'badge--muted': task.status === 'created',
              }"
            >
              {{ task.status }}
            </span>
            <button class="btn btn--primary" type="button" @click="exportCsv">导出 CSV</button>
          </div>
        </div>
        <div class="grid grid--4">
          <div class="stat">
            <div class="stat__label">输入来源</div>
            <div class="stat__value" style="font-size: 15px">
              {{ task.input_source === 'typhoon' ? '历史台风' : '上传数据' }}
            </div>
          </div>
          <div class="stat">
            <div class="stat__label">模型版本</div>
            <div class="stat__value" style="font-size: 15px">{{ task.model_label || '持续性基线' }}</div>
          </div>
          <div class="stat">
            <div class="stat__label">数据版本</div>
            <div class="stat__value" style="font-size: 15px">{{ task.data_version || '—' }}</div>
          </div>
          <div class="stat">
            <div class="stat__label">推理耗时</div>
            <div class="stat__value" style="font-size: 15px">{{ task.duration_ms ?? '—' }} ms</div>
          </div>
        </div>
        <p class="small muted" style="margin-top: 10px">
          输入窗口：{{ task.input_start_time }} → {{ task.input_end_time }}（UTC）；
          创建时间 {{ task.created_at }}；{{ task.message }}
        </p>
        <div v-if="task.error_summary" class="alert alert--error" style="margin-top: 10px">
          {{ task.error_summary }}
        </div>
      </section>

      <section class="card">
        <div class="card__title"><h2>轨迹地图</h2></div>
        <TrackMap :actual="actual" :predicted="task.points || []" :focus-area="config?.focus_area" />
      </section>

      <div class="grid grid--2">
        <section class="card">
          <div class="card__title"><h2>预测结果</h2></div>
          <ForecastTable :points="task.points || []" :key-hours="keyHours" />
        </section>

        <section class="card">
          <div class="card__title"><h2>误差对比</h2></div>
          <template v-if="task.comparison">
            <div class="table-wrap" style="max-height: 240px">
              <table class="data-table">
                <thead>
                  <tr>
                    <th>时效</th>
                    <th>距离误差(km)</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(value, hour) in task.comparison.distance_km" :key="hour">
                    <td>{{ hour }}h</td>
                    <td>{{ value }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p class="small muted" style="margin-top: 8px">
              ADE {{ task.comparison.ade_km ?? '—' }} km · 风速 MAE
              {{ task.comparison.wind_mae_mps ?? '—' }} m/s · 气压 MAE
              {{ task.comparison.pressure_mae_hpa ?? '—' }} hPa；
              匹配 {{ task.comparison.matched_points }} 个时效点。
            </p>
          </template>
          <p v-else class="muted small">
            无实测数据可对比：该输入的最后观测时刻之后没有实测轨迹，误差列暂不可用（UC-05 扩展流程）。
          </p>

          <h3 style="margin-top: 16px">输入窗口（模型输入快照）</h3>
          <div class="table-wrap" style="max-height: 200px">
            <table class="data-table">
              <thead>
                <tr>
                  <th>lat</th>
                  <th>lon</th>
                  <th>max_wind_mps</th>
                  <th>central_pressure_hpa</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(row, index) in task.input_window?.window || []" :key="index">
                  <td>{{ row[0] }}</td>
                  <td>{{ row[1] }}</td>
                  <td>{{ row[2] }}</td>
                  <td>{{ row[3] }}</td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>
      </div>
    </template>
  </div>
</template>
