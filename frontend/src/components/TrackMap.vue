<script setup>
/**
 * 轨迹地图（FR-008、7.1 用户界面要求）。
 *
 * - 实测轨迹与预测轨迹使用不同颜色/线型，并提供图例与时间提示。
 * - 在线底图不可用时仍显示经纬网与轨迹（降级：不加载瓦片，仅画网格与折线）。
 */
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import L from 'leaflet'

const props = defineProps({
  actual: { type: Array, default: () => [] }, // [{lat, lon, iso_time, max_wind_mps}]
  predicted: { type: Array, default: () => [] }, // [{lat, lon, valid_time, forecast_hour, is_key_hour}]
  focusArea: { type: Object, default: null },
  showGrid: { type: Boolean, default: true },
  height: { type: String, default: '440px' },
})

const container = ref(null)
const tilesFailed = ref(false)
let map = null
let tileLayer = null
const layers = {
  actual: null,
  predicted: null,
  markers: null,
  focus: null,
  grid: null,
}

const COLORS = {
  actual: '#1f6feb',
  predicted: '#d1242f',
}

function validPoints(points) {
  return (points || []).filter(
    (point) =>
      point &&
      Number.isFinite(Number(point.lat)) &&
      Number.isFinite(Number(point.lon)),
  )
}

function ensureMap() {
  if (map || !container.value) return map
  map = L.map(container.value, {
    center: [20, 130],
    zoom: 4,
    zoomControl: true,
    attributionControl: true,
    worldCopyJump: true,
  }).setView([20, 130], 4)

  tileLayer = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 8,
    minZoom: 2,
    attribution: '&copy; OpenStreetMap contributors',
  })
  tileLayer.on('tileerror', () => {
    tilesFailed.value = true
  })
  tileLayer.addTo(map)

  layers.actual = L.layerGroup().addTo(map)
  layers.predicted = L.layerGroup().addTo(map)
  layers.markers = L.layerGroup().addTo(map)
  layers.focus = L.layerGroup().addTo(map)
  layers.grid = L.layerGroup().addTo(map)
  return map
}

function clearGroup(group) {
  if (group) group.clearLayers()
}

function drawGrid() {
  clearGroup(layers.grid)
  if (!props.showGrid) return
  for (let lat = -80; lat <= 80; lat += 20) {
    L.polyline(
      [
        [lat, -180],
        [lat, 180],
      ],
      { color: '#c8d1da', weight: 1, dashArray: '3,6', interactive: false },
    ).addTo(layers.grid)
  }
  for (let lon = -180; lon <= 180; lon += 20) {
    L.polyline(
      [
        [-80, lon],
        [80, lon],
      ],
      { color: '#c8d1da', weight: 1, dashArray: '3,6', interactive: false },
    ).addTo(layers.grid)
  }
}

function drawFocusArea() {
  clearGroup(layers.focus)
  if (!props.focusArea) return
  const { lat_min, lat_max, lon_min, lon_max } = props.focusArea
  L.rectangle(
    [
      [lat_min, lon_min],
      [lat_max, lon_max],
    ],
    { color: '#bf8700', weight: 1, fill: false, dashArray: '6,4', interactive: false },
  )
    .bindTooltip('重点关注区（中国近海）', { sticky: true })
    .addTo(layers.focus)
}

function drawTracks() {
  clearGroup(layers.actual)
  clearGroup(layers.predicted)
  clearGroup(layers.markers)

  const actual = validPoints(props.actual)
  if (actual.length) {
    const coordinates = actual.map((point) => [Number(point.lat), Number(point.lon)])
    L.polyline(coordinates, {
      color: COLORS.actual,
      weight: 3,
      opacity: 0.9,
    })
      .bindTooltip(`实测轨迹（${actual.length} 个观测点）`)
      .addTo(layers.actual)
    actual.forEach((point) => {
      L.circleMarker([Number(point.lat), Number(point.lon)], {
        radius: 3,
        color: COLORS.actual,
        fillColor: '#fff',
        fillOpacity: 1,
        weight: 1,
      })
        .bindTooltip(
          `${point.iso_time || ''}<br/>风速 ${point.max_wind_mps ?? '—'} m/s`,
          { sticky: true },
        )
        .addTo(layers.actual)
    })
    addEndpointMarker(coordinates[coordinates.length - 1], '实测末点', COLORS.actual)
  }

  const predicted = validPoints(props.predicted)
  if (predicted.length) {
    const coordinates = predicted.map((point) => [Number(point.lat), Number(point.lon)])
    L.polyline(coordinates, {
      color: COLORS.predicted,
      weight: 3,
      dashArray: '6,6',
    })
      .bindTooltip(`预测轨迹（未来 ${predicted.length * 6} 小时）`)
      .addTo(layers.predicted)
    predicted.forEach((point) => {
      const key = point.is_key_hour
      L.circleMarker([Number(point.lat), Number(point.lon)], {
        radius: key ? 5 : 3,
        color: COLORS.predicted,
        fillColor: key ? COLORS.predicted : '#fff',
        fillOpacity: key ? 0.85 : 1,
        weight: 1.5,
      })
        .bindTooltip(
          `${point.forecast_hour} 小时（${point.valid_time || ''}）<br/>` +
            `风速 ${point.max_wind_mps ?? '—'} m/s<br/>` +
            `气压 ${point.central_pressure_hpa ?? '—'} hPa`,
          { sticky: true },
        )
        .addTo(layers.predicted)
    })
    addEndpointMarker(coordinates[coordinates.length - 1], '预测终点', COLORS.predicted)
  }

  fitBounds()
}

function addEndpointMarker(coordinate, label, color) {
  L.circleMarker(coordinate, {
    radius: 6,
    color,
    fillColor: color,
    fillOpacity: 0.9,
    weight: 2,
  })
    .bindTooltip(label, { permanent: false })
    .addTo(layers.markers)
}

function fitBounds() {
  const coordinates = [
    ...validPoints(props.actual).map((point) => [Number(point.lat), Number(point.lon)]),
    ...validPoints(props.predicted).map((point) => [Number(point.lat), Number(point.lon)]),
  ]
  if (!coordinates.length || !map) return
  map.fitBounds(L.latLngBounds(coordinates).pad(0.25), { maxZoom: 7 })
}

function redraw() {
  ensureMap()
  if (!map) return
  drawGrid()
  drawFocusArea()
  drawTracks()
  // 容器尺寸变化（例如切换布局）后需要让 Leaflet 重新计算
  map.invalidateSize()
}

onMounted(redraw)
onBeforeUnmount(() => {
  if (map) {
    map.remove()
    map = null
  }
})

watch(
  () => [props.actual, props.predicted, props.focusArea],
  () => redraw(),
  { deep: true },
)
</script>

<template>
  <div>
    <div ref="container" class="map" :style="{ height }" />
    <div class="legend">
      <span class="legend__item">
        <span class="legend__swatch" :style="{ background: '#1f6feb' }" />实测轨迹
      </span>
      <span class="legend__item">
        <span class="legend__swatch legend__swatch--dashed" />预测轨迹
      </span>
      <span class="legend__item">
        <span class="legend__swatch" :style="{ background: '#bf8700' }" />重点关注区
      </span>
      <span v-if="tilesFailed" class="legend__item muted">
        在线底图不可用，已降级为经纬网 + 轨迹显示
      </span>
    </div>
  </div>
</template>
