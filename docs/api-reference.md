# 对外接口约定（对应需求文档第 7 节）

所有接口前缀为 `/api`，请求与响应均为 JSON（CSV 上传为 `multipart/form-data`，导出为 `text/csv`）。
会话基于 Cookie（`HttpOnly`、`SameSite=Lax`），空闲 30 分钟失效。

## 1 统一响应格式

成功：

```json
{ "ok": true, "data": { }, "meta": { "page": 1, "page_size": 20, "total": 42, "pages": 3 } }
```

失败：

```json
{ "ok": false, "error": { "code": "validation_error", "message": "面向用户的中文提示", "details": { } } }
```

常见错误码：

| code | HTTP | 含义 |
| --- | --- | --- |
| `unauthorized` | 401 | 未登录或会话失效 |
| `session_expired` | 401 | 空闲超时（NFR-008） |
| `invalid_credentials` | 401 | 用户名或密码错误（统一文案，不区分账号是否存在） |
| `account_locked` | 401 | 连续失败锁定中（UC-01） |
| `account_disabled` | 401 | 账号被禁用 |
| `forbidden` | 403 | 已登录但无权限 |
| `not_found` | 404 | 资源不存在，或无权访问（按不存在处理） |
| `conflict` | 409 | 唯一性冲突，例如用户名重复 |
| `validation_error` | 422 | 参数或上传数据不合法，`details` 含行级明细 |
| `payload_too_large` | 413 | 超过 20 MB 上传限制（NFR-004） |
| `internal_error` | 500 | 服务器错误（不返回堆栈） |

## 2 认证

### POST /api/auth/login

请求：`{ "username": "admin", "password": "******" }`

响应 `data`：

```json
{
  "user": { "id": 1, "username": "admin", "role": "admin", "status": "active" },
  "model": { "label": "持续性预测基线 0.1.0", "kind": "rule_based_persistence", "degraded": true },
  "session_idle_minutes": 30
}
```

### POST /api/auth/logout

需登录。响应 `data.message` 为提示文案，同时清除会话。

### GET /api/auth/me

需登录。返回当前用户 `data.user`。

### POST /api/auth/change-password

需登录。请求 `{ "current_password", "new_password", "confirm_password"? }`。
密码规则：长度 ≥ 8，至少两类字符，不得包含用户名（失败返回 `validation_error`）。

## 3 历史台风（需登录）

### GET /api/typhoons

查询参数：`keyword`（名称或编号）、`season`、`season_from`、`season_to`、`basin`、
`focus_only`、`page`、`page_size`（≤100）。

响应为分页列表，元素结构：

```json
{
  "id": 12, "sid": "WP2020001", "name": "DEMO", "season": 2020, "basin": "WP",
  "start_time": "2020-08-01T00:00:00Z", "end_time": "2020-08-06T18:00:00Z",
  "max_wind_mps": 54.5, "min_pressure_hpa": 965.5, "track_point_count": 24,
  "in_focus_area": true
}
```

无匹配数据时返回空数组（不是错误，UC-02 扩展流程）。

### GET /api/typhoons/seasons

返回 `data.seasons: [2020, 2019, ...]`。

### GET /api/typhoons/{id}/track

响应 `data`：

```json
{
  "typhoon": { },
  "points": [
    { "iso_time": "2020-08-01T00:00:00Z", "lat": 15.9, "lon": 129.2,
      "max_wind_mps": 20.6, "central_pressure_hpa": 1000.0,
      "quality_flag": "original", "intensity_category": "TS",
      "intensity_code": "TS", "intensity_name": "热带风暴", "in_focus_area": true }
  ],
  "focus_segment": [ ],
  "point_count": 24, "missing_wind_count": 0, "missing_pressure_count": 0,
  "interpolated_count": 1
}
```

## 4 上传与校验（需登录）

### POST /api/uploads/track

`multipart/form-data`，字段名固定为 `file`。CSV 表头必须包含
`sid, iso_time, lat, lon, max_wind_mps, pressure_hpa`（大小写不敏感，支持常见别名与 IBTrACS 列名）。

成功（201）`data`：

```json
{
  "upload": { "id": 3, "label": "WP202601（5 点）", "row_count": 5, "valid_point_count": 5, "status": "validated" },
  "validation": {
    "status": "pass", "ok": true, "row_count": 5, "valid_point_count": 5,
    "rejected_row_count": 0, "interpolated_count": 0, "lon_converted": false,
    "error_count": 0, "warning_count": 0, "errors": [], "warnings": [], "infos": []
  }
}
```

校验失败返回 `422`，`error.details.validation.errors` 中每条包含
`row`（Excel 行号，表头为第 1 行）、`column`、`code`、`message`，可直接展示给用户。

### POST /api/uploads/track/validate

同样的上传方式，只校验不保存（`data.saved = false`），用于提交前预览质量报告。

### GET /api/uploads

分页返回当前用户的上传记录；`GET /api/uploads/{id}` 附带全部观测点。

