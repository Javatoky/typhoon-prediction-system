# Group 4: 基于Transformer模型的台风预测路径智能预报系统

## 需求分析与系统分析

- 软件需求分析说明书
  - markdown: [docs/software-requirements-specification.md](docs/software-requirements-specification.md)
  - docx: 详见此仓库 issues 中 `软件需求与系统分析`
- 系统分析说明书
  - markdown: [docs/system-analysis-specification.md](docs/system-analysis-specification.md)
  - docx: 详见此仓库 issues 中 `系统分析`

> 注：若找不见以上相关文档或者还缺任何文档，请联系项目组长。

## 快速开始（首次搭建）

```bash
# 1 后端：建虚拟环境并安装依赖
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1          # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

# 2 配置（改用随机 SECRET_KEY）
Copy-Item .env.example .env           # Linux/macOS: cp .env.example .env

# 3 建库 + 初始管理员 + 登记内置基线模型
python scripts/init_db.py --seed

# 4（可选）生成演示数据，便于前端联调
python scripts/seed_demo_data.py --count 3

# 5 启动后端（Waitress）
python scripts/run_server.py
```

```bash
# 6 前端：开发模式
cd frontend
pnpm install
pnpm dev            # http://localhost:5173，/api 自动代理到 127.0.0.1:5000

# 或构建为生产资源（产物输出到 backend/app/static/app，由 Flask 托管）
pnpm build           # 之后直接访问 http://127.0.0.1:5000/
```

默认管理员账号：`admin` / `Adm1n@2024`（`backend/.env` 可改，**密码不能包含用户名**）。首次登录后请立即修改密码。

## 文档索引

| 文档 | 内容 |
| --- | --- |
| [docs/deployment.md](docs/deployment.md) | 环境搭建、数据库初始化、模型上线、Linux + Waitress + SSH 部署、验收清单、常见问题 |
| [docs/project-structure.md](docs/project-structure.md) | 目录结构、与 9.1 分层架构的对应、需求→代码追踪表 |
| [docs/api-reference.md](docs/api-reference.md) | 全部对外接口的请求/响应约定、错误码、curl 示例 |
| [docs/development.md](docs/development.md) | 分支与提交规范、分层规则、数据库变更约定、分工与推进顺序 |

## 技术栈（依据需求文档 2.3）

| 层 | 选型 |
| --- | --- |
| 前端 | Vue 3 单页应用 + Vue Router + Pinia + Leaflet + Vite |
| 后端 | Python 3.10+ / Flask（REST API）、Waitress（WSGI 服务器） |
| 模型 | PyTorch（离线训练，Web 进程只加载权重推理）；未训练时降级为持续性基线 |
| 存储 | SQLite（业务数据）、CSV（轨迹交换）、PT（模型权重） |
| 部署 | Linux 虚拟环境 + SSH 端口转发（AT-12） |

## 当前进度

- [x] 需求分析（FR-001~FR-015、NFR-001~NFR-012、UC-01~UC-08）
- [x] 系统分析（分层架构、概念模型、算法分析、实施顺序）
- [x] 工程初始化：后端分层骨架 + 9 张数据表 + 全部接口与业务服务
- [x] 数据质量校验（DQ-01~DQ-05）与强度等级、Haversine 指标
- [x] Transformer 模型结构与离线训练/评估脚本
- [x] 前端骨架：登录、工作台、历史台风、任务、账号设置、管理后台
- [x] 测试与文档
- [ ] 导入真实 IBTrACS 数据并训练模型（待组长执行，见 docs/deployment.md 第 5 节）
- [ ] 第 5 阶段部署验收与并发/稳定性测试
