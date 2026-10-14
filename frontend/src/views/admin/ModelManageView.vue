<script setup>
/**
 * 模型管理（FR-014、UC-08）：上传 PT + 元数据，试加载通过后方可启用。
 */
import { onMounted, reactive, ref } from 'vue'
import { adminApi } from '@/api'

const models = ref([])
const runtime = ref(null)
const errorMessage = ref('')
const successMessage = ref('')
const busy = ref(false)

const weightsInput = ref(null)
const metadataInput = ref(null)
const scalerInput = ref(null)
const form = reactive({ name: '', version: '', activate: true })

async function load() {
  busy.value = true
  errorMessage.value = ''
  try {
    const [{ data }, status] = await Promise.all([
      adminApi.models({ page_size: 50 }),
      adminApi.runtimeStatus(),
    ])
    models.value = data
    runtime.value = status.data
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    busy.value = false
  }
}

async function upload() {
  errorMessage.value = ''
  successMessage.value = ''
  const weights = weightsInput.value?.files?.[0]
  const metadata = metadataInput.value?.files?.[0]
  const scaler = scalerInput.value?.files?.[0]
  if (!weights && !metadata) {
    errorMessage.value = '请至少选择权重文件（.pt）或元数据文件（.json）。'
    return
  }
  const formData = new FormData()
  if (weights) formData.append('weights', weights)
  if (metadata) formData.append('metadata', metadata)
  if (scaler) formData.append('scaler', scaler)
  if (form.name) formData.append('name', form.name)
  if (form.version) formData.append('version', form.version)
  if (form.activate) formData.append('activate', 'true')

  busy.value = true
  try {
    const { data } = await adminApi.uploadModel(formData)
    successMessage.value =
      `模型 ${data.model.name} ${data.model.version} 上传成功` +
      (data.model.is_active ? '，并已设为当前模型。' : '（尚未启用）。') +
      (data.warnings?.length ? ` 提示：${data.warnings.join('；')}` : '')
    if (weightsInput.value) weightsInput.value.value = ''
    if (metadataInput.value) metadataInput.value.value = ''
    if (scalerInput.value) scalerInput.value.value = ''
    await load()
  } catch (error) {
    errorMessage.value = error.message
  } finally {
    busy.value = false
  }
}

async function activate(model) {
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const { data } = await adminApi.activateModel(model.id)
    successMessage.value = `当前模型已切换为 ${data.model.name} ${data.model.version}（旧任务保留原模型版本号）。`
    await load()
  } catch (error) {
    errorMessage.value = error.message
  }
}

async function useBaseline() {
  errorMessage.value = ''
  successMessage.value = ''
  try {
    await adminApi.ensureBaseline()
    successMessage.value = '已启用内置持续性基线模型（用于尚未训练出 Transformer 的阶段）。'
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
    <div v-if="successMessage" class="alert alert--success">{{ successMessage }}</div>

    <section class="card">
      <div class="card__title">
        <h2>当前推理模型</h2>
        <button class="btn btn--sm" type="button" @click="useBaseline">启用内置基线</button>
      </div>
      <div v-if="runtime">
        <p>
          <strong>{{ runtime.runtime.label }}</strong>
          <span class="badge" :class="runtime.runtime.degraded ? 'badge--warning' : 'badge--success'">
            {{ runtime.runtime.degraded ? '基线降级' : '已加载' }}
          </span>
        </p>
        <p class="small muted">
          来源 {{ runtime.runtime.source || '—' }} · PyTorch
          {{ runtime.runtime.torch_available ? '可用' : '未安装（无法加载 .pt 权重）' }}
        </p>
        <p v-if="runtime.runtime.load_error" class="alert alert--warning">
          {{ runtime.runtime.load_error }}
        </p>
      </div>
    </section>

    <section class="card">
      <div class="card__title"><h2>上传模型包</h2></div>
      <div class="grid grid--3">
        <div class="field">
          <label for="name">模型名称（元数据无 name 时必填）</label>
          <input id="name" v-model="form.name" class="input" placeholder="TyphoonTransformer" />
        </div>
        <div class="field">
          <label for="version">版本号（元数据无 version 时必填）</label>
          <input id="version" v-model="form.version" class="input" placeholder="0.1.0" />
        </div>
        <div class="field">
          <label for="weights">权重文件（.pt）</label>
          <input id="weights" ref="weightsInput" class="input" type="file" accept=".pt,.pth" />
        </div>
        <div class="field">
          <label for="metadata">元数据（.json，训练脚本自动生成）</label>
          <input id="metadata" ref="metadataInput" class="input" type="file" accept=".json" />
        </div>
        <div class="field">
          <label for="scaler">标准化器（可选 .json）</label>
          <input id="scaler" ref="scalerInput" class="input" type="file" accept=".json" />
          <p class="field__hint">若元数据中已包含 scaler 字段，可不上传此项。</p>
        </div>
      </div>
      <label class="row" style="gap: 4px">
        <input v-model="form.activate" type="checkbox" />上传校验通过后立即设为当前模型
      </label>
      <p class="field__hint">
        系统会先在隔离过程中试加载权重并校验输入输出维度（4 特征 / 5 输入 / 12 输出），
        失败则拒绝启用且不影响当前模型（UC-08 异常流程）。
      </p>
      <button class="btn btn--primary" type="button" :disabled="busy" @click="upload">
        {{ busy ? '处理中…' : '上传' }}
      </button>
    </section>

    <section class="card">
      <div class="card__title">
        <h2>模型版本列表</h2>
        <button class="btn btn--sm" type="button" :disabled="busy" @click="load">刷新</button>
      </div>
      <div class="table-wrap">
        <table class="data-table">
          <thead>
            <tr>
              <th>名称</th>
              <th>版本</th>
              <th>类型</th>
              <th>结构</th>
              <th>数据版本</th>
              <th>可用</th>
              <th>当前</th>
              <th>创建时间</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="model in models" :key="model.id">
              <td>{{ model.name }}</td>
              <td class="mono">{{ model.version }}</td>
              <td>{{ model.kind }}</td>
              <td class="small">
                {{ model.input_length }}→{{ model.output_length }} · F{{ model.feature_dim }}
                <template v-if="model.hyperparams?.d_model">
                  · d{{ model.hyperparams.d_model }}/h{{ model.hyperparams.nhead }}/L{{ model.hyperparams.num_encoder_layers }}
                </template>
              </td>
              <td class="small">{{ model.training_data_version || '—' }}</td>
              <td>
                <span class="badge" :class="model.is_available ? 'badge--success' : 'badge--muted'">
                  {{ model.is_available ? '可用' : '不可用' }}
                </span>
              </td>
              <td>
                <span v-if="model.is_active" class="badge badge--info">当前</span>
                <span v-else class="badge badge--muted">—</span>
              </td>
              <td class="mono small">{{ model.created_at }}</td>
              <td>
                <button
                  class="btn btn--sm"
                  type="button"
                  :disabled="!model.is_available || model.is_active"
                  @click="activate(model)"
                >
                  启用
                </button>
              </td>
            </tr>
            <tr v-if="!models.length">
              <td colspan="9" class="empty">
                还没有模型版本。可以先“启用内置基线”，或用 training/train.py 训练后再上传。
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <p v-if="models.some((item) => item.metrics)" class="small muted" style="margin-top: 8px">
        提示：模型卡中的评估指标（ADE/FDE/MAE）来自训练元数据，报告中需如实引用。
      </p>
    </section>
  </div>
</template>
