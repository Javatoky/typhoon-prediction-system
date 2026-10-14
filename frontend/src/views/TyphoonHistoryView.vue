<script setup>
/**
 * 历史台风查询与实测轨迹查看（FR-003、UC-02）。
 */
import { onMounted, reactive, ref } from 'vue'
import { typhoonApi, systemApi } from '@/api'
import TrackMap from '@/components/TrackMap.vue'
import IntensityLegend from '@/components/IntensityLegend.vue'

const filters = reactive({ keyword: '', season: '', focus_only: false })
const list = ref([])
const seasons = ref([])
const detail = ref(null)
const loading = ref(false)
const errorMessage = ref('')
const config = ref(null)

async function search() {
  loading.value = true
  errorMessage.value = ''
  try {
    const { data } = await typhoonApi.list({
      keyword: filters.keyword || undefined,
      season: filters.season || undefined,
      focus_only: filters.focus_only || undefined,
      page_size: 100,
    })
    list.value = data
    detail.value = null
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    loading.value = false
  }
}

async function openTrack(id) {
  errorMessage.value = ''
  try {
    const { data } = await typhoonApi.track(id)
    detail.value = data
  } catch (error) {
    errorMessage.value = error.message
  }
}

onMounted(async () => {
  try {
    const [configResponse, seasonResponse] = await Promise.all([
      systemApi.config(),
      typhoonApi.seasons(),
    ])
    config.value = configResponse.data
    seasons.value = seasonResponse.data.seasons || []
    await search()
  } catch (error) {
    errorMessage.value = error.message
  }
})
</script>

<template>
  <div>
    <div v-if="errorMessage" class="alert alert--error">{{ errorMessage }}</div>

    <section class="card">
      <div class="card__title"><h2>历史台风查询</h2></div>
      <div class="row">
        <input
          v-model="filters.keyword"
          class="input"
          style="flex: 1; min-width: 200px"
          placeholder="台风名称或编号（WP 编号）"
          @keyup.enter="search"
        />
        <select v-model="filters.season" class="select" style="width: 140px">
          <option value="">全部年份</option>
          <option v-for="year in seasons" :key="year" :value="year">{{ year }}</option>
        </select>
        <label class="row" style="gap: 4px">
          <input v-model="filters.focus_only" type="checkbox" />仅看进入关注区
        </label>
        <button class="btn btn--primary" type="button" :disabled="loading" @click="search">
          {{ loading ? '查询中…' : '查询' }}
        </button>
      </div>
      <p class="field__hint">
        关注区为 {{ config?.focus_area?.lon_min }}°E–{{ config?.focus_area?.lon_max }}°E、
        {{ config?.focus_area?.lat_min }}°N–{{ config?.focus_area?.lat_max }}°N。
      </p>
    </section>

    <div class="grid grid--2">
      <section class="card">
        <div class="card__title">
          <h2>台风列表</h2>
          <span class="small muted">共 {{ list.length }} 条</span>
        </div>
        <div class="table-wrap">
          <table class="data-table">
            <thead>
              <tr>
                <th>编号</th>
                <th>名称</th>
                <th>年份</th>
                <th>轨迹点</th>
                <th>最大风速</th>
                <th>最低气压</th>
                <th>关注区</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="item in list" :key="item.id">
                <td class="mono">{{ item.sid }}</td>
                <td>{{ item.name || '—' }}</td>
                <td>{{ item.season }}</td>
                <td>{{ item.track_point_count }}</td>
                <td>{{ item.max_wind_mps ?? '—' }}</td>
                <td>{{ item.min_pressure_hpa ?? '—' }}</td>
                <td>
                  <span v-if="item.in_focus_area" class="badge badge--info">是</span>
                  <span v-else class="badge badge--muted">否</span>
                </td>
                <td>
                  <button class="btn btn--sm" type="button" @click="openTrack(item.id)">查看轨迹</button>
                </td>
              </tr>
              <tr v-if="!list.length">
                <td colspan="8" class="empty">
                  没有匹配数据（空结果不是错误）。若历史数据为空，请管理员在“数据集管理”中导入 IBTrACS。
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <div>
        <section class="card">
          <div class="card__title">
            <h2>实测轨迹</h2>
            <span v-if="detail" class="small muted">
              {{ detail.typhoon.sid }} {{ detail.typhoon.name }} ·
              {{ detail.point_count }} 点 ·
              插值 {{ detail.interpolated_count }} 点
            </span>
          </div>
          <TrackMap :actual="detail?.points || []" :focus-area="config?.focus_area" height="360px" />
          <p v-if="!detail" class="muted small">请在左侧选择一个台风查看实测轨迹。</p>
        </section>

        <section v-if="detail" class="card">
          <div class="card__title"><h3>轨迹数据（含质量标志）</h3></div>
          <div class="table-wrap" style="max-height: 260px">
            <table class="data-table">
              <thead>
                <tr>
                  <th>时间(UTC)</th>
                  <th>纬度</th>
                  <th>经度</th>
                  <th>风速(m/s)</th>
                  <th>气压(hPa)</th>
                  <th>等级</th>
                  <th>质量标志</th>
                </tr>
              </thead>
              <tbody>
                <tr v-for="(point, index) in detail.points" :key="index">
                  <td class="mono small">{{ point.iso_time }}</td>
                  <td>{{ point.lat }}</td>
                  <td>{{ point.lon }}</td>
                  <td>{{ point.max_wind_mps ?? '—' }}</td>
                  <td>{{ point.central_pressure_hpa ?? '—' }}</td>
                  <td>{{ point.intensity_name || point.intensity_code }}</td>
                  <td>
                    <span
                      class="badge"
                      :class="point.quality_flag === 'original' ? 'badge--muted' : 'badge--warning'"
                    >
                      {{ point.quality_flag }}
                    </span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </section>

        <section class="card">
          <div class="card__title"><h3>强度等级阈值</h3></div>
          <IntensityLegend :thresholds="config?.intensity_thresholds || []" />
        </section>
      </div>
    </div>
  </div>
</template>
