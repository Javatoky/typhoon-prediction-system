"""历史台风查询接口（FR-003、UC-02）。"""

from __future__ import annotations

from flask import Blueprint, request

from app.api.deps import current_user, login_required
from app.core.params import optional_bool, optional_int, parse_pagination
from app.core.responses import paginated, success
from app.services import typhoon_service

bp = Blueprint("typhoons", __name__, url_prefix="/api/typhoons")


def _query_params() -> dict:
    """GET 查询参数统一解析为与 JSON 相同的字典结构。"""
    return {key: value for key, value in request.args.items()}


@bp.get("")
@login_required
def list_typhoons():
    params = _query_params()
    page, page_size = parse_pagination(
        params,
        default=int(current_app_page_size()),
        maximum=100,
    )
    items, total = typhoon_service.list_typhoons(
        keyword=params.get("keyword") or params.get("name") or None,
        season=optional_int(params, "season", minimum=1840, maximum=2200, label="年份"),
        season_from=optional_int(params, "season_from", minimum=1840, maximum=2200, label="起始年份"),
        season_to=optional_int(params, "season_to", minimum=1840, maximum=2200, label="结束年份"),
        basin=params.get("basin") or None,
        focus_only=optional_bool(params, "focus_only", default=False),
        page=page,
        page_size=page_size,
    )
    return paginated(
        [typhoon.to_dict() for typhoon in items], page=page, page_size=page_size, total=total
    )


@bp.get("/seasons")
@login_required
def seasons():
    return success({"seasons": typhoon_service.available_seasons()})


@bp.get("/<int:typhoon_id>")
@login_required
def typhoon_detail(typhoon_id: int):
    typhoon = typhoon_service.get_typhoon(typhoon_id)
    return success(typhoon.to_dict())


@bp.get("/<int:typhoon_id>/track")
@login_required
def typhoon_track(typhoon_id: int):
    typhoon = typhoon_service.get_typhoon(typhoon_id)
    return success(typhoon_service.get_track(typhoon))


def current_app_page_size() -> int:
    from flask import current_app

    return int(current_app.config.get("PAGE_SIZE_DEFAULT", 20))


__all__ = ["bp"]