## 5 预测任务（需登录）

### POST /api/predictions

请求（两种来源二选一）：

```json
{ "typhoon_id": 12 }
{ "upload_id": 3, "model_version_id": 5 }
```

响应（201）`data` 为任务详情：

```json
{
  "id": 8, "task_code": "T20250901093012-A1B2C3", "status": "succeeded",
  "input_source": "typhoon", "typhoon_sid": "WP2020001", "typhoon_name": "DEMO",
  "model_version_id": 1, "model_label": "持续性预测基线 0.1.0",
  "data_version": "test", "duration_ms": 12,
  "input_window": { "feature_order": ["lat","lon","max_wind_mps","central_pressure_hpa"],
                    "window": [[18.2,126.5,28.0,985.0], "..."] },
  "points": [
    { "forecast_hour": 6, "valid_time": "2020-08-07T00:00:00Z",
      "lat": 21.1, "lon": 123.0, "max_wind_mps": 37.0, "central_pressure_hpa": 965.0,
      "intensity_category": "TY", "intensity_name": "台风", "is_key_hour": true,
      "quality_flag": "predicted", "actual": null, "error_km": null }
  ],
  "comparison": {
    "matched_points": 6,
    "distance_km": { "6": 42.1, "12": 88.5 },
    "ade_km": 120.4, "wind_mae_mps": 3.2, "pressure_mae_hpa": 4.5
  },
  "intensity_thresholds": [ ]
}
```

失败时任务仍然落库（`status = "failed"`、`error_summary` 为脱敏摘要），接口返回 4xx/5xx 统一错误。

### GET /api/predictions

查询参数：`status`、`page`、`page_size`、`scope=all`（仅管理员可看全部）。
普通用户永远只能看到自己的任务（FR-010、权限要求）。

### GET /api/predictions/{id}

任务详情，非所有者访问返回 404（不泄露存在性）。

### GET /api/predictions/{id}/export

返回 `text/csv`（UTF-8 BOM，Excel 双击不乱码），字段：

```text
task_code, source, model, data_version, forecast_hour, valid_time, lat, lon,
max_wind_mps, pressure_hpa, intensity_category, intensity_name,
actual_lat, actual_lon, actual_wind_mps, actual_pressure_hpa, error_km, quality_flag
```

文件头部包含 `#` 注释行记录任务编号、来源、模型版本、输入窗口与免责声明。

### GET /api/predictions/statistics · /api/predictions/status-choices

工作台与筛选控件所需的统计与枚举。

## 6 管理员接口（需 `admin` 角色）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/admin/users` | 用户列表（`keyword`、分页） |
| POST | `/api/admin/users` | 创建用户；未传 `password` 时返回一次性初始密码 |
| PUT | `/api/admin/users/{id}` | 修改状态/角色/显示名；拒绝禁用最后一个管理员 |
| POST | `/api/admin/users/{id}/reset-password` | 重置密码，返回一次性新密码 |
| GET | `/api/admin/datasets` | 数据集导入记录 |
| POST | `/api/admin/datasets/import` | 导入 CSV；表单字段 `file`、`source`、`source_version`、`strict` |
| GET | `/api/admin/models` | 模型版本列表 |
| POST | `/api/admin/models` | 上传模型；表单字段 `weights`(.pt)、`metadata`(.json)、`scaler`(.json)、`name`、`version`、`activate` |
| PUT | `/api/admin/models/{id}/activate` | 启用模型（试加载失败不替换当前模型） |
| POST | `/api/admin/models/baseline` | 登记并启用内置持续性基线 |
| GET | `/api/admin/models/runtime-status` | 当前推理器状态与最近版本 |
| GET | `/api/admin/logs` | 操作日志（`action`、`actor`、`result`、分页） |
| GET | `/api/admin/logs/actions` | 动作枚举与中文标签 |
| GET | `/api/admin/overview` | 管理概览统计 |

## 7 系统接口

| 方法 | 路径 | 权限 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/system/health` | 公开 | 健康检查，供部署与稳定性测试 |
| GET | `/api/system/config` | 登录 | 特征口径、时效、强度阈值、上传限制、免责声明（前端据此渲染，避免硬编码） |
| GET | `/api/system/model-status` | 登录 | 当前推理器状态 |

## 8 命令行等价示例

```bash
# 登录并保存会话 Cookie
curl -c cookies.txt -X POST http://127.0.0.1:5000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"Adm1n@2024"}'

# 上传并校验 CSV
curl -b cookies.txt -X POST http://127.0.0.1:5000/api/uploads/track \
  -F "file=@../data/samples/valid-track.csv"

# 用历史台风创建预测任务
curl -b cookies.txt -X POST http://127.0.0.1:5000/api/predictions \
  -H "Content-Type: application/json" -d '{"typhoon_id":1}'

# 导出结果
curl -b cookies.txt -o result.csv http://127.0.0.1:5000/api/predictions/1/export
```
