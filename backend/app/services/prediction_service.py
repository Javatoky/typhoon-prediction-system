"""预测业务编排服务（FR-006 ~ FR-011、UC-04/UC-05、12.1 时序）。

分层职责（12.1）：
- 控制器只做权限与参数入口；
- 本服务负责业务编排（校验 → 推理 → 保存 → 对比 → 导出）；
- 预处理器与模型封装算法细节（app.ml）；
- 任何层异常都在此转换为统一业务错误，不向浏览器返回堆栈。

任务状态（10.3）：created → validating → running → succeeded / failed
"""

from __future__ import annotations

import csv
import io
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from flask import current_app
from sqlalchemy import func

from app.core.errors import AppError, NotFoundError, ValidationError
from app.core.security import generate_token, sanitize_text, utcnow
from app.extensions import db
from app.ml import schema
from app.ml.registry import get_registry
from app.models import ModelVersion, PredictionPoint, PredictionTask, TrackPoint, Typhoon, Upload, User
from app.services import audit_service, typhoon_service, upload_service
from app.utils.intensity import classify_intensity
from app.utils.metrics import distance_errors_by_hour, haversine_km, mae
from app.utils.timeutil import add_hours, format_iso, is_exact_step

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 辅助
# ---------------------------------------------------------------------------
def generate_task_code() -> str:
    return f"T{datetime.utcnow().strftime('%Y%m%d%H%M%S')}-{generate_token(3).upper()}"


def _input_length() -> int:
    return int(current_app.config.get("INPUT_LENGTH", schema.INPUT_LENGTH))


def _resolve_source(user: User, *, typhoon_id: int | None, upload_id: int | None) -> tuple[str, list[list[float]], list, str, str | None, Typhoon | None, Upload | None]:
    """解析输入来源，返回 (来源类型, 输入窗口, 输入时刻, 展示名称, 数据版本, 台风, 上传)。"""
    input_length = _input_length()
    if bool(typhoon_id) == bool(upload_id):
        raise ValidationError("请且仅请选择一种输入来源：历史台风(typhoon_id) 或 上传文件(upload_id)。")

    if typhoon_id:
        typhoon = typhoon_service.get_typhoon(int(typhoon_id))
        points = sorted(typhoon.points, key=lambda point: point.iso_time)
        complete = [
            point
            for point in points
            if point.max_wind_mps is not None and point.central_pressure_hpa is not None
        ]
        if len(complete) < input_length:
            raise ValidationError(
                f"台风 {typhoon.sid} 可用观测点不足 {input_length} 个（字段齐全的点共 {len(complete)} 个），无法创建预测任务。"
            )
        tail = complete[-input_length:]
        for previous, current in zip(tail, tail[1:]):
            if not is_exact_step(current.iso_time, previous.iso_time, schema.TIME_STEP_HOURS):
                raise ValidationError(
                    f"台风 {typhoon.sid} 最近 {input_length} 个观测点的时间间隔不是 6 小时，"
                    "请改选其他时间段或使用上传数据。"
                )
        window = [
            [float(point.lat), float(point.lon), float(point.max_wind_mps), float(point.central_pressure_hpa)]
            for point in tail
        ]
        times = [point.iso_time for point in tail]
        label = f"{typhoon.sid} {typhoon.name or ''}".strip()
        data_version = typhoon.source_version or typhoon.source
        return "typhoon", window, times, label, data_version, typhoon, None

    upload = upload_service.get_upload(user, int(upload_id))
    window = upload_service.window_from_upload(upload, input_length=input_length)
    if not window:
        raise ValidationError(
            "该上传记录中没有连续 6 小时、四特征齐全的观测点，请重新上传包含连续观测点的 CSV。"
        )
    times = upload_service.window_times(upload, input_length=input_length)
    label = upload.label or (upload.original_filename or f"上传 {upload.id}")
    return "upload", window, times, label, f"upload:{upload.id}", None, upload


def _active_model() -> ModelVersion | None:
    return (
        ModelVersion.query.filter_by(is_active=True).order_by(ModelVersion.id.desc()).first()
    )


