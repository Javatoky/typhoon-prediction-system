<script setup>
/**
 * 预测工作台（FR-003 ~ FR-009）。
 * 主流程 ≤ 5 步：选择数据 → 上传/校验（可选）→ 开始预测 → 查看地图与表格 → 导出（NFR-007）。
 */
import { computed, onMounted, reactive, ref } from 'vue'
import { useRouter } from 'vue-router'
import { predictionApi, systemApi, typhoonApi, uploadApi } from '@/api'
import TrackMap from '@/components/TrackMap.vue'
import ForecastTable from '@/components/ForecastTable.vue'
import IntensityLegend from '@/components/IntensityLegend.vue'

const router = useRouter()

const config = ref(null)
const seasons = ref([])
const typhoons = ref([])
const uploads = ref([])
const searching = ref(false)

const source = ref('typhoon') // typhoon | upload
const filters = reactive({ keyword: '', season: '' })
const selectedTyphoonId = ref(null)
const selectedUploadId = ref(null)

const track = ref(null)
const prediction = ref(null)
const busy = ref(false)
const errorMessage = ref('')
const successMessage = ref('')

const fileInput = ref(null)
const uploadFile = ref(null)
const uploadReport = ref(null)
const uploading = ref(false)

const keyHours = computed(() => config.value?.key_forecast_hours || [6, 12, 24, 48, 72])
const focusArea = computed(() => config.value?.focus_area || null)
const inputLength = computed(() => config.value?.input_length || 5)

const uploadDetail = ref(null)

const actualPoints = computed(() => {
  if (source.value === 'typhoon' && track.value) return track.value.points
  if (source.value === 'upload' && uploadDetail.value) return uploadDetail.value.points
  return []
})

const predictedPoints = computed(() => prediction.value?.points || [])

const comparison = computed(() => prediction.value?.comparison || null)

async function loadConfig() {
  const { data } = await systemApi.config()
  config.value = data
}

async function loadSeasons() {
  const { data } = await typhoonApi.seasons()
  seasons.value = data.seasons || []
}

async function searchTyphoons() {
  searching.value = true
  errorMessage.value = ''
  try {
    const { data } = await typhoonApi.list({
      keyword: filters.keyword || undefined,
      season: filters.season || undefined,
      page_size: 50,
    })
    typhoons.value = data
    if (data.length && !selectedTyphoonId.value) {
      await selectTyphoon(data[0].id)
    }
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    searching.value = false
  }
}

async function loadUploads() {
  try {
    const { data } = await uploadApi.list({ page_size: 20 })
    uploads.value = data
    if (data.length && !selectedUploadId.value) {
      await selectUpload(data[0].id)
    }
  } catch (error) {
    errorMessage.value = error.message
  }
}

async function selectTyphoon(id) {
  selectedTyphoonId.value = id
  source.value = 'typhoon'
  prediction.value = null
  try {
    const { data } = await typhoonApi.track(id)
    track.value = data
  } catch (error) {
    errorMessage.value = error.message
  }
}

async function selectUpload(id) {
  selectedUploadId.value = id
  source.value = 'upload'
  prediction.value = null
  try {
    const { data } = await uploadApi.detail(id)
    uploadDetail.value = data
  } catch (error) {
    errorMessage.value = error.message
  }
}

function onFileChange(event) {
  const file = event.target.files?.[0] || null
  uploadFile.value = file
  uploadReport.value = null
}

async function validateFile() {
  if (!uploadFile.value) {
    errorMessage.value = '请先选择一个 CSV 文件。'
    return
  }
  uploading.value = true
  errorMessage.value = ''
  try {
    const { data } = await uploadApi.validate(uploadFile.value)
    uploadReport.value = data.validation
    successMessage.value = '校验完成，请查看数据质量报告。'
  } catch (error) {
    errorMessage.value = error.message
    uploadReport.value = error.details || null
  } finally {
    uploading.value = false
  }
}

async function submitUpload() {
  if (!uploadFile.value) {
    errorMessage.value = '请先选择一个 CSV 文件。'
    return
  }
  uploading.value = true
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const { data } = await uploadApi.upload(uploadFile.value)
    uploadReport.value = data.validation
    successMessage.value = `上传成功：${data.upload.valid_point_count} 个有效观测点已保存。`
    uploadFile.value = null
    if (fileInput.value) fileInput.value.value = ''
    await loadUploads()
    if (data.upload?.id) await selectUpload(data.upload.id)
  } catch (error) {
    errorMessage.value = error.message
    uploadReport.value = error.details || null
  } finally {
    uploading.value = false
  }
}

async function runPrediction() {
  busy.value = true
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const payload =
      source.value === 'typhoon'
        ? { typhoon_id: selectedTyphoonId.value }
        : { upload_id: selectedUploadId.value }
    const { data } = await predictionApi.create(payload)
    prediction.value = data
    if (data.status === 'succeeded') {
      successMessage.value = `预测成功，任务编号 ${data.task_code}（模型：${data.model_label || '持续性基线'}）。`
    } else {
      errorMessage.value = data.error_summary || '预测任务未成功完成。'
    }
  } catch (error) {
    errorMessage.value = error.message
    if (error.details) uploadReport.value = error.details
  } finally {
    busy.value = false
  }
}

