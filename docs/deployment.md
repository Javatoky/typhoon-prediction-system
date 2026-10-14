# 部署与运行指南

面向本课程组的三类角色（数据与模型、后端、前端）与验收场景 AT-12（Linux 虚拟环境 + Waitress + SSH 隧道）。

## 1 环境要求

| 项目 | 版本 | 说明 |
| --- | --- | --- |
| Python | 3.10 及以上（推荐 3.11） | 后端与训练脚本 |
| Node.js | 18 及以上（推荐 20） | 前端构建 |
| pnpm | 8 及以上 | 前端包管理（也可以用 npm，命令自行替换） |
| 浏览器 | Chrome / Edge 当前主流版本 | 分辨率不低于 1366×768（NFR-011） |
| 可选 | PyTorch 2.2+ | 仅训练与加载 `.pt` 权重时需要 |

> 未安装 PyTorch 也能完整跑通系统：模型层会自动降级为内置“持续性预测基线”（见第 5 节）。

## 2 后端首次搭建

以下命令均在 `backend/` 目录执行。

### 2.1 创建虚拟环境并安装依赖

Windows（PowerShell）：

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt -r requirements-dev.txt
```

Linux / macOS：

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt -r requirements-dev.txt
```

需要训练或加载 Transformer 权重时，再追加：

```bash
pip install -r requirements-ml.txt
# 有 NVIDIA GPU 时先按官方指引安装对应 CUDA 版 torch
```

### 2.2 准备配置文件

```bash
cp .env.example .env      # Windows: Copy-Item .env.example .env
```

至少需要修改 `SECRET_KEY`：

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

把输出填入 `.env` 的 `SECRET_KEY`。`backend/.env` 已在 `.gitignore` 中，禁止提交真实密钥。

### 2.3 建库、创建管理员、登记内置基线模型

```bash
python scripts/init_db.py --seed
```

输出示例：

```text
建表完成：sqlite:///.../backend/instance/typhoon.sqlite
已创建管理员账号：admin（密码：来自参数/.env）
已登记并启用内置模型：持续性预测基线 0.1.0
```

如需自定义管理员口令（**注意：密码不能包含用户名**）：

```bash
python scripts/init_db.py --seed --admin-user admin --admin-password 'Adm1n@2024'
```

### 2.4 准备演示数据（可选，便于前端联调）

```bash
python scripts/seed_demo_data.py --count 3
```

或生成一份标准格式 CSV 供手工上传测试：

```bash
python scripts/make_sample_csv.py            # data/samples/valid-track.csv
python scripts/make_sample_csv.py --invalid  # 故意包含各类校验错误
```

### 2.5 启动后端

```bash
python scripts/run_server.py            # Waitress，默认 127.0.0.1:5000
python scripts/run_server.py --dev      # Flask 调试服务器（仅本机开发）
```

验证：

```bash
curl http://127.0.0.1:5000/api/system/health
```

## 3 前端首次搭建

在 `frontend/` 目录执行：

```bash
pnpm install
pnpm dev        # http://localhost:5173，/api 自动代理到 127.0.0.1:5000
```

开发期同学请使用 `pnpm dev`，它带有热更新且不需要每次构建。

打包为生产资源（产物输出到 `backend/app/static/app/`，由 Flask 直接托管）：

```bash
pnpm build
```

构建后直接访问后端根路径 `http://127.0.0.1:5000/` 即可打开页面（前后端同源，无跨域问题）。

## 4 日常运行流程

```bash
# 终端 1：后端
cd backend && .\.venv\Scripts\Activate.ps1 && python scripts/run_server.py

# 终端 2：前端（开发模式）
cd frontend && pnpm dev
```

浏览器打开 `http://localhost:5173`，用管理员账号登录，先到「管理后台 → 数据集管理」导入历史数据，再到「预测工作台」创建任务。

## 5 模型上线流程（组长 / 数据与模型负责人）

1. 下载 IBTrACS v4 的 WP 盆地子集，放到 `data/raw/`，例如 `ibtracs.WP.list.v04r01.csv`。

   ```bash
   cd backend
   python training/prepare_data.py --input ../data/raw/ibtracs.WP.list.v04r01.csv
   ```

   产物：`data/processed/dataset.npz`、`data/processed/scaler.json`、`data/processed/dataset_meta.json`。

2. 训练（CPU 即可，建议先跑通再调参）：

   ```bash
   python training/train.py --epochs 60
   ```

   产物：`data/models/best.pt`、`data/models/last.pt`、`data/models/metadata.json`。