def create_task(
    user: User,
    *,
    typhoon_id: int | None = None,
    upload_id: int | None = None,
    model_version_id: int | None = None,
) -> PredictionTask:
    """创建并执行一次预测任务（同步执行，保证单用户推理在 NFR-002 的 5 秒内返回）。"""
    started = time.perf_counter()
    input_length = _input_length()
    source_type, window, times, label, data_version, typhoon, upload = _resolve_source(
        user, typhoon_id=typhoon_id, upload_id=upload_id
    )

    model_record = None
    if model_version_id:
        model_record = ModelVersion.query.get(int(model_version_id))
        if model_record is None or not model_record.is_available:
            raise ValidationError(f"模型版本 {model_version_id} 不存在或不可用，请选择其他版本。")
    else:
        model_record = _active_model()

    task = PredictionTask(
        task_code=generate_task_code(),
        user_id=user.id,
        typhoon_id=typhoon.id if typhoon else None,
        upload_id=upload.id if upload else None,
        input_source=source_type,
        model_version_id=model_record.id if model_record else None,
        data_version=data_version,
        status=PredictionTask.STATUS_CREATED,
        message=f"输入：{label}",
        input_window_json=json.dumps(
            {
                "feature_order": list(schema.FEATURE_COLUMNS),
                "window": window,
            },
            ensure_ascii=False,
        ),
        input_start_time=times[0] if times else None,
        input_end_time=times[-1] if times else None,
        started_at=utcnow(),
    )
    db.session.add(task)
    db.session.commit()  # 先落库，保证失败任务也可追踪（10.3）

    try:
        task.status = PredictionTask.STATUS_VALIDATING
        db.session.commit()

        if len(window) != input_length:
            raise ValidationError(
                f"输入窗口必须包含 {input_length} 个连续观测点，当前为 {len(window)} 个。"
            )

        task.status = PredictionTask.STATUS_RUNNING
        db.session.commit()

        registry = get_registry()
        # 若任务指定了与当前加载不同的模型版本，按需切换，保证“旧任务保留原模型版本号”
        if model_record and registry.current.model_version_id != model_record.id:
            _ensure_forecaster(model_record)
        forecast = registry.current.predict(window)
        base_time = times[-1] if times else utcnow()

        key_hours = set(current_app.config.get("KEY_FORECAST_HOURS", list(schema.KEY_FORECAST_HOURS)))
        for point, forecast_hour in zip(forecast.points, forecast.forecast_hours):
            valid_time = add_hours(base_time, forecast_hour)
            code, name = classify_intensity(point[2])
            db.session.add(
                PredictionPoint(
                    task_id=task.id,
                    forecast_hour=forecast_hour,
                    valid_time=valid_time,
                    lat=point[0],
                    lon=point[1],
                    max_wind_mps=point[2],
                    central_pressure_hpa=point[3],
                    intensity_category=code,
                    intensity_name=name,
                    is_key_hour=forecast_hour in key_hours,
                    quality_flag="predicted",
                )
            )

        task.status = PredictionTask.STATUS_SUCCEEDED
        task.message = f"预测成功：{label}，模型 {forecast.model_label}"
        task.error_summary = None
        db.session.commit()

        if typhoon is not None:
            compare_with_actual(task, typhoon)

        task.finished_at = utcnow()
        task.duration_ms = int((time.perf_counter() - started) * 1000)
        audit_service.record(
            audit_service.ACTION_PREDICTION_CREATE,
            actor=user,
            target_type="prediction_task",
            target_id=task.task_code,
            detail=f"来源 {source_type}，模型 {forecast.model_label}，耗时 {task.duration_ms} ms",
        )
        db.session.commit()
        logger.info(
            "预测任务 %s 成功：用户=%s 模型=%s 耗时=%dms",
            task.task_code,
            user.username,
            forecast.model_label,
            task.duration_ms,
        )
        return task

    except AppError as exc:
        _fail_task(task, exc.message)
        raise
    except Exception as exc:  # 未预期异常：不向浏览器返回堆栈（12.1）
        logger.exception("预测任务 %s 发生未预期异常", task.task_code)
        _fail_task(task, f"服务器内部错误：{exc.__class__.__name__}")
        raise AppError(
            "预测过程中出现服务器内部错误，任务已标记为失败，请联系管理员查看应用日志。",
            status_code=500,
            code="internal_error",
        ) from exc


