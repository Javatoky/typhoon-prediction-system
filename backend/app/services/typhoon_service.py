"""历史台风查询服务（FR-003、UC-02）。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import or_

from app.core.errors import NotFoundError
from app.models import TrackPoint, Typhoon
from app.utils.intensity import classify_intensity


def list_typhoons(
    *,
    keyword: str | None = None,
    season: int | None = None,
    season_from: int | None = None,
    season_to: int | None = None,
    basin: str | None = None,
    focus_only: bool = False,
    page: int = 1,
    page_size: int = 20,
) -> tuple[list[Typhoon], int]:
    """按名称/编号/年份查询台风列表（无匹配时返回空列表而不是错误，UC-02 扩展流程）。"""
    query = Typhoon.query

    if keyword:
        pattern = f"%{keyword.strip()}%"
        query = query.filter(
            or_(
                Typhoon.name.ilike(pattern),
                Typhoon.sid.ilike(pattern),
            )
        )
    if season is not None:
        query = query.filter(Typhoon.season == season)
    if season_from is not None:
        query = query.filter(Typhoon.season >= season_from)
    if season_to is not None:
        query = query.filter(Typhoon.season <= season_to)
    if basin:
        query = query.filter(Typhoon.basin == basin.upper())
    if focus_only:
        query = query.filter(Typhoon.in_focus_area.is_(True))

    total = query.count()
    items = (
        query.order_by(Typhoon.season.desc(), Typhoon.sid.asc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )
    return items, total


def get_typhoon(typhoon_id: int) -> Typhoon:
    typhoon = Typhoon.query.get(typhoon_id)
    if typhoon is None:
        raise NotFoundError(f"未找到编号为 {typhoon_id} 的台风记录。")
    return typhoon


def get_track(typhoon: Typhoon) -> dict[str, Any]:
    """返回实测轨迹，并附带强度等级与关注区标记。"""
    points = sorted(typhoon.points, key=lambda point: point.iso_time)
    series: list[dict[str, Any]] = []
    for point in points:
        code, name = classify_intensity(point.max_wind_mps)
        item = point.to_dict()
        item["intensity_code"] = code
        item["intensity_name"] = name
        series.append(item)

    focus_points = [item for item in series if item.get("in_focus_area")]
    return {
        "typhoon": typhoon.to_dict(),
        "points": series,
        "focus_segment": focus_points,
        "point_count": len(series),
        "missing_wind_count": sum(1 for item in series if item.get("max_wind_mps") is None),
        "missing_pressure_count": sum(
            1 for item in series if item.get("central_pressure_hpa") is None
        ),
        "interpolated_count": sum(
            1 for item in series if item.get("quality_flag") == "interpolated"
        ),
    }


def available_seasons() -> list[int]:
    rows = (
        Typhoon.query.with_entities(Typhoon.season)
        .distinct()
        .order_by(Typhoon.season.desc())
        .all()
    )
    return [int(row[0]) for row in rows]


def track_times(typhoon: Typhoon) -> set:
    """该台风全部实测时刻集合，供预测结果对比使用（FR-009）。"""
    return {point.iso_time for point in typhoon.points}


def latest_points(typhoon: Typhoon, count: int) -> list[TrackPoint]:
    points = sorted(typhoon.points, key=lambda point: point.iso_time)
    return points[-count:]


__all__ = [
    "list_typhoons",
    "get_typhoon",
    "get_track",
    "available_seasons",
    "track_times",
    "latest_points",
]