3. 评估并与持续性基线对比：

   ```bash
   python training/evaluate.py --split test
   ```

   产物：`data/models/metrics_test.json`（含 6/12/24/48/72 小时误差、ADE、FDE、MAE、强度准确率与基线对比）。

4. 在「管理后台 → 模型管理」上传 `best.pt`（字段名 `weights`）与 `metadata.json`（字段名 `metadata`），
   勾选“上传后立即设为当前模型”。系统会先试加载并校验 4 特征 / 5 输入 / 12 输出维度，失败则拒绝启用且不影响旧模型。

5. 若暂时没有训练成果，可在同一页面点「启用内置基线」，使系统先完成功能闭环（13.1 第 2 步）。

## 6 Linux 服务器部署（AT-12 / NFR-010）

```bash
# 1) 上传代码后创建虚拟环境
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 2) 配置 .env（SECRET_KEY、HOST=127.0.0.1、PORT=5000）
# 3) 初始化
python scripts/init_db.py --seed

# 4) 构建前端（若服务器上没有 Node，可在本地构建后把 backend/app/static/app 一起上传）
cd ../frontend && pnpm install && pnpm build && cd ../backend

# 5) 以 Waitress 启动（不依赖 root 权限）
python scripts/run_server.py --host 127.0.0.1 --port 5000 --threads 4
```

客户端本地建立 SSH 端口转发后访问：

```bash
ssh -L 5000:127.0.0.1:5000 用户名@服务器地址
# 浏览器打开 http://127.0.0.1:5000
```

长期运行建议交给进程守护（例如 `nohup ... &`、`screen`/`tmux`，或系统管理员提供的服务管理），
本课程不引入 Docker 与容器编排（13.1 明确的迭代边界）。

## 7 测试与自检

```bash
cd backend
pytest                                   # 全部测试
pytest tests/test_validator.py -v        # 只跑数据质量校验
```

测试覆盖：DQ-01~DQ-05 校验规则、强度等级阈值、Haversine 指标、认证与锁定、权限隔离、
CSV 上传与导出、数据集导入与整批回滚、模型前向（安装 PyTorch 时）。

## 8 常见问题

| 现象 | 原因与处理 |
| --- | --- |
| 登录页提示“用户名或密码不正确” | 账号需由管理员创建；确认 `init_db.py --seed` 已执行且使用 `.env` 中的口令 |
| 预测结果始终不变 | 当前模型是持续性基线（未上传 Transformer 权重）。控制台与「管理概览」会显示“基线降级” |
| 上传报“缺少必需列” | 表头必须是 `sid, iso_time, lat, lon, max_wind_mps, pressure_hpa`（大小写不敏感，支持常见别名） |
| 上传报“需要连续 5 个有效观测点” | 错误详情会指出缺失行号；补齐 6 小时间隔的观测点后重试 |
| 地图空白 | 无网络时底图瓦片不可用，系统自动降级为经纬网 + 轨迹显示（仍满足 2.3 地图约束） |
| `flask db` 之类命令不存在 | 本工程按课程约束不使用迁移工具，表结构由 `db.create_all()` 创建，变更方式见 `docs/development.md` |
| 前端 404 / 白屏 | 未执行 `pnpm build`；开发期请直接用 `pnpm dev` 的 5173 端口 |
| 中文日志乱码 | 已在 `configure_logging` 中把控制台编码改为 UTF-8；若仍有问题请设置 `PYTHONIOENCODING=utf-8` |

## 9 验收前检查清单

- [ ] `pytest` 全绿
- [ ] `python scripts/init_db.py --seed` 可重复执行不报错
- [ ] 管理员登录 → 创建普通用户 → 普通用户改密 → 重新登录（AT-01）
- [ ] 导入一份数据集并在工作台查询到历史台风（AT-02）
- [ ] 上传合法与非法 CSV，分别成功与给出行级错误（AT-03）
- [ ] 用 5 个观测点生成 12 个预测点，导出 CSV 字段与页面一致（AT-04/08）
- [ ] 地图、表格、误差对比数据一致（AT-05/06）
- [ ] 普通用户无法调用 `/api/admin/*`（AT-07/09）
- [ ] 无效模型无法替换当前模型，切换后新任务记录新版本（AT-10）
- [ ] `pnpm build` 后由 Flask 单进程可访问全部页面（AT-12）