def _ensure_forecaster(model_record: ModelVersion) -> None:
    """按指定模型版本准备推理器（用于重跑历史任务或指定模型版本）。"""
    from app.ml.forecaster import build_baseline_forecaster, build_transformer_forecaster
    from app.ml.forecaster import KIND_BASELINE

    registry = get_registry()
    if model_record.kind == KIND_BASELINE or not model_record.weights_path:
        registry.set_forecaster(
            build_baseline_forecaster(
                note=f"任务指定了内置基线模型 {model_record.name} {model_record.version}。"
            ),
            source="database",
        )
        return
    hyperparams = json.loads(model_record.hyperparams_json) if model_record.hyperparams_json else {}
    forecaster = build_transformer_forecaster(
        weights_path=model_record.weights_path,
        hyperparams=hyperparams,
        scaler_path=model_record.scaler_path,
        label=f"{model_record.name} {model_record.version}",
        model_version_id=model_record.id,
        training_data_version=model_record.training_data_version,
    )
    registry.set_forecaster(forecaster, source="database")


def _fail_task(task: PredictionTask, message: str) -> None:
    """把任务置为失败并保存错误摘要（不保存堆栈、不保存密码，10.3）。"""
    try:
        db.session.rollback()
        fresh = db.session.get(PredictionTask, task.id)
        if fresh is not None:
            fresh.status = PredictionTask.STATUS_FAILED
            fresh.error_summary = sanitize_text(
                message, max_length=int(current_app.config.get("TASK_ERROR_SUMMARY_MAX_CHARS", 500))
            )
            fresh.message = "预测失败，请根据错误提示修正输入后重新创建任务。"
            fresh.finished_at = utcnow()
            db.session.commit()
    except Exception:  # pragma: no cover
        logger.exception("标记任务 %s 失败状态时出错", getattr(task, "task_code", "?"))
        db.session.rollback()


# ---------------------------------------------------------------------------
# 真实轨迹对比（FR-009）
# ---------------------------------------------------------------------------
def compare_with_actual(task: PredictionTask, typhoon: Typhoon) -> dict[str, Any] | None:
    """存在后续实测数据时计算分时效误差；否则返回 None 并在任务信息中说明。

    匹配顺序（UC-05 / FR-009）：
    1. 首选“预测有效时刻 == 实测时刻”的精确匹配（历史台风场景，最严格）；
    2. 若完全没有精确匹配，则回退为“输入窗口之后的后续实测点按顺序对齐”。
       该回退覆盖“截至今日的实测轨迹”场景（最后一个观测点就是最后可用观测），
       此时若严格要求时刻精确相等，FR-009 将永远无法演示。
    """
    points = sorted(task.points, key=lambda item: item.forecast_hour)
    actual_by_time = {point.iso_time: point for point in typhoon.points}
    matched_pairs: list[tuple[Any, Any]] = []
    mode = "exact_time"

    for point in points:
        observed = actual_by_time.get(point.valid_time)
        if observed is not None:
            matched_pairs.append((point, observed))

    if not matched_pairs:
        follow_up = [
            point
            for point in sorted(typhoon.points, key=lambda item: item.iso_time)
            if task.input_end_time is not None and point.iso_time > task.input_end_time
        ]
        if follow_up:
            mode = "sequential_follow_up"
            matched_pairs = list(zip(points, follow_up))

    if not matched_pairs:
        task.message = (task.message or "") + "；该输入时刻之后没有实测数据，暂不可对比误差。"
        db.session.commit()
        return None

    predicted_positions: list[list[float]] = []
    actual_positions: list[list[float]] = []
    predicted_wind: list[float | None] = []
    actual_wind: list[float | None] = []
    predicted_pressure: list[float | None] = []
    actual_pressure: list[float | None] = []
    predicted_category: list[str | None] = []
    actual_category: list[str | None] = []
    hours: list[int] = []

    for point, observed in matched_pairs:
        point.actual_lat = observed.lat
        point.actual_lon = observed.lon
        point.actual_wind_mps = observed.max_wind_mps
        point.actual_pressure_hpa = observed.central_pressure_hpa
        point.error_km = round(
            haversine_km(point.lat, point.lon, observed.lat, observed.lon), 2
        )
        predicted_positions.append([point.lat, point.lon])
        actual_positions.append([observed.lat, observed.lon])
        predicted_wind.append(point.max_wind_mps)
        actual_wind.append(observed.max_wind_mps)
        predicted_pressure.append(point.central_pressure_hpa)
        actual_pressure.append(observed.central_pressure_hpa)
        predicted_category.append(point.intensity_category)
        actual_category.append(classify_intensity(observed.max_wind_mps)[0])
        hours.append(point.forecast_hour)

    matched = len(matched_pairs)
    by_hour = distance_errors_by_hour(predicted_positions, actual_positions, hours)
    note = (
        "误差按 Haversine 大圆距离计算，仅覆盖存在实测数据的时效点。"
        if mode == "exact_time"
        else "误差按 Haversine 大圆距离计算；实测时刻与预测有效时刻不完全一致，"
        "已按输入窗口之后的后续实测点顺序对齐，仅作教学演示。"
    )
    summary = {
        "matched_points": matched,
        "match_mode": mode,
        "distance_km": {str(hour): round(value, 2) for hour, value in by_hour.items()},
        "ade_km": round(sum(by_hour.values()) / len(by_hour), 2),
        "wind_mae_mps": round(mae(predicted_wind, actual_wind), 3),
        "pressure_mae_hpa": round(mae(predicted_pressure, actual_pressure), 3),
        "category_accuracy": round(
            sum(1 for p, a in zip(predicted_category, actual_category) if p == a) / matched, 4
        ),
        "note": note,
    }
    task.message = f"{task.message or ''}；已对比 {matched} 个时效点的实测数据。"
    db.session.commit()
    return summary


