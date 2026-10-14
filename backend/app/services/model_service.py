"""模型版本管理服务（FR-014、UC-08）。

关键约束：
- 上传时必须校验元数据与输入输出维度，并在隔离过程中试加载；
- 试加载失败不得替换当前模型，系统始终只允许一个当前模型；
- 切换成功后新任务使用新模型，旧任务保留原模型版本号（外键，不需要额外处理）。
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.core.security import generate_token, normalize_stored_filename, utcnow
from app.extensions import db
from app.ml import schema
from app.ml.forecaster import KIND_BASELINE, torch_available
from app.models import ModelVersion, User
from app.services import audit_service

logger = logging.getLogger(__name__)

BASELINE_NAME = "持续性预测基线"
BASELINE_VERSION = "0.1.0"
ACTION_MODEL_REGISTER = "admin.model_register"


@dataclass
class UploadOutcome:
    record: ModelVersion
    warnings: list[str]

    def to_dict(self) -> dict:
        return {"model": self.record.to_dict(), "warnings": self.warnings}


def _store_dir() -> Path:
    from flask import current_app

    path = Path(current_app.config["MODEL_STORE_DIR"])
    path.mkdir(parents=True, exist_ok=True)
    return path


def _save_bytes(raw: bytes, original_name: str) -> tuple[Path, str]:
    suffix = normalize_stored_filename(original_name) or ".bin"
    target = _store_dir() / f"{generate_token(16)}{suffix}"
    target.write_bytes(raw)
    return target, hashlib.sha256(raw).hexdigest()


def _parse_metadata(raw: bytes | None, *, fallback_name: str) -> tuple[dict, str | None]:
    if raw is None:
        return {}, None
    try:
        payload = json.loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise ValidationError(f"元数据必须是 UTF-8 编码的 JSON 对象：{exc}") from None
    if not isinstance(payload, dict):
        raise ValidationError("元数据 JSON 顶层必须是对象，例如 {\"name\": \"...\", \"hyperparams\": {...}}。")
    payload.setdefault("name", fallback_name)
    return payload, json.dumps(payload, ensure_ascii=False)


def _validate_feature_order(metadata: dict) -> None:
    order = metadata.get("feature_order") or metadata.get("feature_columns")
    if order is None:
        return
    normalized = [str(item).strip() for item in order]
    accepted = [list(schema.FEATURE_COLUMNS), list(schema.FEATURE_ORDER)]
    if normalized not in accepted:
        raise ValidationError(
            "元数据中的特征顺序与系统约定不一致："
            f"文件为 {normalized}，系统要求 {list(schema.FEATURE_COLUMNS)}。"
        )


def _validate_dims(metadata: dict, hyperparams: dict) -> tuple[int, int, int]:
    input_length = int(hyperparams.get("input_length", metadata.get("input_length", schema.INPUT_LENGTH)))
    output_length = int(hyperparams.get("output_length", metadata.get("output_length", schema.OUTPUT_LENGTH)))
    feature_dim = int(hyperparams.get("feature_dim", metadata.get("feature_dim", schema.FEATURE_DIM)))
    if feature_dim != schema.FEATURE_DIM:
        raise ValidationError(
            f"模型特征维度必须为 {schema.FEATURE_DIM}（{'、'.join(schema.FEATURE_COLUMNS)}），当前为 {feature_dim}。"
        )
    if input_length != schema.INPUT_LENGTH:
        raise ValidationError(
            f"模型输入长度必须为 {schema.INPUT_LENGTH}（最近 {schema.INPUT_LENGTH * schema.TIME_STEP_HOURS} 小时），当前为 {input_length}。"
        )
    if output_length != schema.OUTPUT_LENGTH:
        raise ValidationError(
            f"模型输出长度必须为 {schema.OUTPUT_LENGTH}（最长 {schema.OUTPUT_LENGTH * schema.TIME_STEP_HOURS} 小时），当前为 {output_length}。"
        )
    return input_length, output_length, feature_dim


def _trial_load(weights_path: Path | None, hyperparams: dict, scaler_path: Path | None) -> None:
    """在隔离过程中试加载权重，失败则抛异常（不修改当前模型）。"""
    if weights_path is None:
        return
    if not torch_available():
        raise ValidationError(
            "服务器未安装 PyTorch，无法校验 .pt 权重。请安装 backend/requirements-ml.txt 后重试，"
            "或继续使用内置持续性基线。"
        )
    from app.ml.forecaster import build_transformer_forecaster

    try:  # 独立构造，不写入全局注册表，避免影响正在服务的旧模型
        build_transformer_forecaster(
            weights_path=weights_path,
            hyperparams=hyperparams,
            scaler_path=scaler_path,
            label="trial-load",
        )
    except Exception as exc:
        raise ValidationError(
            f"模型试加载失败，未启用该版本：{exc}。请确认权重文件与元数据中的模型结构一致。"
        ) from exc


def list_models(*, page: int = 1, page_size: int = 20):
    query = ModelVersion.query.order_by(ModelVersion.created_at.desc())
    total = query.count()
    return query.offset((page - 1) * page_size).limit(page_size).all(), total


def get_model(model_id: int) -> ModelVersion:
    record = db.session.get(ModelVersion, model_id)
    if record is None:
        raise NotFoundError(f"未找到编号为 {model_id} 的模型版本。")
    return record


def upload_model(
    actor: User,
    *,
    name: str,
    version: str,
    weights: bytes | None,
    weights_filename: str | None,
    metadata: bytes | None,
    metadata_filename: str | None,
    scaler: bytes | None = None,
    scaler_filename: str | None = None,
    activate: bool = False,
) -> UploadOutcome:
    from flask import current_app

    warnings: list[str] = []
    name = (name or "").strip() or "未命名模型"
    version = (version or "").strip() or utcnow().strftime("%Y%m%d%H%M")

    if ModelVersion.query.filter_by(name=name, version=version).first() is not None:
        raise ConflictError(f"模型 {name} {version} 已存在，请修改版本号后重新上传。")

    max_bytes = int(current_app.config.get("MAX_MODEL_MB", 200)) * 1024 * 1024
    for label, raw in (("权重文件", weights), ("元数据", metadata), ("标准化器", scaler)):
        if raw is not None and len(raw) > max_bytes:
            raise ValidationError(
                f"{label} 大小 {len(raw) / 1024 / 1024:.1f} MB 超过 {max_bytes // 1024 // 1024} MB 限制。"
            )

    metadata_payload, metadata_text = _parse_metadata(metadata, fallback_name=name)
    _validate_feature_order(metadata_payload)

    hyperparams = dict(metadata_payload.get("hyperparams") or {})
    metadata_payload.setdefault("hyperparams", hyperparams)
    input_length, output_length, feature_dim = _validate_dims(metadata_payload, hyperparams)

    if weights is None and metadata_payload.get("kind") != KIND_BASELINE:
        warnings.append("未上传权重文件，该版本将标记为不可用（仅登记元数据）。")

    if weights is None and metadata is None:
        raise ValidationError("至少需要上传模型权重文件(.pt) 或元数据文件(.json)。")

    weights_path: Path | None = None
    weights_checksum: str | None = None
    weights_suffix = normalize_stored_filename(weights_filename or ".pt")
    allowed_suffixes = [item.lower() for item in current_app.config.get("ALLOWED_MODEL_SUFFIXES", [".pt", ".pth"])]
    if weights is not None:
        if weights_suffix not in allowed_suffixes:
            raise ValidationError(f"权重文件只允许 {'、'.join(allowed_suffixes)} 格式，当前为 {weights_suffix}。")
        weights_path, weights_checksum = _save_bytes(weights, weights_filename or weights_suffix)

    metadata_path: Path | None = None
    if metadata_text is not None:
        metadata_path, _ = _save_bytes(metadata_text.encode("utf-8"), "metadata.json")

    scaler_path: Path | None = None
    scaler_payload = metadata_payload.get("scaler")
    if scaler is not None:
        try:
            parsed = json.loads(scaler.decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise ValidationError(f"标准化器必须是 UTF-8 编码的 JSON：{exc}") from None
        if not isinstance(parsed, dict) or "mean" not in parsed or "std" not in parsed:
            raise ValidationError("标准化器 JSON 必须包含 mean 与 std 两个数组。")
        scaler_path, _ = _save_bytes(scaler, "scaler.json")
    elif isinstance(scaler_payload, dict) and "mean" in scaler_payload and "std" in scaler_payload:
        scaler_path, _ = _save_bytes(
            json.dumps(scaler_payload, ensure_ascii=False).encode("utf-8"), "scaler.json"
        )
    elif weights is not None:
        warnings.append(
            "未提供标准化器参数：推理将使用原始量纲，预测精度可能下降。"
            "建议在训练脚本中导出 scaler.json 并在元数据中以 scaler 字段或独立文件上传。"
        )

    # 显式校验标准化器维度（不依赖试加载）
    if scaler_path is not None:
        try:
            from app.ml.preprocess import FeatureScaler

            FeatureScaler.load(scaler_path)
        except Exception as exc:
            raise ValidationError(f"标准化器格式不合法：{exc}") from exc

    _trial_load(weights_path, hyperparams, scaler_path)

    metadata_kind = str(metadata_payload.get("kind", "transformer" if weights is not None else KIND_BASELINE))
    record = ModelVersion(
        name=name,
        version=version,
        arch=str(hyperparams.get("arch", metadata_payload.get("arch", "transformer_encoder"))),
        kind=metadata_kind,
        weights_path=str(weights_path) if weights_path else None,
        scaler_path=str(scaler_path) if scaler_path else None,
        metadata_path=str(metadata_path) if metadata_path else None,
        file_size_bytes=len(weights) if weights is not None else None,
        checksum=weights_checksum,
        input_length=input_length,
        output_length=output_length,
        feature_dim=feature_dim,
        feature_order=",".join(schema.FEATURE_COLUMNS),
        hyperparams_json=json.dumps(hyperparams, ensure_ascii=False),
        metrics_json=json.dumps(metadata_payload.get("metrics"), ensure_ascii=False)
        if metadata_payload.get("metrics")
        else None,
        training_data_version=metadata_payload.get("training_data_version")
        or metadata_payload.get("data_version"),
        is_active=False,
        is_available=weights is not None or metadata_kind == KIND_BASELINE,
        validation_message="；".join(warnings) if warnings else "试加载通过。",
        uploaded_by=actor.id,
    )
    db.session.add(record)
    audit_service.record(
        audit_service.ACTION_MODEL_UPLOAD,
        actor=actor,
        target_type="model_version",
        target_id=f"{name} {version}",
        detail=f"kind={record.kind} available={record.is_available}",
    )
    db.session.commit()

    if activate and record.is_available:
        activate_model(actor, record.id)
        record = get_model(record.id)

    return UploadOutcome(record=record, warnings=warnings)


def activate_model(actor: User, model_id: int) -> ModelVersion:
    """启用指定模型版本；试加载失败不得替换当前模型。"""
    from app.ml.registry import get_registry

    record = get_model(model_id)
    if not record.is_available:
        raise ValidationError(
            f"模型 {record.name} {record.version} 当前不可用（{record.validation_message or '缺少权重文件'}），"
            "请先上传有效的权重与元数据。"
        )

    # 再次试加载，确认磁盘上的文件仍然有效（例如文件被清理或损坏）
    if record.kind != KIND_BASELINE and record.weights_path:
        hyperparams = json.loads(record.hyperparams_json) if record.hyperparams_json else {}
        weights_path = Path(record.weights_path)
        if not weights_path.exists():
            record.is_available = False
            record.validation_message = "权重文件不存在，请重新上传。"
            db.session.commit()
            raise ValidationError(f"模型文件 {weights_path} 不存在，已标记为不可用，当前模型保持不变。")
        try:
            _trial_load(
                weights_path,
                hyperparams,
                Path(record.scaler_path) if record.scaler_path else None,
            )
        except ValidationError:
            raise
        except Exception as exc:
            raise ValidationError(f"模型试加载失败，未切换当前模型：{exc}") from exc

    previous = ModelVersion.query.filter_by(is_active=True).first()
    ModelVersion.query.filter(ModelVersion.is_active.is_(True)).update(
        {ModelVersion.is_active: False}, synchronize_session=False
    )
    record.is_active = True
    audit_service.record(
        audit_service.ACTION_MODEL_ACTIVATE,
        actor=actor,
        target_type="model_version",
        target_id=record.id,
        detail=f"{record.name} {record.version}（原当前模型："
        f"{previous.name + ' ' + previous.version if previous else '无'}）",
    )
    db.session.commit()

    registry = get_registry()
    try:
        registry.load_active()
    except Exception as exc:  # pragma: no cover
        logger.error("切换后重新加载模型失败，继续使用上一个可用模型：%s", exc)
    logger.info("当前模型已切换为 %s %s", record.name, record.version)
    return record


def ensure_baseline_version(actor: User | None = None, *, activate: bool = True) -> ModelVersion:
    """登记内置持续性基线为一个模型版本。

    用途：在尚未训练出 Transformer 时，让“当前模型”字段有真实取值，
    使 NFR-012（任务记录模型版本）在阶段1/2 也能被验收。
    """
    record = ModelVersion.query.filter_by(kind=KIND_BASELINE, version=BASELINE_VERSION).first()
    created = record is None
    if record is None:
        record = ModelVersion(
            name=BASELINE_NAME,
            version=BASELINE_VERSION,
            arch="persistence",
            kind=KIND_BASELINE,
            input_length=schema.INPUT_LENGTH,
            output_length=schema.OUTPUT_LENGTH,
            feature_dim=schema.FEATURE_DIM,
            feature_order=",".join(schema.FEATURE_COLUMNS),
            hyperparams_json=json.dumps(
                {
                    "arch": "persistence",
                    "note": "规则基线：以最后一个观测点重复填充全部时效，用于打通系统闭环并作为模型评价对照。",
                },
                ensure_ascii=False,
            ),
            is_available=True,
            validation_message="内置规则基线，无需权重文件。",
            uploaded_by=getattr(actor, "id", None),
        )
        db.session.add(record)
        db.session.commit()
    if activate and not record.is_active:
        ModelVersion.query.filter(ModelVersion.is_active.is_(True)).update(
            {ModelVersion.is_active: False}, synchronize_session=False
        )
        record.is_active = True
        audit_service.record(
            ACTION_MODEL_REGISTER if created else ACTION_MODEL_ACTIVATE,
            actor=actor,
            target_type="model_version",
            target_id=record.id,
            detail=f"{record.name} {record.version}（内置规则基线）",
        )
        db.session.commit()
    return record


__all__ = [
    "BASELINE_NAME",
    "BASELINE_VERSION",
    "ACTION_MODEL_REGISTER",
    "UploadOutcome",
    "list_models",
    "get_model",
    "upload_model",
    "activate_model",
    "ensure_baseline_version",
]
