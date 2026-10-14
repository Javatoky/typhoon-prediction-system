"""标准化预处理器（11.3 / 11.6）。

职责：
- 用训练集统计量（均值/标准差）标准化四个特征，验证、测试与 Web 推理复用同一标准化器。
- 提供反标准化与物理范围修正，保证输出不出现越界值。

scaler 文件格式（JSON，可被训练脚本与 Web 进程共同读取）：
    {
      "feature_columns": ["lat", "lon", "max_wind_mps", "central_pressure_hpa"],
      "mean": [...], "std": [...],
      "version": "1.0", "created_at": "...", "fixed_range": false
    }
当 fixed_range 为 true 时使用物理量程做 min-max 标准化（无训练集统计量时的兜底方案）。
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from app.ml import schema
from app.utils.units import normalize_lat, normalize_lon

SCALER_VERSION = "1.0"

# 物理修正使用的量程（11.6：反标准化后进行物理范围修正）
DEFAULT_PHYSICAL_RANGE: dict[str, tuple[float, float]] = {
    "lat": (schema.LAT_RANGE[0], schema.LAT_RANGE[1]),
    "lon": (schema.LON_RANGE[0], schema.LON_RANGE[1]),
    "max_wind_mps": schema.WIND_RANGE_MPS,
    "central_pressure_hpa": schema.PRESSURE_RANGE_HPA,
}

_EPS = 1e-6


@dataclass
class FeatureScaler:
    """四特征标准化器。"""

    feature_columns: list[str] = field(default_factory=lambda: list(schema.FEATURE_COLUMNS))
    mean: list[float] = field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    std: list[float] = field(default_factory=lambda: [1.0, 1.0, 1.0, 1.0])
    version: str = SCALER_VERSION
    created_at: str | None = None
    fixed_range: bool = False
    fitted_sample_count: int = 0

    # ------------------------------------------------------------------
    # 构造
    # ------------------------------------------------------------------
    @classmethod
    def from_numpy(cls, array, *, version: str = SCALER_VERSION) -> "FeatureScaler":
        """从 (N, 4) 数组统计均值与标准差（训练集专用）。"""
        count = int(array.shape[0])
        if count == 0:
            raise ValueError("无法用空数据集拟合标准化器。")
        mean = [float(value) for value in array.mean(axis=0)]
        std = [float(value) for value in array.std(axis=0)]
        std = [value if value > _EPS else 1.0 for value in std]
        return cls(
            feature_columns=list(schema.FEATURE_COLUMNS),
            mean=mean,
            std=std,
            version=version,
            fixed_range=False,
            fitted_sample_count=count,
        )

    @classmethod
    def from_fixed_range(cls) -> "FeatureScaler":
        """无训练集统计量时的兜底：按物理量程做 min-max 标准化。

        仅用于“尚未训练模型”的阶段2演示，模型元数据必须注明 fixed_range=true，
        避免把兜底方案误当作正式训练的标准化参数。
        """
        mean: list[float] = []
        std: list[float] = []
        for column in schema.FEATURE_COLUMNS:
            low, high = DEFAULT_PHYSICAL_RANGE[column]
            mean.append((low + high) / 2.0)
            std.append((high - low) / 2.0)
        return cls(
            feature_columns=list(schema.FEATURE_COLUMNS),
            mean=mean,
            std=std,
            fixed_range=True,
            fitted_sample_count=0,
        )

    @classmethod
    def from_dict(cls, payload: dict) -> "FeatureScaler":
        columns = list(payload.get("feature_columns") or schema.FEATURE_COLUMNS)
        if columns != list(schema.FEATURE_COLUMNS):
            raise ValueError(
                "标准化器特征顺序与系统约定不一致："
                f"文件为 {columns}，系统要求 {list(schema.FEATURE_COLUMNS)}"
            )
        mean = [float(value) for value in payload.get("mean", [])]
        std = [float(value) for value in payload.get("std", [])]
        if len(mean) != schema.FEATURE_DIM or len(std) != schema.FEATURE_DIM:
            raise ValueError(f"标准化器 mean/std 长度必须为 {schema.FEATURE_DIM}。")
        std = [value if abs(value) > _EPS else 1.0 for value in std]
        return cls(
            feature_columns=columns,
            mean=mean,
            std=std,
            version=str(payload.get("version", SCALER_VERSION)),
            created_at=payload.get("created_at"),
            fixed_range=bool(payload.get("fixed_range", False)),
            fitted_sample_count=int(payload.get("fitted_sample_count", 0) or 0),
        )

    # ------------------------------------------------------------------
    # 持久化
    # ------------------------------------------------------------------
    def to_dict(self) -> dict:
        return {
            "feature_columns": self.feature_columns,
            "mean": self.mean,
            "std": self.std,
            "version": self.version,
            "created_at": self.created_at,
            "fixed_range": self.fixed_range,
            "fitted_sample_count": self.fitted_sample_count,
        }

    def save(self, path: str | Path) -> Path:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return target

    @classmethod
    def load(cls, path: str | Path) -> "FeatureScaler":
        raw = Path(path).read_text(encoding="utf-8")
        return cls.from_dict(json.loads(raw))

    # ------------------------------------------------------------------
    # 变换
    # ------------------------------------------------------------------
    def transform_window(self, window: Sequence[Sequence[float]]) -> list[list[float]]:
        return [self.transform_row(row) for row in window]

    def transform_row(self, row: Sequence[float]) -> list[float]:
        if len(row) != schema.FEATURE_DIM:
            raise ValueError(f"特征向量长度必须为 {schema.FEATURE_DIM}，实际为 {len(row)}")
        return [
            (float(value) - self.mean[index]) / self.std[index]
            for index, value in enumerate(row)
        ]

    def inverse_transform(self, matrix: Iterable[Sequence[float]]) -> list[list[float]]:
        return [self.inverse_transform_row(row) for row in matrix]

    def inverse_transform_row(self, row: Sequence[float]) -> list[float]:
        return [
            float(value) * self.std[index] + self.mean[index] for index, value in enumerate(row)
        ]


# ---------------------------------------------------------------------------
# 物理范围修正（11.6）
# ---------------------------------------------------------------------------
def correct_physical_range(
    rows: Iterable[Sequence[float]],
    *,
    previous_row: Sequence[float] | None = None,
    max_jump_deg: float = 8.0,
) -> list[list[float]]:
    """对反标准化后的预测结果做物理修正。

    规则：
    1. 纬度裁剪到 [-90, 90]；经度归一化到 [-180, 180]。
    2. 风速裁剪到 [0, 120] m/s；气压裁剪到 [850, 1020] hPa。
    3. 相邻时效点若出现异常跳变（> max_jump_deg 度），按跳变上限收缩，避免不物理的折线。
    """
    corrected: list[list[float]] = []
    reference = list(previous_row) if previous_row is not None else None
    for row in rows:
        values = [float(value) for value in row]
        if len(values) != schema.FEATURE_DIM:
            raise ValueError(f"预测点长度必须为 {schema.FEATURE_DIM}，实际为 {len(values)}")
        lat = normalize_lat(values[0]) or 0.0
        lon = normalize_lon(values[1]) or 0.0
        wind = min(max(values[2], schema.WIND_RANGE_MPS[0]), schema.WIND_RANGE_MPS[1])
        pressure = min(
            max(values[3], schema.PRESSURE_RANGE_HPA[0]), schema.PRESSURE_RANGE_HPA[1]
        )
        if reference is not None:
            lat = min(max(lat, reference[0] - max_jump_deg), reference[0] + max_jump_deg)
            lon = min(max(lon, reference[1] - max_jump_deg), reference[1] + max_jump_deg)
            wind = min(max(wind, reference[2] - 40.0), reference[2] + 40.0)
            pressure = min(max(pressure, reference[3] - 80.0), reference[3] + 80.0)
        if not all(math.isfinite(value) for value in (lat, lon, wind, pressure)):
            raise ValueError("预测结果包含非有限数值，模型输出可能已损坏。")
        reference = [lat, lon, wind, pressure]
        corrected.append([round(lat, 3), round(lon, 3), round(wind, 2), round(pressure, 1)])
    return corrected


def feature_vector(point) -> list[float]:
    """从 TrackPoint / UploadPoint 等对象提取特征向量（顺序遵循 schema.FEATURE_COLUMNS）。"""
    wind = getattr(point, "max_wind_mps", None)
    pressure = getattr(point, "central_pressure_hpa", None)
    if pressure is None:
        pressure = getattr(point, "pressure_hpa", None)
    return [
        float(getattr(point, "lat")),
        float(getattr(point, "lon")),
        float(wind if wind is not None else 0.0),
        float(pressure if pressure is not None else 0.0),
    ]


def points_to_window(points: Sequence, *, input_length: int = schema.INPUT_LENGTH) -> list[list[float]]:
    """从按时间升序的对象序列中取末尾 input_length 个点作为输入窗口。"""
    if not points:
        return []
    tail = list(points)[-input_length:]
    if len(tail) < input_length:
        return []
    return [feature_vector(point) for point in tail]


__all__ = [
    "SCALER_VERSION",
    "DEFAULT_PHYSICAL_RANGE",
    "FeatureScaler",
    "correct_physical_range",
    "feature_vector",
    "points_to_window",
]
