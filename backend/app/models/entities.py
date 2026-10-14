"""数据库模型（10.2 核心数据实体）。

实体与需求追踪：
    User             -> FR-001/002/012
    LoginAttempt     -> UC-01 连续失败锁定
    Typhoon          -> FR-003
    TrackPoint       -> FR-003/004/009
    DatasetImport    -> FR-013
    ModelVersion     -> FR-014
    PredictionTask   -> FR-006/007/010，NFR-012 可追溯性
    PredictionPoint  -> FR-008/009/011
    AuditLog         -> FR-015

建模约定：
- 时间统一存 UTC（naive datetime，见 app.core.security.utcnow）。
- 经纬度入库前统一为 lat ∈ [-90, 90]、lon ∈ [-180, 180]。
- 外键显式声明 ondelete，SQLite 需在连接时打开 PRAGMA foreign_keys。
"""

from __future__ import annotations

from app.extensions import db
from app.core.security import utcnow


def _iso(value):
    return value.isoformat() + "Z" if value is not None else None


class TimestampMixin:
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False)
    updated_at = db.Column(db.DateTime, default=utcnow, onupdate=utcnow, nullable=False)


# ---------------------------------------------------------------------------
# 账号与安全
# ---------------------------------------------------------------------------
class User(TimestampMixin, db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    display_name = db.Column(db.String(64), nullable=False, default="")
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(16), nullable=False, default="user")  # user / admin
    status = db.Column(db.String(16), nullable=False, default="active")  # active / disabled / locked

    failed_login_count = db.Column(db.Integer, nullable=False, default=0)
    locked_until = db.Column(db.DateTime, nullable=True)
    last_login_at = db.Column(db.DateTime, nullable=True)
    # 改密后使既有会话失效（可选增强），保留字段便于 Step4 扩展
    password_changed_at = db.Column(db.DateTime, nullable=True)

    tasks = db.relationship("PredictionTask", back_populates="user", lazy="dynamic")

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    def to_dict(self, *, include_private: bool = False) -> dict:
        data = {
            "id": self.id,
            "username": self.username,
            "display_name": self.display_name,
            "role": self.role,
            "status": self.status,
            "last_login_at": _iso(self.last_login_at),
            "created_at": _iso(self.created_at),
        }
        if include_private:
            data["locked_until"] = _iso(self.locked_until)
            data["failed_login_count"] = self.failed_login_count
        return data

    def __repr__(self) -> str:  # pragma: no cover
        return f"<User {self.username} role={self.role} status={self.status}>"


class LoginAttempt(db.Model):
    """登录尝试记录：用于统计失败次数与审计（不保存密码）。"""

    __tablename__ = "login_attempts"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), nullable=False, index=True)
    ip_address = db.Column(db.String(64), nullable=True)
    success = db.Column(db.Boolean, nullable=False, default=False)
    reason = db.Column(db.String(64), nullable=True)
    attempted_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)


# ---------------------------------------------------------------------------
# 台风与轨迹
# ---------------------------------------------------------------------------
class Typhoon(TimestampMixin, db.Model):
    __tablename__ = "typhoons"

    id = db.Column(db.Integer, primary_key=True)
    sid = db.Column(db.String(32), unique=True, nullable=False, index=True)  # IBTrACS SID
    name = db.Column(db.String(64), nullable=True, index=True)
    season = db.Column(db.Integer, nullable=False, index=True)  # 年份
    basin = db.Column(db.String(8), nullable=False, default="WP", index=True)
    source = db.Column(db.String(64), nullable=False, default="IBTrACS")
    source_version = db.Column(db.String(32), nullable=True)
    start_time = db.Column(db.DateTime, nullable=True)
    end_time = db.Column(db.DateTime, nullable=True)
    max_wind_mps = db.Column(db.Float, nullable=True)
    min_pressure_hpa = db.Column(db.Float, nullable=True)
    track_point_count = db.Column(db.Integer, nullable=False, default=0)
    # 是否进入中国近海关注区（3.1：训练用整个 WP，展示重点关注中国近海）
    in_focus_area = db.Column(db.Boolean, nullable=False, default=False, index=True)

    dataset_import_id = db.Column(
        db.Integer, db.ForeignKey("dataset_imports.id", ondelete="SET NULL"), nullable=True
    )

    points = db.relationship(
        "TrackPoint",
        back_populates="typhoon",
        cascade="all, delete-orphan",
        order_by="TrackPoint.iso_time",
        lazy="selectin",
    )

    def to_dict(self, *, with_points: bool = False) -> dict:
        data = {
            "id": self.id,
            "sid": self.sid,
            "name": self.name or "",
            "season": self.season,
            "basin": self.basin,
            "source": self.source,
            "source_version": self.source_version,
            "start_time": _iso(self.start_time),
            "end_time": _iso(self.end_time),
            "max_wind_mps": self.max_wind_mps,
            "min_pressure_hpa": self.min_pressure_hpa,
            "track_point_count": self.track_point_count,
            "in_focus_area": self.in_focus_area,
        }
        if with_points:
            data["points"] = [point.to_dict() for point in self.points]
        return data

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Typhoon {self.sid} {self.name} {self.season}>"