# ---------------------------------------------------------------------------
# 查询与导出
# ---------------------------------------------------------------------------
def list_tasks(
    user: User,
    *,
    status: str | None = None,
    page: int = 1,
    page_size: int = 20,
    all_users: bool = False,
) -> tuple[list[PredictionTask], int]:
    query = PredictionTask.query
    if not (all_users and user.is_admin):
        query = query.filter(PredictionTask.user_id == user.id)
    if status:
        query = query.filter(PredictionTask.status == status)
    total = query.count()
    items = (
        query.order_by(PredictionTask.created_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return items, total


def get_task(user: User, task_id: int) -> PredictionTask:
    task = db.session.get(PredictionTask, task_id)
    if task is None or (task.user_id != user.id and not user.is_admin):
        # 非所有者按不存在处理，避免泄露他人任务是否存在（7 权限要求）
        raise NotFoundError(f"未找到编号为 {task_id} 的预测任务。")
    return task


def task_detail(task: PredictionTask) -> dict[str, Any]:
    """任务详情：任务元数据 + 输入窗口 + 预测点 + 误差对比。"""
    payload = task.to_dict(with_points=True)
    payload["comparison"] = comparison_summary(task)
    payload["intensity_thresholds"] = _thresholds()
    return payload


def comparison_summary(task: PredictionTask) -> dict[str, Any] | None:
    points = [point for point in task.points if point.actual_lat is not None]
    if not points:
        return None
    by_hour = {
        point.forecast_hour: point.error_km
        for point in points
        if point.error_km is not None
    }
    return {
        "matched_points": len(points),
        "distance_km": {str(hour): value for hour, value in sorted(by_hour.items())},
        "ade_km": round(sum(by_hour.values()) / len(by_hour), 2) if by_hour else None,
        "wind_mae_mps": _safe_mae(
            [point.max_wind_mps for point in points], [point.actual_wind_mps for point in points]
        ),
        "pressure_mae_hpa": _safe_mae(
            [point.central_pressure_hpa for point in points],
            [point.actual_pressure_hpa for point in points],
        ),
        "note": "误差按 Haversine 大圆距离计算；仅在输入时刻之后存在实测数据时提供。",
    }


def _safe_mae(predicted, actual):
    value = mae(predicted, actual)
    return None if value != value else round(value, 3)


def _thresholds() -> list[dict]:
    from app.utils.intensity import threshold_table

    return threshold_table()


EXPORT_HEADERS = [
    "task_code",
    "source",
    "model",
    "data_version",
    "forecast_hour",
    "valid_time",
    "lat",
    "lon",
    "max_wind_mps",
    "pressure_hpa",
    "intensity_category",
    "intensity_name",
    "actual_lat",
    "actual_lon",
    "actual_wind_mps",
    "actual_pressure_hpa",
    "error_km",
    "quality_flag",
]


def build_export_csv(task: PredictionTask) -> str:
    """生成 CSV 文本（FR-011）：包含任务元数据、时效、有效时间、经纬度、风速、气压、等级与误差。"""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["# 台风路径预测结果导出"])
    writer.writerow(["# 任务编号", task.task_code])
    writer.writerow(["# 创建用户", task.user.username if task.user else ""])
    writer.writerow(["# 数据来源", task.data_version or ""])
    writer.writerow(["# 模型版本", f"{task.model_version.name} {task.model_version.version}" if task.model_version else "持续性基线"])
    writer.writerow(["# 输入开始时间", format_iso(task.input_start_time) or ""])
    writer.writerow(["# 输入结束时间", format_iso(task.input_end_time) or ""])
    writer.writerow(["# 创建时间", format_iso(task.created_at) or ""])
    writer.writerow(["# 说明", "结果仅用于教学研究与系统演示，不作为官方防灾决策依据。"])
    writer.writerow([])
    writer.writerow(EXPORT_HEADERS)
    for point in sorted(task.points, key=lambda item: item.forecast_hour):
        writer.writerow(
            [
                task.task_code,
                task.input_source,
                f"{task.model_version.name} {task.model_version.version}" if task.model_version else "持续性基线",
                task.data_version or "",
                point.forecast_hour,
                format_iso(point.valid_time),
                f"{point.lat:.3f}",
                f"{point.lon:.3f}",
                "" if point.max_wind_mps is None else f"{point.max_wind_mps:.2f}",
                "" if point.central_pressure_hpa is None else f"{point.central_pressure_hpa:.1f}",
                point.intensity_category or "",
                point.intensity_name or "",
                "" if point.actual_lat is None else f"{point.actual_lat:.3f}",
                "" if point.actual_lon is None else f"{point.actual_lon:.3f}",
                "" if point.actual_wind_mps is None else f"{point.actual_wind_mps:.2f}",
                "" if point.actual_pressure_hpa is None else f"{point.actual_pressure_hpa:.1f}",
                "" if point.error_km is None else f"{point.error_km:.2f}",
                point.quality_flag,
            ]
        )
    return buffer.getvalue()


def export_filename(task: PredictionTask) -> str:
    stamp = (task.created_at or utcnow()).strftime("%Y%m%d%H%M")
    return f"prediction_{task.task_code}_{stamp}.csv"


def export_dir() -> Path:
    path = Path(current_app.config["EXPORT_DIR"])
    path.mkdir(parents=True, exist_ok=True)
    return path


def statistics(user: User) -> dict[str, Any]:
    """工作台统计卡片数据。"""
    query = PredictionTask.query
    if not user.is_admin:
        query = query.filter(PredictionTask.user_id == user.id)
    total = query.count()
    succeeded = query.filter(PredictionTask.status == PredictionTask.STATUS_SUCCEEDED).count()
    failed = query.filter(PredictionTask.status == PredictionTask.STATUS_FAILED).count()
    avg_duration = (
        db.session.query(func.avg(PredictionTask.duration_ms))
        .filter(PredictionTask.status == PredictionTask.STATUS_SUCCEEDED)
        .scalar()
    )
    return {
        "task_total": total,
        "task_succeeded": succeeded,
        "task_failed": failed,
        "avg_duration_ms": int(avg_duration) if avg_duration else None,
        "typhoon_total": Typhoon.query.count(),
    }


__all__ = [
    "generate_task_code",
    "create_task",
    "compare_with_actual",
    "list_tasks",
    "get_task",
    "task_detail",
    "comparison_summary",
    "build_export_csv",
    "export_filename",
    "export_dir",
    "statistics",
    "EXPORT_HEADERS",
]