async function exportResult() {
  if (!prediction.value) return
  try {
    const { blob, filename } = await predictionApi.export(prediction.value.id)
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

onMounted(async () => {
  try {
    await loadConfig()
    await Promise.all([loadSeasons(), searchTyphoons(), loadUploads()])
  } catch (error) {
    errorMessage.value = error.message
  }
})
</script>

<template>
  <div>
    <div v-if="errorMessage" class="alert alert--error">{{ errorMessage }}</div>
    <div v-if="successMessage" class="alert alert--success">{{ successMessage }}</div>

    <div class="grid grid--2">
      <!-- 第 1 步：选择数据 -->
      <section class="card">
        <div class="card__title"><h2>① 选择预测输入</h2></div>

        <div class="row" style="margin-bottom: 10px">
          <label class="row" style="gap: 4px">
            <input v-model="source" type="radio" value="typhoon" />历史台风
          </label>
          <label class="row" style="gap: 4px">
            <input v-model="source" type="radio" value="upload" />我的上传
          </label>
        </div>

        <template v-if="source === 'typhoon'">
          <div class="row" style="margin-bottom: 8px">
            <input v-model="filters.keyword" class="input" style="flex: 1" placeholder="台风名称或编号" />
            <select v-model="filters.season" class="select" style="width: 120px">
              <option value="">全部年份</option>
              <option v-for="year in seasons" :key="year" :value="year">{{ year }}</option>
            </select>
            <button class="btn" type="button" :disabled="searching" @click="searchTyphoons">
              {{ searching ? '查询中…' : '查询' }}
            </button>
          </div>
          <div class="table-wrap" style="max-height: 260px">
            <table class="data-table">
              <thead>
                <tr>
                  <th>编号</th>
                  <th>名称</th>
                  <th>年份</th>
                  <th>轨迹点</th>
                  <th>最大风速</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in typhoons" :key="item.id">
                  <td class="mono">{{ item.sid }}</td>
                  <td>{{ item.name || '—' }}</td>
                  <td>{{ item.season }}</td>
                  <td>{{ item.track_point_count }}</td>
                  <td>{{ item.max_wind_mps ?? '—' }} m/s</td>
                  <td>
                    <button class="btn btn--sm" type="button" @click="selectTyphoon(item.id)">
                      {{ selectedTyphoonId === item.id ? '已选择' : '选择' }}
                    </button>
                  </td>
                </tr>
                <tr v-if="!typhoons.length">
                  <td colspan="6" class="empty">
                    没有匹配的台风。请确认管理员已导入 IBTrACS 数据（管理后台 → 数据集管理）。
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </template>

        <template v-else>
          <div class="table-wrap" style="max-height: 260px">
            <table class="data-table">
              <thead>
                <tr>
                  <th>标签</th>
                  <th>编号</th>
                  <th>有效点</th>
                  <th>操作</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="item in uploads" :key="item.id">
                  <td>{{ item.label }}</td>
                  <td class="mono">{{ item.sid || '—' }}</td>
                  <td>{{ item.valid_point_count }}/{{ item.row_count }}</td>
                  <td>
                    <button class="btn btn--sm" type="button" @click="selectUpload(item.id)">
                      {{ selectedUploadId === item.id ? '已选择' : '选择' }}
                    </button>
                  </td>
                </tr>
                <tr v-if="!uploads.length">
                  <td colspan="4" class="empty">还没有上传记录，请在下方上传 CSV 轨迹文件。</td>
                </tr>
              </tbody>
            </table>
          </div>
        </template>

        <p class="field__hint">
          模型输入为最近 {{ inputLength }} 个 6 小时观测点（纬度、经度、风速、气压），
          输出未来 {{ config?.output_length || 12 }} 个时效点。
        </p>
      </section>

      <!-- 第 2 步：上传与校验 -->
      <section class="card">
        <div class="card__title"><h2>② 上传并校验 CSV（可选）</h2></div>
        <div class="field">
          <input ref="fileInput" class="input" type="file" accept=".csv" @change="onFileChange" />
          <p class="field__hint">
            UTF-8 编码，表头需包含 {{ (config?.limits?.csv_columns || ['sid', 'iso_time', 'lat', 'lon', 'max_wind_mps', 'pressure_hpa']).join('、') }}，
            大小不超过 {{ config?.limits?.max_upload_mb || 20 }} MB。
          </p>
        </div>
        <div class="row">
          <button class="btn" type="button" :disabled="uploading || !uploadFile" @click="validateFile">
            仅校验
          </button>
          <button class="btn btn--primary" type="button" :disabled="uploading || !uploadFile" @click="submitUpload">
            {{ uploading ? '处理中…' : '上传并保存' }}
          </button>
        </div>

        <div v-if="uploadReport" style="margin-top: 12px">
          <div
            class="alert"
            :class="uploadReport.ok ? 'alert--warning' : 'alert--error'"
          >
            <strong>数据质量报告：</strong>
            {{ uploadReport.ok ? '校验通过' : '校验未通过，已拒绝保存' }}；
            共 {{ uploadReport.row_count }} 行，有效点 {{ uploadReport.valid_point_count }} 个，
            错误 {{ uploadReport.error_count }} 处，提示 {{ uploadReport.warning_count }} 处。
            <template v-if="uploadReport.lon_converted">（经度已转换为 -180~180 口径）</template>
            <template v-if="uploadReport.interpolated_count">（插值 {{ uploadReport.interpolated_count }} 个点）</template>
          </div>
          <div v-if="uploadReport.errors?.length" class="report">
            <h3>错误明细</h3>
            <ul>
              <li v-for="(issue, index) in uploadReport.errors" :key="`e${index}`">
                <span class="mono">第 {{ issue.row ?? '—' }} 行</span>
                [{{ issue.code }}] {{ issue.message }}
              </li>
            </ul>
          </div>
          <div v-if="uploadReport.warnings?.length" class="report">
            <h3>提示信息</h3>
            <ul>
              <li v-for="(issue, index) in uploadReport.warnings" :key="`w${index}`">
                <span class="mono">第 {{ issue.row ?? '—' }} 行</span>
                [{{ issue.code }}] {{ issue.message }}
              </li>
            </ul>
          </div>
        </div>
      </section>
    </div>

    <!-- 第 3 步：开始预测 -->
    <section class="card">
      <div class="card__title">
        <h2>③ 开始预测</h2>
        <div class="row">
          <button
            class="btn"
            type="button"
            :disabled="busy || !prediction"
            @click="router.push({ name: 'task-detail', params: { id: prediction.id } })"
          >
            查看任务详情
          </button>
          <button class="btn" type="button" :disabled="busy || !prediction" @click="exportResult">
            导出 CSV
          </button>
          <button
            class="btn btn--primary"
            type="button"
            :disabled="busy || (source === 'typhoon' ? !selectedTyphoonId : !selectedUploadId)"
            @click="runPrediction"
          >
            {{ busy ? '推理中…' : '开始预测' }}
          </button>
        </div>
      </div>
      <p class="muted small">
        使用当前启用模型生成未来 {{ config?.output_length || 12 }} 个 6 小时时效点，
        并自动换算强度等级（
        <span class="mono">GB/T 19201-2006</span>）。
      </p>
    </section>

    <!-- 第 4 步：地图 -->
    <section class="card">
      <div class="card__title"><h2>④ 轨迹地图</h2></div>
      <TrackMap :actual="actualPoints" :predicted="predictedPoints" :focus-area="focusArea" />
    </section>

    <!-- 第 5 步：结果表格与误差 -->
    <div class="grid grid--2">
      <section class="card">
        <div class="card__title">
          <h2>⑤ 预测结果</h2>
          <span v-if="prediction" class="badge badge--info">{{ prediction.task_code }}</span>
        </div>
        <ForecastTable :points="predictedPoints" :key-hours="keyHours" />
      </section>

      <div>
        <section class="card">
          <div class="card__title"><h2>误差对比（FR-009）</h2></div>
          <template v-if="comparison">
            <p class="small">
              已匹配 {{ comparison.matched_points }} 个存在实测数据的时效点（Haversine 大圆距离）。
            </p>
            <div class="table-wrap" style="max-height: 220px">
              <table class="data-table">
                <thead>
                  <tr>
                    <th>时效</th>
                    <th>距离误差(km)</th>
                  </tr>
                </thead>
                <tbody>
                  <tr v-for="(value, hour) in comparison.distance_km" :key="hour">
                    <td>{{ hour }}h</td>
                    <td>{{ value }}</td>
                  </tr>
                </tbody>
              </table>
            </div>
            <p class="small muted" style="margin-top: 8px">
              ADE {{ comparison.ade_km ?? '—' }} km · 风速 MAE
              {{ comparison.wind_mae_mps ?? '—' }} m/s · 气压 MAE
              {{ comparison.pressure_mae_hpa ?? '—' }} hPa
            </p>
          </template>
          <p v-else class="muted small">
            尚不可对比：当前输入的最后一个观测时刻之后没有实测数据，因此无法计算分时效误差。
          </p>
        </section>

        <section class="card">
          <div class="card__title"><h2>强度等级阈值</h2></div>
          <IntensityLegend :thresholds="config?.intensity_thresholds || []" />
          <p class="small muted" style="margin-top: 8px">
            等级由预测风速按上表映射得到，仅用于教学展示；报告中须注明风速平均时段口径。
          </p>
        </section>
      </div>
    </div>
  </div>
</template>

<style scoped>
.report {
  margin-top: 8px;
  font-size: 13px;
}
.report h3 {
  font-size: 13px;
  margin-bottom: 4px;
}
.report ul {
  margin: 0;
  padding-left: 18px;
  line-height: 1.7;
  max-height: 180px;
  overflow: auto;
}
</style>
