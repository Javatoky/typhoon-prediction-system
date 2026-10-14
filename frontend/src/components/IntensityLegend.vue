<script setup>
/**
 * 强度等级图例：阈值来自后端 /api/system/config，避免前端硬编码（NFR-009）。
 */
const props = defineProps({
  thresholds: { type: Array, default: () => [] },
})

function label(level) {
  const min = level.min_wind_mps
  const max = level.max_wind_mps
  if (max === null || max === undefined) return `≥ ${min} m/s`
  return `${min} ~ ${max} m/s`
}
</script>

<template>
  <div class="table-wrap" style="max-height: none">
    <table class="data-table">
      <thead>
        <tr>
          <th>等级</th>
          <th>名称</th>
          <th>风速区间</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="level in props.thresholds" :key="level.code">
          <td class="mono">{{ level.code }}</td>
          <td>{{ level.name_cn }}</td>
          <td>{{ label(level) }}</td>
        </tr>
        <tr v-if="!props.thresholds.length">
          <td colspan="3" class="empty">尚未加载强度阈值配置</td>
        </tr>
      </tbody>
    </table>
  </div>
</template>
