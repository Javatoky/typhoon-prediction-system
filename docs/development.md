# 开发约定与协作说明

本文件是给项目组三名开发同学（1 名前端、2 名后端）与组长（数据与模型）的共同约定，
保证分层边界、命名、错误处理与提交方式一致（NFR-009 可维护性）。

## 1 分支与提交

| 分支 | 用途 |
| --- | --- |
| `main` | 随时可运行、可演示的主分支（13.1：任何阶段都要保持主分支可运行） |
| `feat/backend-*` | 后端功能分支 |
| `feat/frontend-*` | 前端功能分支 |
| `feat/model-*` | 数据与模型分支 |
| `fix/*` | 缺陷修复 |

提交信息建议格式（中文即可）：

```text
<类型>(<范围>): <简短说明>

类型：feat / fix / docs / test / refactor / chore
范围：auth / prediction / upload / admin / model / frontend / docs
示例：feat(prediction): 支持指定模型版本重跑历史任务
```

只提交源码与配置示例，禁止提交：`backend/instance/`、`data/raw/`、`*.pt`、`*.sqlite`、`.env`。

## 2 后端分层规则（9.1 架构约束）

```text
api（控制层） → services（业务层） → models / ml
```

必须遵守：

1. **控制层只做权限与参数**：`app/api/*.py` 中不允许出现业务判断、SQL 查询或模型调用，
   统一调用 `app/services`。
2. **业务层负责事务与异常转换**：所有可预期失败都转换为 `app/core/errors.py` 中的 `AppError` 子类，
   不要把原始异常（含堆栈、路径）抛给浏览器（12.1）。
3. **数据访问集中在 ORM 实体与查询辅助**：不在控制层写 `db.session`；简单查询可放在服务层。
4. **不硬编码配置**：路径、限制、阈值统一放 `backend/config.py` 或 `.env`（NFR-009）。
5. **特征口径唯一来源是 `app/ml/schema.py`**：任何涉及“四个特征顺序、CSV 列名、时效、
   强度阈值”的代码都必须引用它，禁止复制常量。训练脚本也复用同一模块。

### 2.1 新增一个接口的步骤

1. 在 `app/services/` 实现业务函数（含校验、事务、异常）。
2. 在 `app/api/` 对应蓝图加路由，加 `@login_required` 或 `@admin_required`。
3. 参数解析统一使用 `app/core/params.py`，响应统一使用 `app/core/responses.py`。
4. 在 `backend/tests/` 增加用例（正常流程 + 至少一个失败/权限用例）。
5. 更新 `docs/api-reference.md`。

## 3 前端约定

1. 所有请求走 `src/api/client.js`，不要直接 `fetch`；错误对象统一是 `ApiError`
   （含 `code`、`message`、`details`），页面只需展示 `error.message`。
2. 状态放在 Pinia（`src/stores/`），页面组件不保存跨页共享数据。
3. 依赖后端的枚举、阈值、列名一律从 `/api/system/config` 读取，不在前端写死。
4. 权限只做体验优化（隐藏入口、路由守卫），真正的拒绝必须在后端完成（7.1）。
5. 样式使用 `src/styles/main.css` 中的变量与 `btn / card / badge / alert` 等类，
   保证 1366×768 下控件不重叠（NFR-011）。

## 4 数据与模型约定（组长）

1. 训练数据从 IBTrACS v4 的 WP 盆地获取，不要把范围截取成中国近海（3.1 样本量风险）。
2. 风速单位：IBTrACS 原始列为 knot，入库前换算成 m/s（`KNOT_TO_MPS`）。
3. 标准化器必须由训练集拟合并交付 `scaler.json`；Web 推理复用同一文件（11.3）。
4. 模型元数据必须包含：特征顺序、输入/输出长度、超参数、数据版本、指标与随机种子（11.5）。
5. 评估必须按台风划分独立测试集，并同时给出持续性基线指标（NFR-005、6.1）。
6. 若模型未优于基线，如实披露，不得通过调整测试集规避（11.5）。

## 5 数据库变更约定

课程阶段不引入 Alembic 等迁移工具，使用 `db.create_all()`。因此：

- **新增表 / 新增可空列**：直接改 `app/models/entities.py`，重新执行
  `python scripts/init_db.py`（会创建缺失的表）；已有表的列变更需要手工处理。
- **已有表的结构变更（改类型、加非空列、删列）**：开发期按以下流程处理，避免"悄悄丢数据"：

  1. 先备份：`copy backend\instance\typhoon.sqlite backend\instance\typhoon.backup.sqlite`
  2. 修改模型后执行 `python scripts/init_db.py --reset --seed`（会清空数据）
  3. 用 `python scripts/seed_demo_data.py` 或管理后台重新导入数据集

- 到第 5 阶段（部署验收）如果确实需要保留数据升级，再引入 `Alembic`，
  这属于 13.2 之外的范围，需在组内确认后再做。

## 6 测试约定

- 后端：`pytest`，放在 `backend/tests/`。夹具见 `tests/conftest.py`。
  - **注意**：`app` 夹具只在建表/清表时压入应用上下文，不要在 `yield` 期间保持长期上下文，
    否则测试客户端的会话行为会出现偏差（例如登出后会话未失效）。
  - 断言尽量使用错误码等 ASCII 文本，避免在 Windows GBK 控制台下看乱码。
- 前端：当前阶段以手测与走查为主；如需自动化可在后续引入 Vitest（不属本阶段基线）。
- 每次提交前至少跑一次 `pytest`；涉及前端构建的改动必须执行 `pnpm build` 确认无报错。

## 7 分工建议与推进顺序

按 13.1 的实施顺序（先打通软件闭环，再换模型）：

| 阶段 | 目标 | 主要负责人 | 本仓库已提供 |
| --- | --- | --- | --- |
| 第 1 步 | 数据闭环：IBTrACS 读取、筛选、可视化、CSV 格式 | 组长 + 后端A | `training/prepare_data.py`、数据集导入接口、历史台风页 |
| 第 2 步 | 基线预测：任务、结果、地图、评估 | 后端A + 前端 | `ml/models/baseline.py`、预测接口、工作台与任务详情页 |
| 第 3 步 | Transformer：替换基线，先验证张量与小样本 | 组长 | `ml/models/transformer.py`、`training/train.py`、`evaluate.py` |
| 第 4 步 | 账号与管理：登录、角色、用户管理、日志 | 后端B | `auth`/`admin` 蓝图、管理后台页面 |
| 第 5 步 | 部署验收：Waitress、Linux、SSH、并发与稳定性 | 组长 + 后端B | `scripts/run_server.py`、`docs/deployment.md` |

建议的接口冻结点：先按 `docs/api-reference.md` 冻结请求/响应结构，再由前端并行开发，
后端同学用 `backend/data/samples/valid-track.csv` 造数据自测。

## 8 日志与安全红线

1. 密码只保存哈希（`pbkdf2:sha256`），任何地方不得打印或返回明文（6.2）。
2. 登录失败提示必须统一文案，不暴露账号是否存在。
3. 上传文件必须使用随机文件名落盘，只允许 `.csv`；模型只允许 `.pt/.pth` 与 `.json`。
4. 管理接口必须在服务端再次校验角色，不能只依赖前端隐藏菜单。
5. 面向用户的错误信息不得包含服务器路径、SQL 语句或堆栈；
   失败任务只保存脱敏后的错误摘要（10.3）。

## 9 一键自检清单（提交前）

```bash
cd backend
pytest -q                                  # 全绿
python -m compileall -q app scripts training   # 语法检查

cd ../frontend
pnpm build                                   # 构建通过（产物在 backend/app/static/app）
```