class TrackPoint(db.Model):
    """实测轨迹点（3.4 核心数据字典）。"""

    __tablename__ = "track_points"
    __table_args__ = (
        db.UniqueConstraint("typhoon_id", "iso_time", name="uq_track_point_typhoon_time"),
        db.Index("ix_track_point_typhoon_time", "typhoon_id", "iso_time"),
    )

    id = db.Column(db.Integer, primary_key=True)
    typhoon_id = db.Column(
        db.Integer, db.ForeignKey("typhoons.id", ondelete="CASCADE"), nullable=False
    )
    iso_time = db.Column(db.DateTime, nullable=False)
    lat = db.Column(db.Float, nullable=False)
    lon = db.Column(db.Float, nullable=False)
    max_wind_mps = db.Column(db.Float, nullable=True)
    central_pressure_hpa = db.Column(db.Float, nullable=True)
    # original / interpolated / corrected（DQ-05：短缺失插值必须保留质量标志）
    quality_flag = db.Column(db.String(16), nullable=False, default="original")
    intensity_category = db.Column(db.String(16), nullable=True)
    # 气压来源（D-02）：primary 表示主列 USA_PRES，fallback:XXX 表示由其他机构列填补
    pressure_source = db.Column(db.String(32), nullable=True)
    # 该点是否位于关注区（3.1）
    in_focus_area = db.Column(db.Boolean, nullable=False, default=False)

    typhoon = db.relationship("Typhoon", back_populates="points")

    def to_dict(self) -> dict:
        return {
            "iso_time": _iso(self.iso_time),
            "lat": self.lat,
            "lon": self.lon,
            "max_wind_mps": self.max_wind_mps,
            "central_pressure_hpa": self.central_pressure_hpa,
            "quality_flag": self.quality_flag,
            "intensity_category": self.intensity_category,
            "in_focus_area": self.in_focus_area,
            "pressure_source": self.pressure_source,
        }

    def to_features(self) -> list[float]:
        from app.ml.preprocess import feature_vector

        return feature_vector(self)


class DatasetImport(TimestampMixin, db.Model):
    """数据集导入记录（FR-013 / UC-07：来源、版本、校验未过则整批回滚）。"""

    __tablename__ = "dataset_imports"

    id = db.Column(db.Integer, primary_key=True)
    source = db.Column(db.String(128), nullable=False, default="IBTrACS")
    source_version = db.Column(db.String(32), nullable=True)
    original_filename = db.Column(db.String(255), nullable=True)
    stored_filename = db.Column(db.String(255), nullable=True)
    file_size_bytes = db.Column(db.BigInteger, nullable=True)
    row_count = db.Column(db.Integer, nullable=False, default=0)
    typhoon_count = db.Column(db.Integer, nullable=False, default=0)
    skipped_row_count = db.Column(db.Integer, nullable=False, default=0)
    # pending / validating / imported / rolled_back / failed
    status = db.Column(db.String(16), nullable=False, default="pending", index=True)
    message = db.Column(db.String(500), nullable=True)
    imported_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    imported_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source": self.source,
            "source_version": self.source_version,
            "original_filename": self.original_filename,
            "row_count": self.row_count,
            "typhoon_count": self.typhoon_count,
            "skipped_row_count": self.skipped_row_count,
            "status": self.status,
            "message": self.message,
            "imported_by": self.imported_by,
            "imported_at": _iso(self.imported_at),
            "created_at": _iso(self.created_at),
        }


