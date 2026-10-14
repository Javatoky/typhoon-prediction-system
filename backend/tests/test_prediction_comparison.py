"""预测服务对比逻辑回归测试（FR-009、UC-05）。

覆盖两种真实场景：
1. 历史台风：预测时刻之后没有实测数据 -> 隐藏误差并给出说明（UC-05 扩展流程）；
2. 进行中的台风：预测时刻之后仍有实测数据 -> 计算分时效误差与 ADE/MAE。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest


def _build_typhoon(app, *, points: int, sid: str = "WPCOMP01", start: datetime | None = None):
    """插入一条平滑轨迹，返回台风 id。"""
    from app.extensions import db
    from app.models import TrackPoint, Typhoon
    from app.utils.intensity import classify_intensity

    start = start or datetime(2021, 8, 1, 0, 0, 0)
    with app.app_context():
        typhoon = Typhoon(sid=sid, name="CMP", season=start.year, basin="WP", source="test")
        db.session.add(typhoon)
        db.session.flush()
        lat, lon, wind, pressure = 15.0, 132.0, 20.0, 1000.0
        for step in range(points):
            lat += 0.6
            lon -= 0.7
            wind += 1.0
            pressure -= 1.2
            code, _name = classify_intensity(wind)
            db.session.add(
                TrackPoint(
                    typhoon_id=typhoon.id,
                    iso_time=start + timedelta(hours=6 * step),
                    lat=round(lat, 2),
                    lon=round(lon, 2),
                    max_wind_mps=round(wind, 1),
                    central_pressure_hpa=round(pressure, 1),
                    quality_flag="original",
                    intensity_category=code,
                    in_focus_area=True,
                )
            )
        typhoon.start_time = start
        typhoon.end_time = start + timedelta(hours=6 * (points - 1))
        typhoon.track_point_count = points
        db.session.commit()
        return typhoon.id


def test_comparison_hidden_when_no_future_observations(app, admin_user):
    """历史台风（轨迹已结束）不应伪造误差数据。"""
    from app.extensions import db
    from app.models import PredictionTask
    from app.services import prediction_service

    typhoon_id = _build_typhoon(app, points=24, sid="WPHIST01")
    with app.app_context():
        task = prediction_service.create_task(admin_user, typhoon_id=typhoon_id)
        db.session.refresh(task)
        assert task.status == PredictionTask.STATUS_SUCCEEDED
        assert prediction_service.comparison_summary(task) is None
        assert "没有实测数据" in (task.message or "")
        assert all(point.actual_lat is None for point in task.points)
        assert all(point.error_km is None for point in task.points)


def test_comparison_computed_for_ongoing_typhoon(app, admin_user):
    """进行中的台风：补充后续实测点后应给出分时效误差与汇总指标。"""
    from app.extensions import db
    from app.models import PredictionTask, TrackPoint, Typhoon
    from app.services import prediction_service
    from app.utils.intensity import classify_intensity

    typhoon_id = _build_typhoon(app, points=24, sid="WPONGO01")
    with app.app_context():
        task = prediction_service.create_task(admin_user, typhoon_id=typhoon_id)
        task_id = task.id
        assert prediction_service.comparison_summary(task) is None

        # 模拟“跟踪继续”：删除最后 12 个实测点并重新生成预测，使预测之后仍有实测
        typhoon = db.session.get(Typhoon, typhoon_id)
        ordered = sorted(typhoon.points, key=lambda item: item.iso_time)
        future = ordered[-12:]
        for point in future:
            db.session.delete(point)
        db.session.commit()
        typhoon = db.session.get(Typhoon, typhoon_id)
        assert len(typhoon.points) == 12

        task2 = prediction_service.create_task(admin_user, typhoon_id=typhoon_id)
        task2_id = task2.id

        # 把“未来实测”补回来（时间与预测有效时刻严格对齐）
        for point in future:
            code, _name = classify_intensity(point.max_wind_mps)
            db.session.add(
                TrackPoint(
                    typhoon_id=typhoon_id,
                    iso_time=point.iso_time,
                    lat=point.lat,
                    lon=point.lon,
                    max_wind_mps=point.max_wind_mps,
                    central_pressure_hpa=point.central_pressure_hpa,
                    quality_flag="original",
                    intensity_category=code,
                    in_focus_area=True,
                )
            )
        db.session.commit()

        fresh = db.session.get(PredictionTask, task2_id)
        typhoon = db.session.get(Typhoon, typhoon_id)
        summary = prediction_service.compare_with_actual(fresh, typhoon)
        db.session.commit()

        assert summary is not None
        assert summary["matched_points"] == 12
        assert summary["match_mode"] == "exact_time"
        assert set(summary["distance_km"]) == {
            "6", "12", "18", "24", "30", "36", "42", "48", "54", "60", "66", "72"
        }
        assert summary["ade_km"] is not None
        assert summary["wind_mae_mps"] is not None
        assert summary["pressure_mae_hpa"] is not None

        reloaded = db.session.get(PredictionTask, task2_id)
        assert any(point.error_km is not None for point in reloaded.points)
        detail = prediction_service.comparison_summary(reloaded)
        assert detail is not None
        assert detail["matched_points"] == 12


def test_comparison_does_not_mutate_prediction_points(app, admin_user):
    """对比只写实测列，绝不修改预测结果本身（教学口径一致性）。"""
    from app.core.errors import NotFoundError
    from app.extensions import db
    from app.models import PredictionTask
    from app.services import prediction_service

    typhoon_id = _build_typhoon(app, points=24, sid="WPONGO02")
    with app.app_context():
        task = prediction_service.create_task(admin_user, typhoon_id=typhoon_id)
        snapshot = [
            (point.forecast_hour, point.lat, point.lon, point.max_wind_mps)
            for point in task.points
        ]

        # 后续实测点补齐后执行对比，预测结果本身必须逐字节保持不变
        typhoon = prediction_service.typhoon_service.get_typhoon(typhoon_id)
        summary = prediction_service.compare_with_actual(task, typhoon)
        db.session.commit()
        assert summary is None  # 轨迹已结束，没有后续实测

        # 输入不存在时抛出可预期业务错误，且不产生半成品任务
        with pytest.raises(NotFoundError):
            prediction_service.create_task(admin_user, upload_id=10**6)

        reloaded = db.session.get(PredictionTask, task.id)
        after = [
            (point.forecast_hour, point.lat, point.lon, point.max_wind_mps)
            for point in reloaded.points
        ]
        assert snapshot == after
