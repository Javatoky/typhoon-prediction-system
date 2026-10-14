<script setup>
/**
 * 管理概览：账号、数据、任务与当前模型状态。
 */
import { onMounted, ref } from 'vue'
import { adminApi } from '@/api'

const overview = ref(null)
const errorMessage = ref('')
const busy = ref(false)

async function load() {
  busy.value = true
  try {
    const { data } = await adminApi.overview()
    overview.value = data
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    busy.value = false
  }
}

async function enableBaseline() {
  errorMessage.value = ''
  try {
    await adminApi.ensureBaseline()
    await load()
  } catch (error) {
    errorMessage.value = error.message
  }
}

onMounted(load)
</script>

<template>
  <div>
    <div v-if="errorMessage" class="alert alert--error">{{ errorMessage }}</div>

    <section class="card">
      <div class="card__title">
        <h2>系统概览</h2>
        <button class="btn btn--sm" type="button" :disabled="busy" @click="load">
          {{ busy ? '刷新中…' : '刷新' }}
        </button>
      </div>
      <div v-if="overview" class="grid grid--4">
        <div class="stat">
          <div class="stat__label">账号总数</div>
          <div class="stat__value">{{ overview.users.total }}</div>
          <div class="small muted">
            启用 {{ overview.users.active }} · 管理员 {{ overview.users.admin }}
          </div>
        </div>
        <div class="stat">
          <div class="stat__label">历史台风</div>
          <div class="stat__value">{{ overview.data.typhoons }}</div>
          <div class="small muted">轨迹点 {{ overview.data.track_points }}</div>
        </div>
        <div class="stat">
          <div class="stat__label">预测任务</div>
          <div class="stat__value">{{ overview.tasks.task_total }}</div>
          <div class="small muted">
            成功 {{ overview.tasks.task_succeeded }} · 失败 {{ overview.tasks.task_failed }}
          </div>
        </div>
        <div class="stat">
          <div class="stat__label">平均推理耗时</div>
          <div class="stat__value">{{ overview.tasks.avg_duration_ms ?? '—' }}</div>
          <div class="small muted">毫秒（NFR-002 目标 &lt; 5000）</div>
        </div>
      </div>
    </section>

    <section class="card">
      <div class="card__title">
        <h2>当前推理模型</h2>
        <button class="btn btn--sm" type="button" @click="enableBaseline">启用内置基线</button>
      </div>
      <div v-if="overview" class="model-status">
        <p>
          <strong>{{ overview.model.label }}</strong>
          <span class="badge" :class="overview.model.degraded ? 'badge--warning' : 'badge--success'">
            {{ overview.model.degraded ? '基线降级' : '已加载模型' }}
          </span>
        </p>
        <p class="small muted">
          来源 {{ overview.model.source || '—' }} · 类型 {{ overview.model.kind }} ·
          输入 {{ overview.model.input_length }} × 输出 {{ overview.model.output_length }} ·
          PyTorch {{ overview.model.torch_available ? '可用' : '未安装' }}
        </p>
        <p v-if="overview.model.load_error" class="alert alert--warning" style="margin-top: 8px">
          模型加载告警：{{ overview.model.load_error }}
        </p>
        <p class="small muted">
          当没有任何已启用模型时，系统自动使用持续性基线完成闭环（13.1 第 2 步），
          此时预测结果不代表 Transformer 能力。
        </p>
      </div>
    </section>
  </div>
</template>

<style scoped>
.model-status p {
  margin: 4px 0;
}
.model-status strong {
  margin-right: 8px;
}
</style>