# ---------------------------------------------------------------------------
# 模型版本
# ---------------------------------------------------------------------------
class ModelVersion(TimestampMixin, db.Model):
    """模型版本（FR-014 / UC-08：始终只允许一个当前模型）。"""

    __tablename__ = "model_versions"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)
    version = db.Column(db.String(32), nullable=False)
    arch = db.Column(db.String(64), nullable=False, default="transformer_encoder")
    # rule_based_persistence 为内置基线，不需要权重文件（用于训练完成前的阶段2）
    kind = db.Column(db.String(32), nullable=False, default="transformer")

    weights_path = db.Column(db.String(255), nullable=True)
    scaler_path = db.Column(db.String(255), nullable=True)
    metadata_path = db.Column(db.String(255), nullable=True)
    file_size_bytes = db.Column(db.BigInteger, nullable=True)
    checksum = db.Column(db.String(64), nullable=True)

    input_length = db.Column(db.Integer, nullable=False, default=5)
    output_length = db.Column(db.Integer, nullable=False, default=12)
    feature_dim = db.Column(db.Integer, nullable=False, default=4)
    feature_order = db.Column(db.String(128), nullable=False, default="lat,lon,max_wind,central_pressure")
    hyperparams_json = db.Column(db.Text, nullable=True)
    metrics_json = db.Column(db.Text, nullable=True)
    training_data_version = db.Column(db.String(64), nullable=True)

    is_active = db.Column(db.Boolean, nullable=False, default=False, index=True)
    is_available = db.Column(db.Boolean, nullable=False, default=False)
    validation_message = db.Column(db.String(500), nullable=True)
    uploaded_by = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True)

    tasks = db.relationship("PredictionTask", back_populates="model_version", lazy="dynamic")

    def to_dict(self) -> dict:
        import json

        def _load(raw):
            if not raw:
                return None
            try:
                return json.loads(raw)
            except (ValueError, TypeError):
                return None

        return {
            "id": self.id,
            "name": self.name,
            "version": self.version,
            "arch": self.arch,
            "kind": self.kind,
            "is_active": self.is_active,
            "is_available": self.is_available,
            "input_length": self.input_length,
            "output_length": self.output_length,
            "feature_dim": self.feature_dim,
            "feature_order": self.feature_order,
            "hyperparams": _load(self.hyperparams_json),
            "metrics": _load(self.metrics_json),
            "training_data_version": self.training_data_version,
            "validation_message": self.validation_message,
            "file_size_bytes": self.file_size_bytes,
            "checksum": self.checksum,
            "uploaded_by": self.uploaded_by,
            "created_at": _iso(self.created_at),
        }


