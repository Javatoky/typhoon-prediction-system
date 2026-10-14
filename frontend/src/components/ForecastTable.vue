<script setup>
/**
 * 预测结果表格（FR-008、7.1：表格展示所有 12 个预测时效点）。
 * 存在实测数据时同时显示误差列（FR-009）。
 */
const props = defineProps({
  points: { type: Array, default: () => [] },
  keyHours: { type: Array, default: () => [6, 12, 24, 48, 72] },
})

const emit = defineEmits(['select'])

function hasComparison() {
  return props.points.some((point) => point.actual && point.actual.lat != null)
}

function formatNumber(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return '—'
  return Number(value).toFixed(digits)
}

function categoryClass(code) {
  if (!code || code === 'NA') return 'badge badge--muted'
  if (code === 'SuperTY' || code === 'STY') return 'badge badge--danger'
  if (code === 'TY' || code === 'STS') return 'badge badge--warning'
  return 'badge badge--info'
}
</script>

<template>
  <div class="table-wrap">
    <table class="data-table">
      <thead>
        <tr>
          <th>时效</th>
          <th>有效时间(UTC)</th>
          <th>纬度</th>
          <th>经度</th>
          <th>风速(m/s)</th>
          <th>气压(hPa)</th>
          <th>强度等级</th>
          <template v-if="hasComparison()">
            <th>实测纬度</th>
            <th>实测经度</th>
            <th>距离误差(km)</th>
          </template>
        </tr>
      </thead>
      <tbody>
        <tr v-for="point in points" :key="point.forecast_hour" @click="emit('select', point)">
          <td>
            <strong>{{ point.forecast_hour }}h</strong>
            <span v-if="keyHours.includes(point.forecast_hour)" class="badge badge--info" style="margin-left: 6px">
              关键
            </span>
          </td>
          <td class="mono small">{{ point.valid_time || '—' }}</td>
          <td>{{ formatNumber(point.lat, 2) }}</td>
          <td>{{ formatNumber(point.lon, 2) }}</td>
          <td>{{ formatNumber(point.max_wind_mps, 1) }}</td>
          <td>{{ formatNumber(point.central_pressure_hpa, 1) }}</td>
          <td>
            <span :class="categoryClass(point.intensity_category)">
              {{ point.intensity_name || point.intensity_category || '未定级' }}
            </span>
          </td>
          <template v-if="hasComparison()">
            <td>{{ formatNumber(point.actual?.lat, 2) }}</td>
            <td>{{ formatNumber(point.actual?.lon, 2) }}</td>
            <td>{{ formatNumber(point.error_km, 1) }}</td>
          </template>
        </tr>
        <tr v-if="!points.length">
          <td :colspan="hasComparison() ? 10 : 7" class="empty">暂无预测结果</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
