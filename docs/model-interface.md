# 数据 / 模型侧接口约定（后端 ↔ 模型）

本文件约定**后端业务代码**与**数据 / 模型侧**之间的边界：哪些已冻结、各自负责什么、模型侧以后怎么改都不会影响后端。
对外 HTTP 接口仍以 [api-reference.md](api-reference.md) 为准（由后端负责人维护）；本文件只管 HTTP 之下、模型之上的那一层。

- 状态：**冻结**（2026-11-06）
- 起草：AI（数据与模型线执行），组长确认
- 变更规则：见第 6 节

---

## 0 一句话

> **后端不需要等模型。** 系统已有"持续性预测基线"占位，它与将来的 Transformer 走**完全相同**的接口；
> 模型训练好后由管理员在后台上传三个文件即可替换，后端代码不改一行。

---

## 1 职责边界

```text
api/ ──→ services/ ──→ app/ml/registry.py ──→ app/ml/forecaster.py ──→ 模型（基线 / Transformer）
 └──────── 后端负责 ────────┘ └──────────────── 数据 / 模型侧负责 ────────────────┘
```

| 归属 | 目录 / 文件 | 说明 |
| --- | --- | --- |
| 后端 | `app/api/`、`app/services/`、`app/models/` | 只通过第 2、3 节的接口使用模型 |
| 模型侧 | `app/ml/`（`schema.py` 除外，见下）、`training/`、`scripts/` 中的数据脚本 | 内部实现可以随时改，只要第 2~4 节不变 |
| 共同 | `app/ml/schema.py` | 特征顺序、窗口长度、量程等全系统常量；**改动需双方同意** |

后端**不得**自行实现：标准化 / 反标准化、经度口径换算、模型前向计算、风速单位换算以外的特征处理。
这些都在模型侧内部完成，后端拿到的永远是物理单位的结果。

---

## 2 接口 I-1：推理调用（冻结）

```python
from app.ml.registry import get_registry

forecast = get_registry().current.predict(window)
```

### 输入 `window`

| 项 | 约定 |
| --- | --- |
| 形状 | 5 行 × 4 列（`schema.INPUT_LENGTH × schema.FEATURE_DIM`） |
| 列顺序 | `lat, lon, max_wind_mps, central_pressure_hpa`（`schema.FEATURE_COLUMNS`） |
| 单位 | 度 / 度 / m/s / hPa（物理单位，**不要**预先标准化） |
| 经度口径 | −180 ~ 180（与数据库 `TrackPoint` 一致） |
| 时间 | 按时间升序，相邻点间隔 6 小时；**连续性由调用方保证**（DQ-04 校验在上传 / 选取窗口时完成） |

### 输出 `ForecastResult`

| 字段 | 类型 | 约定 |
| --- | --- | --- |
| `points` | `list[list[float]]`，12 × 4 | 列顺序、单位同输入；经度 −180 ~ 180；已做物理量程修正 |
| `forecast_hours` | `list[int]` | `[6, 12, …, 72]`，与 `points` 一一对应 |
| `model_label` | `str` | 展示用，如 `持续性预测基线 0.1.0` |
| `model_kind` | `str` | `rule_based_persistence`（基线）或 `transformer` |
| `notes` | `list[str]` | 给用户看的提示（如"当前为基线"），可直接展示 |
| `to_rows()` | 方法 | 返回 `[{"forecast_hour": 6, "lat": …, "lon": …, "max_wind_mps": …, "central_pressure_hpa": …}, …]` |

### 异常

| 情况 | 抛出 | 后端处理 |
| --- | --- | --- |
| 窗口长度 / 列数不对 | `ValueError` | 视为输入错误，任务标记失败并保存脱敏摘要 |
| 模型输出含非有限值 | `ValueError` | 同上 |

---

## 3 接口 I-2：运行状态（冻结）

`get_registry().status()` 返回字典，后端可直接透传给前端：

| 键 | 说明 |
| --- | --- |
| `label` / `kind` | 同 `ForecastResult` |
| `model_version_id` | 对应数据库 `ModelVersion.id`；基线未登记时为 `None` |
| `input_length` / `output_length` / `feature_order` | 5 / 12 / 四特征顺序 |
| `training_data_version` | 训练数据版本，如 `ibtracs-v04r01-wp-2000plus-synoptic` |
| `scaler` | 标准化器参数（调试用，前端不必展示） |
| `torch_available` | 服务器是否装了 PyTorch |
| `source` | `database` / `baseline` / `test` |
| `load_error` | 最近一次加载失败的原因，无则 `None` |
| `degraded` | `True` 表示当前在用基线 |