# ---------------------------------------------------------------------------
# 预测任务与结果
# ---------------------------------------------------------------------------
class PredictionTask(TimestampMixin, db.Model):
    """预测任务（10.3 生命周期：已创建/校验中/推理中/成功/失败）。"""

    __tablename__ = "prediction_tasks"

    STATUS_CREATED = "created"
    STATUS_VALIDATING = "validating"
    STATUS_RUNNING = "running"
    STATUS_SUCCEEDED = "succeeded"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = (STATUS_CREATED, STATUS_VALIDATING, STATUS_RUNNING, STATUS_SUCCEEDED, STATUS_FAILED)

    id = db.Column(db.Integer, primary_key=True)
    task_code = db.Column(db.String(32), unique=True, nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # 输入来源：历史台风 或 用户上传
    typhoon_id = db.Column(db.Integer, db.ForeignKey("typhoons.id", ondelete="SET NULL"), nullable=True)
    upload_id = db.Column(db.Integer, db.ForeignKey("uploads.id", ondelete="SET NULL"), nullable=True)
    input_source = db.Column(db.String(16), nullable=False, default="typhoon")  # typhoon / upload

    model_version_id = db.Column(
        db.Integer, db.ForeignKey("model_versions.id", ondelete="SET NULL"), nullable=True
    )
    data_version = db.Column(db.String(64), nullable=True)  # NFR-012：记录数据来源

    status = db.Column(db.String(16), nullable=False, default=STATUS_CREATED, index=True)
    message = db.Column(db.String(500), nullable=True)
    error_summary = db.Column(db.String(500), nullable=True)

    # 输入窗口快照（JSON 文本，保证任务可复现）
    input_window_json = db.Column(db.Text, nullable=True)
    input_start_time = db.Column(db.DateTime, nullable=True)
    input_end_time = db.Column(db.DateTime, nullable=True)

    duration_ms = db.Column(db.Integer, nullable=True)
    started_at = db.Column(db.DateTime, nullable=True)
    finished_at = db.Column(db.DateTime, nullable=True)

    model_version = db.relationship("ModelVersion", back_populates="tasks")
    user = db.relationship("User", back_populates="tasks")
    typhoon = db.relationship("Typhoon")
    upload = db.relationship("Upload")

    points = db.relationship(
        "PredictionPoint",
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="PredictionPoint.forecast_hour",
        lazy="selectin",
    )

    def to_dict(self, *, with_points: bool = False, with_input: bool = True) -> dict:
        import json

        data = {
            "id": self.id,
            "task_code": self.task_code,
            "user_id": self.user_id,
            "username": self.user.username if self.user else None,
            "input_source": self.input_source,
            "typhoon_id": self.typhoon_id,
            "typhoon_sid": self.typhoon.sid if self.typhoon else None,
            "typhoon_name": (self.typhoon.name if self.typhoon else None) or (self.upload.label if self.upload else None),
            "upload_id": self.upload_id,
            "model_version_id": self.model_version_id,
            "model_label": self.model_version.name + " " + self.model_version.version if self.model_version else None,
            "model_kind": self.model_version.kind if self.model_version else None,
            "data_version": self.data_version,
            "status": self.status,
            "message": self.message,
            "error_summary": self.error_summary,
            "input_start_time": _iso(self.input_start_time),
            "input_end_time": _iso(self.input_end_time),
            "duration_ms": self.duration_ms,
            "started_at": _iso(self.started_at),
            "finished_at": _iso(self.finished_at),
            "created_at": _iso(self.created_at),
            "point_count": len(self.points) if with_points else None,
        }
        if with_input and self.input_window_json:
            try:
                data["input_window"] = json.loads(self.input_window_json)
            except (ValueError, TypeError):
                data["input_window"] = None
        if with_points:
            data["points"] = [point.to_dict() for point in self.points]
        return data


class PredictionPoint(db.Model):
    """预测点（3.2：未来 12 个时效点，重点展示 6/12/24/48/72 小时）。"""

    __tablename__ = "prediction_points"
    __table_args__ = (
        db.UniqueConstraint("task_id", "forecast_hour", name="uq_prediction_point_task_hour"),
    )

    id = db.Column(db.Integer, primary_key=True)
    task_id = db.Column(
        db.Integer, db.ForeignKey("prediction_tasks.id", ondelete="CASCADE"), nullable=False, index=True
    )
    forecast_hour = db.Column(db.Integer, nullable=False)
    valid_time = db.Column(db.DateTime, nullable=False)
    lat = db.Column(db.Float, nullable=False)
    lon = db.Column(db.Float, nullable=False)
    max_wind_mps = db.Column(db.Float, nullable=True)
    central_pressure_hpa = db.Column(db.Float, nullable=True)
    intensity_category = db.Column(db.String(16), nullable=True)
    intensity_name = db.Column(db.String(32), nullable=True)
    is_key_hour = db.Column(db.Boolean, nullable=False, default=False)
    quality_flag = db.Column(db.String(16), nullable=False, default="predicted")

    # 真实轨迹对比（FR-009）：存在后续实测数据时填充
    actual_lat = db.Column(db.Float, nullable=True)
    actual_lon = db.Column(db.Float, nullable=True)
    actual_wind_mps = db.Column(db.Float, nullable=True)
    actual_pressure_hpa = db.Column(db.Float, nullable=True)
    error_km = db.Column(db.Float, nullable=True)

    task = db.relationship("PredictionTask", back_populates="points")

    def to_dict(self) -> dict:
        return {
            "forecast_hour": self.forecast_hour,
            "valid_time": _iso(self.valid_time),
            "lat": self.lat,
            "lon": self.lon,
            "max_wind_mps": self.max_wind_mps,
            "central_pressure_hpa": self.central_pressure_hpa,
            "intensity_category": self.intensity_category,
            "intensity_name": self.intensity_name,
            "is_key_hour": self.is_key_hour,
            "quality_flag": self.quality_flag,
            "actual": {
                "lat": self.actual_lat,
                "lon": self.actual_lon,
                "max_wind_mps": self.actual_wind_mps,
                "central_pressure_hpa": self.actual_pressure_hpa,
            }
            if self.actual_lat is not None
            else None,
            "error_km": self.error_km,
        }


class Upload(TimestampMixin, db.Model):
    """用户上传的轨迹文件（FR-004/005/006）。

    只保存校验通过的输入窗口；原始文件以随机文件名落盘，禁止路径穿越（6.2）。
    """

    __tablename__ = "uploads"

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    label = db.Column(db.String(128), nullable=False, default="")
    original_filename = db.Column(db.String(255), nullable=True)
    stored_filename = db.Column(db.String(255), nullable=True)
    file_size_bytes = db.Column(db.BigInteger, nullable=True)
    checksum = db.Column(db.String(64), nullable=True)
    sid = db.Column(db.String(32), nullable=True, index=True)
    row_count = db.Column(db.Integer, nullable=False, default=0)
    valid_point_count = db.Column(db.Integer, nullable=False, default=0)
    quality_summary_json = db.Column(db.Text, nullable=True)
    # validated / rejected
    status = db.Column(db.String(16), nullable=False, default="validated", index=True)

    user = db.relationship("User")
    points = db.relationship(
        "UploadPoint",
        back_populates="upload",
        cascade="all, delete-orphan",
        order_by="UploadPoint.iso_time",
        lazy="selectin",
    )

    def to_dict(self, *, with_points: bool = False) -> dict:
        import json

        summary = None
        if self.quality_summary_json:
            try:
                summary = json.loads(self.quality_summary_json)
            except (ValueError, TypeError):
                summary = None
        data = {
            "id": self.id,
            "label": self.label,
            "original_filename": self.original_filename,
            "sid": self.sid,
            "row_count": self.row_count,
            "valid_point_count": self.valid_point_count,
            "status": self.status,
            "quality_summary": summary,
            "created_at": _iso(self.created_at),
        }
        if with_points:
            data["points"] = [point.to_dict() for point in self.points]
        return data


class UploadPoint(db.Model):
    """上传文件中通过校验的观测点。"""

    __tablename__ = "upload_points"

    id = db.Column(db.Integer, primary_key=True)
    upload_id = db.Column(db.Integer, db.ForeignKey("uploads.id", ondelete="CASCADE"), nullable=False)
    iso_time = db.Column(db.DateTime, nullable=False)
    lat = db.Column(db.Float, nullable=False)
    lon = db.Column(db.Float, nullable=False)
    max_wind_mps = db.Column(db.Float, nullable=True)
    central_pressure_hpa = db.Column(db.Float, nullable=True)
    quality_flag = db.Column(db.String(16), nullable=False, default="original")
    source_row = db.Column(db.Integer, nullable=True)

    upload = db.relationship("Upload", back_populates="points")

    def to_dict(self) -> dict:
        return {
            "iso_time": _iso(self.iso_time),
            "lat": self.lat,
            "lon": self.lon,
            "max_wind_mps": self.max_wind_mps,
            "central_pressure_hpa": self.central_pressure_hpa,
            "quality_flag": self.quality_flag,
            "source_row": self.source_row,
        }


# ---------------------------------------------------------------------------
# 审计日志
# ---------------------------------------------------------------------------
class AuditLog(db.Model):
    """操作日志（FR-015）：登录、导入、模型切换、用户管理。"""

    __tablename__ = "audit_logs"

    id = db.Column(db.Integer, primary_key=True)
    actor_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    actor_username = db.Column(db.String(64), nullable=True)
    action = db.Column(db.String(64), nullable=False, index=True)
    target_type = db.Column(db.String(32), nullable=True)
    target_id = db.Column(db.String(64), nullable=True)
    result = db.Column(db.String(16), nullable=False, default="success")  # success / failure
    detail = db.Column(db.String(500), nullable=True)  # 已脱敏，禁止写入密码
    ip_address = db.Column(db.String(64), nullable=True)
    created_at = db.Column(db.DateTime, default=utcnow, nullable=False, index=True)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "actor_id": self.actor_id,
            "actor_username": self.actor_username,
            "action": self.action,
            "target_type": self.target_type,
            "target_id": self.target_id,
            "result": self.result,
            "detail": self.detail,
            "ip_address": self.ip_address,
            "created_at": _iso(self.created_at),
        }


__all__ = [
    "TimestampMixin",
    "User",
    "LoginAttempt",
    "Typhoon",
    "TrackPoint",
    "DatasetImport",
    "ModelVersion",
    "PredictionTask",
    "PredictionPoint",
    "Upload",
    "UploadPoint",
    "AuditLog",
]
