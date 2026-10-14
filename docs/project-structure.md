# 项目结构与分层对照

本文件说明工程目录如何对应《软件需求分析说明书》与《系统分析说明书》中的架构设计（9.1 分层架构、10.2 数据实体、11 算法模块）。

## 1 目录总览

```text
jk244-group04/
├── README.md
├── docs/                                  # 课程文档与本工程的开发文档
│   ├── software-requirements-specification.md   # 需求分析说明书（已有）
│   ├── system-analysis-specification.md         # 系统分析说明书（已有）
│   ├── project-structure.md                     # 本文件
│   ├── deployment.md                            # 环境搭建、初始化、部署与验收命令
│   ├── api-reference.md                         # 对外接口约定
│   └── development.md                           # 开发约定、分支规范、分工建议
├── backend/                               # Flask 后端（Python 3.10+）
│   ├── app/
│   │   ├── __init__.py                    # create_app 工厂
│   │   ├── extensions.py                  # SQLAlchemy 实例
│   │   ├── api/                           # 控制层（Controller）
│   │   ├── services/                      # 业务层（Service）
│   │   ├── models/entities.py             # 数据访问层（ORM 实体）
│   │   ├── ml/                            # 模型层（预处理 + 推理）
│   │   ├── core/                          # 错误、响应、日志、参数、安全
│   │   ├── utils/                         # 单位、强度等级、指标、时间（纯函数）
│   │   └── static/app/                    # 前端构建产物（pnpm build 输出）
│   ├── training/                          # 离线训练管线（与 Web 进程解耦）
│   ├── scripts/                           # 可执行前置脚本
│   ├── tests/                             # pytest 测试
│   ├── config.py                          # 集中配置（环境变量 / .env）
│   ├── requirements.txt                   # 运行期依赖
│   ├── requirements-ml.txt                # PyTorch（可选）
│   ├── requirements-dev.txt               # pytest
│   └── .env.example
├── frontend/                              # Vue 3 单页应用
│   ├── src/{api,router,stores,layouts,components,views,styles}
│   ├── vite.config.js                     # /api 代理 + 构建输出到后端静态目录
│   └── package.json
└── data/                                  # 数据与模型产物（不入版本库）
    ├── raw/                               # IBTrACS 原始 CSV
    ├── processed/                         # dataset.npz / scaler.json / dataset_meta.json
    ├── models/                            # best.pt / last.pt / metadata.json
    └── samples/                           # 演示与测试用 CSV
```

## 2 与分层架构的对应

| 分析文档中的层 | 代码位置 | 说明 |
| --- | --- | --- |
| 浏览器端（Vue 单页应用） | `frontend/src` | 页面交互、Leaflet 轨迹绘制、表格与曲线展示 |
| 控制层 | `backend/app/api` | 只做权限校验与参数解析，不写业务逻辑（12.1） |
| 业务层 | `backend/app/services` | 任务编排、事务边界、异常转统一业务错误 |
| 数据访问层 | `backend/app/models/entities.py` | 9 张表，见下方实体对照 |
| 模型层 | `backend/app/ml` | 预处理器 + 模型封装 + 注册表（热加载） |
| 横切关注点 | `backend/app/core`、`backend/app/utils` | 错误码、统一响应、日志、参数、安全、单位与指标 |
| 离线训练 | `backend/training` | 与 Web 进程解耦，产物为 PT + scaler + metadata |

依赖方向是单向的：`api → services → models / ml`，`core`、`utils` 可被任意层引用，但 `utils` 与 `ml/schema.py` 不依赖 Flask，便于训练脚本复用同一口径。

## 3 数据实体对照（10.2）

| 文档实体 | 代码类 | 表名 | 说明 |
| --- | --- | --- | --- |
| User | `User` | `users` | 账号、密码哈希、角色 `user/admin`、状态 `active/disabled/locked` |
| （补充） | `LoginAttempt` | `login_attempts` | 登录尝试记录，支撑 UC-01 的连续失败锁定 |
| Typhoon | `Typhoon` | `typhoons` | 台风基本信息与资料来源、是否进入关注区 |
| TrackPoint | `TrackPoint` | `track_points` | 实测轨迹点，含 `quality_flag`（original/interpolated） |
| （补充） | `DatasetImport` | `dataset_imports` | 数据集导入批次，支撑 FR-013 与整批回滚 |
| ModelVersion | `ModelVersion` | `model_versions` | 权重、版本、输入输出维度、唯一启用状态 |
| PredictionTask | `PredictionTask` | `prediction_tasks` | 发起者、输入快照、模型版本、状态、耗时 |
| PredictionPoint | `PredictionPoint` | `prediction_points` | 12 个时效点，含强度等级与实测对比误差 |
| （补充） | `Upload` / `UploadPoint` | `uploads` / `upload_points` | 用户上传的输入数据与质量报告 |
| AuditLog | `AuditLog` | `audit_logs` | 登录、导入、模型切换、用户管理等操作 |

## 4 需求 → 代码追踪

| 需求编号 | 主要实现位置 |
| --- | --- |
| FR-001 身份认证 | `api/auth.py`、`services/auth_service.py`、`core/security.py` |
| FR-002 退出与改密 | `api/auth.py`（`/logout`、`/change-password`） |
| FR-003 历史台风查询 | `api/typhoons.py`、`services/typhoon_service.py` |
| FR-004/005 CSV 上传与校验 | `api/uploads.py`、`services/upload_service.py`、`ml/validator.py` |
| FR-006/007 创建预测与推理 | `api/predictions.py`、`services/prediction_service.py`、`ml/forecaster.py` |
| FR-008/009 展示与对比 | `components/TrackMap.vue`、`ForecastTable.vue`、`prediction_service.compare_with_actual` |
| FR-010 任务历史 | `api/predictions.py`（列表）、`views/TaskListView.vue` |
| FR-011 结果导出 | `services/prediction_service.build_export_csv`、`views/...`（下载） |
| FR-012 用户管理 | `api/admin.py`、`services/user_service.py` |
| FR-013 数据集管理 | `api/admin.py`、`services/dataset_service.py` |
| FR-014 模型管理 | `api/admin.py`、`services/model_service.py`、`ml/registry.py` |
| FR-015 操作日志 | `services/audit_service.py`、`api/admin.py`（`/logs`） |
| NFR-009 分层与配置 | 本文件的第 2 节、`backend/config.py` |
| NFR-010 可移植性 | `config.py`（路径可配置）、`scripts/run_server.py`（Waitress） |

## 5 前端页面结构

| 路由 | 视图 | 关联需求 |
| --- | --- | --- |
| `/login` | `LoginView.vue` | FR-001 |
| `/` | `WorkspaceView.vue` | FR-003~FR-009、FR-011 |
| `/typhoons` | `TyphoonHistoryView.vue` | FR-003 |
| `/tasks` | `TaskListView.vue` | FR-010 |
| `/tasks/:id` | `TaskDetailView.vue` | FR-008/009/011 |
| `/profile` | `ProfileView.vue` | FR-002 |
| `/admin/overview` | `AdminOverviewView.vue` | FR-015 |
| `/admin/users` | `UserManageView.vue` | FR-012 |
| `/admin/datasets` | `DatasetManageView.vue` | FR-013 |
| `/admin/models` | `ModelManageView.vue` | FR-014 |
| `/admin/logs` | `AuditLogView.vue` | FR-015 |
