# 数据与模型产物目录

本目录保存原始数据、训练中间产物与模型权重，均**不纳入版本库**（见根目录 `.gitignore`）。

```text
data/
├── raw/         # IBTrACS v4 原始 CSV
├── processed/   # 训练样本与标准化器
├── models/      # 训练输出（best.pt / last.pt / metadata.json）
└── samples/     # 演示与测试用 CSV（脚本生成，可提交）
```

## 1 获取 IBTrACS 数据（raw/）

从 NOAA/NCEI 下载 IBTrACS v4 的西北太平洋子集，建议只取 WP 盆地（3.1：训练用整个 WP 盆地，
避免只截取中国近海造成样本过少）：

- 数据主页：<https://www.ncei.noaa.gov/products/international-best-track-archive>
- 文件命名示例：`ibtracs.WP.list.v04r01.csv`

把文件放到 `data/raw/`，例如：

```text
data/raw/ibtracs.WP.list.v04r01.csv
```

关键列（本项目 `app/ml/schema.py` 会做列名归一化）：
`SID`、`ISO_TIME`、`LAT`、`LON`、`USA_WIND`（knot）、`USA_PRES`（hPa）、`NAME`、`BASIN`、`SEASON`。

> 关键口径提醒：`USA_WIND` 单位是 **knot**，系统会按 `0.514444` 换算为 m/s 并保留来源与版本，
> 报告附录必须注明该换算规则（2.4 假设与依赖）。

## 2 生成训练样本（processed/）

```bash
cd backend
python training/prepare_data.py --input ../data/raw/ibtracs.WP.list.v04r01.csv
```

产物：

| 文件 | 内容 |
| --- | --- |
| `dataset.npz` | `x_train/y_train/x_val/y_val/x_test/y_test`，形状 `(N, 5, 4)` 与 `(N, 12, 4)` |
| `scaler.json` | 四特征均值/标准差（由训练集拟合，Web 推理复用之，11.3） |
| `dataset_meta.json` | 数据统计、清洗行数、按 SID 划分的三个集合清单（可复现） |

特性顺序固定为 `lat, lon, max_wind_mps, central_pressure_hpa`，不得调整。

## 3 训练与评估（models/）

```bash
python training/train.py --epochs 60
python training/evaluate.py --split test
```

产物：

| 文件 | 内容 |
| --- | --- |
| `best.pt` | 验证集最优权重（上传到 Web 系统时使用） |
| `last.pt` | 最后一轮权重（断点续训参考） |
| `metadata.json` | 超参数、特征顺序、标准化参数、训练历史、最佳轮次、基线对比 |
| `metrics_test.json` | 独立测试集分时效误差、ADE/FDE、风速与气压 MAE、强度准确率 |

## 4 演示与测试样本（samples/）

```bash
python scripts/seed_demo_data.py --count 3      # 生成并导入 3 个演示台风
python scripts/make_sample_csv.py               # 合法样本（附录A 格式）
python scripts/make_sample_csv.py --invalid     # 故意包含 DQ-01~DQ-04 错误
python scripts/make_sample_csv.py --big-mb 21   # 超过 20MB，用于 NFR-004 边界测试
```

## 5 为什么不入库

- 原始 IBTrACS 文件体积大（WP 子集数十 MB 以上），超出仓库合理容量。
- 模型权重与训练产物可由脚本重新生成，且需要连同元数据一起交付（11.5）。
- 上传的用户数据属于运行期数据，保存在 `backend/instance/uploads/`，同样不入库。