切换模型：管理员启用某版本后调用 `get_registry().load_active()`；**试加载失败不会替换当前模型**（已实现）。

---

## 4 接口 I-3：模型交付物（冻结）

模型侧交付三个文件，管理员在「管理后台 → 模型管理」上传（`POST /api/admin/models`）：

| 表单字段 | 文件 | 必需 | 内容 |
| --- | --- | --- | --- |
| `weights` | `best.pt` | 是 | PyTorch 权重（state_dict 或其包装字典） |
| `metadata` | `metadata.json` | 是 | 见下表 |
| `scaler` | `scaler.json` | 建议 | 至少含长度为 4 的 `mean`、`std` 数组 |

### `metadata.json` 字段

| 字段 | 必需 | 后端是否解析 | 说明 |
| --- | --- | --- | --- |
| `name` / `version` | 否（缺省用上传表单值） | 是 | 展示与版本管理 |
| `kind` | 否（默认 `transformer`） | 是 | |
| `feature_order` | 否 | 是（校验顺序） | 必须等于四特征顺序 |
| `hyperparams` | 是 | **只读其中 3 个维度字段** | `feature_dim=4`、`input_length=5`、`output_length=12` 由后端校验；**其余字段后端原样保存、原样交回模型侧，不得解析或依赖** |
| `training_data_version` | 建议 | 是（展示） | |
| `metrics` | 建议 | 是（展示） | 测试集指标，含基线对比 |

### 扩展点（模型侧保留，后端无需任何改动）

以后模型侧的改动（例如经度口径、预测方式、网络结构）**全部写在 `hyperparams` 里**，由 `app/ml/forecaster.py` 读取：

| `hyperparams` 键 | 取值 | 缺省（向后兼容） | 用途 |
| --- | --- | --- | --- |
| `arch` | `transformer_encoder` 等 | `transformer_encoder` | 网络结构 |
| `lon_convention` | `-180_180` / `0_360` | `-180_180` | 模型内部使用的经度口径（数据卡片 Q-01） |
| `target_mode` | `absolute` / `delta` | `absolute` | 模型输出绝对位置还是相对上一时刻的位移 |

`scaler.json` 允许包含 `mean` / `std` 以外的键（例如位移目标的标准化参数），后端只校验 `mean` / `std`，**不得拒绝额外的键**。

无论这些字段怎么取值，第 2 节的输入输出**始终是物理单位、经度 −180 ~ 180**。

---

## 5 接口 I-4：数据交换（已冻结，沿用现有约定）

| 项 | 约定 | 依据 |
| --- | --- | --- |
| 上传 / 导出 CSV 列 | `sid, iso_time, lat, lon, max_wind_mps, pressure_hpa` | 需求文档附录 A、`schema.REQUIRED_CSV_COLUMNS` |
| 数据库轨迹点口径 | 经度 −180 ~ 180、风速 m/s、气压 hPa | `docs/data-card.md` D-01 / D-07 |
| IBTrACS 导入口径 | 由 `dataset_service` 实现，口径以数据卡片为准 | `docs/data-card.md` |

---

## 6 变更规则

| 改动 | 是否需要后端参与 |
| --- | --- |
| 模型内部：经度口径、预测位移、换网络结构、重新训练、新标准化方式 | **不需要**，只要第 2~5 节不变；通过 `hyperparams` 扩展 |
| 第 2~5 节任何一项（字段、形状、单位、经度口径、异常类型） | 需要：双方同意，先改本文件，再改代码 |
| `app/ml/schema.py` 常量 | 需要：双方同意 |

新增扩展字段时，模型侧必须保证**缺省值等于现有行为**，旧模型文件继续可用。

---

## 7 后端现在就能做的事

1. **用基线联调**：`python scripts/init_db.py --seed` 会登记并启用内置基线，预测任务全流程立即可用，结果格式与真模型一致。
2. **写测试**：现有测试默认走基线，不依赖 PyTorch 与权重文件；需要固定输出时，可用
   `get_registry(app).set_forecaster(Forecaster(...), source="test")` 注入自定义推理器。
3. **模型管理页**：上传 / 启用 / 回退流程已可测；没有真权重时，可用 `POST /api/admin/models/baseline`。

---

## 8 模型侧待办（内部，不影响以上接口）

- [ ] Q-01：模型内部改用 `0_360` 经度，并修正推理端相邻点 ±8° 经度限制在跨 180° 时的误判（`app/ml/preprocess.py`）
- [ ] 支持 `hyperparams.lon_convention` / `target_mode`（缺省保持现有行为）
- [ ] 训练首个可用模型，交付 `best.pt` + `metadata.json` + `scaler.json`，附测试集与基线对比
