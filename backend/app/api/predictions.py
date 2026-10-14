"""预测任务接口（FR-006 ~ FR-011、7 外部接口需求）。"""

from __future__ import annotations

from flask import Blueprint, Response, request

from app.api.deps import current_user, login_required
from app.core.params import optional_int, parse_pagination
from app.core.responses import paginated, success
from app.models import PredictionTask
from app.services import prediction_service

bp = Blueprint("predictions", __name__, url_prefix="/api/predictions")


@bp.post("")
@login_required
def create_prediction():
    """创建预测任务（UC-04）。"""
    from app.core.params import get_json

    payload = get_json()
    typhoon_id = optional_int(payload, "typhoon_id", minimum=1, label="台风ID")
    upload_id = optional_int(payload, "upload_id", minimum=1, label="上传记录ID")
    model_version_id = optional_int(payload, "model_version_id", minimum=1, label="模型版本ID")
    task = prediction_service.create_task(
        current_user(),
        typhoon_id=typhoon_id,
        upload_id=upload_id,
        model_version_id=model_version_id,
    )
    return success(prediction_service.task_detail(task), status=201)


@bp.get("")
@login_required
def list_predictions():
    params = {key: value for key, value in request.args.items()}
    from flask import current_app

    page, page_size = parse_pagination(
        params, default=int(current_app.config.get("PAGE_SIZE_DEFAULT", 20)), maximum=100
    )
    user = current_user()
    items, total = prediction_service.list_tasks(
        user,
        status=params.get("status") or None,
        page=page,
        page_size=page_size,
        all_users=params.get("scope") == "all",
    )
    return paginated(
        [task.to_dict(with_points=False) for task in items],
        page=page,
        page_size=page_size,
        total=total,
    )


@bp.get("/statistics")
@login_required
def statistics():
    return success(prediction_service.statistics(current_user()))


@bp.get("/<int:task_id>")
@login_required
def task_detail(task_id: int):
    task = prediction_service.get_task(current_user(), task_id)
    return success(prediction_service.task_detail(task))


@bp.get("/<int:task_id>/export")
@login_required
def export_task(task_id: int):
    """导出标准 CSV（FR-011）。导出失败不得影响页面结果，因此单独成接口。"""
    task = prediction_service.get_task(current_user(), task_id)
    content = prediction_service.build_export_csv(task)
    filename = prediction_service.export_filename(task)
    # 使用 UTF-8 BOM，保证 Excel 直接双击打开不乱码
    body = ("\ufeff" + content).encode("utf-8")
    return Response(
        body,
        mimetype="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Task-Code": task.task_code,
        },
    )


@bp.get("/status-choices")
@login_required
def status_choices():
    from flask import current_app

    return success(
        {
            "statuses": [
                {"value": value, "label": label}
                for value, label in [
                    (PredictionTask.STATUS_CREATED, "已创建"),
                    (PredictionTask.STATUS_VALIDATING, "校验中"),
                    (PredictionTask.STATUS_RUNNING, "推理中"),
                    (PredictionTask.STATUS_SUCCEEDED, "成功"),
                    (PredictionTask.STATUS_FAILED, "失败"),
                ]
            ],
            "key_forecast_hours": list(current_app.config.get("KEY_FORECAST_HOURS", [])),
        }
    )


__all__ = ["bp"]
