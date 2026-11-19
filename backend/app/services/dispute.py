from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status
from PIL import Image
from pillow_heif import register_heif_opener
from sqlalchemy.orm import Session

from app.models.dispute import DisputePhoto, DisputeRecord
from app.models.work_order import WorkOrderStatus
from app.repositories.dispute import (
    get_dispute_by_number,
    get_photo_by_number,
    list_disputes_for_order,
    list_photos_for_dispute,
)
from app.schemas.dispute import (
    DisputeListResponse,
    DisputePhotoListResponse,
    DisputePhotoResponse,
    DisputeResponse,
    DisputeWrite,
)
from app.services.work_order import require_work_order

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_PHOTO_ROOT = _PROJECT_ROOT / "data" / "dispute_photos"

_MAX_PHOTO_BYTES = 10 * 1024 * 1024
_MAX_PHOTO_PIXELS = 40_000_000
_PHOTO_FORMATS = {
    "JPEG": (".jpg", "image/jpeg"),
    "PNG": (".png", "image/png"),
    "WEBP": (".webp", "image/webp"),
    "HEIF": (".heic", "image/heif"),
    "GIF": (".gif", "image/gif"),
    "BMP": (".bmp", "image/bmp"),
    "TIFF": (".tif", "image/tiff"),
    "AVIF": (".avif", "image/avif"),
}

register_heif_opener()


def _photo_format(source: Path | BinaryIO, *, validate_content: bool = False) -> tuple[str, str]:
    with Image.open(source, formats=list(_PHOTO_FORMATS)) as image:
        if image.width * image.height > _MAX_PHOTO_PIXELS:
            raise ValueError("Image exceeds the pixel limit")
        image_format = _PHOTO_FORMATS.get(image.format or "")
        if image_format is None:
            raise ValueError("Unsupported image format")
        if validate_content:
            image.load()
        return image_format


def ensure_disputes_editable(order) -> None:
    if order.status == WorkOrderStatus.ISSUED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=("Нельзя изменять спорные ситуации в выданном заказ-наряде."),
        )


def require_order_dispute(
    db: Session,
    *,
    order,
    dispute_number: int,
) -> DisputeRecord:
    dispute = get_dispute_by_number(
        db,
        dispute_number,
    )

    if dispute is None or dispute.work_order_id != order.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=("Спорная ситуация в этом заказ-наряде не найдена."),
        )

    return dispute


def require_dispute_photo(
    db: Session,
    *,
    dispute: DisputeRecord,
    photo_number: int,
) -> DisputePhoto:
    photo = get_photo_by_number(
        db,
        photo_number,
    )

    if photo is None or photo.dispute_id != dispute.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Фотография не найдена.",
        )

    return photo


def create_dispute(
    db: Session,
    *,
    work_order_number: int,
    payload: DisputeWrite,
) -> DisputeResponse:
    order = require_work_order(
        db,
        work_order_number,
    )

    ensure_disputes_editable(order)

    dispute = DisputeRecord(
        work_order_id=order.id,
        found_text=payload.found_text,
        master_recommendation=payload.master_recommendation,
        client_response=payload.client_response,
    )

    db.add(dispute)
    db.commit()
    db.refresh(dispute)

    return DisputeResponse.model_validate(dispute)


def update_dispute(
    db: Session,
    *,
    work_order_number: int,
    dispute_number: int,
    payload: DisputeWrite,
) -> DisputeResponse:
    order = require_work_order(
        db,
        work_order_number,
    )

    ensure_disputes_editable(order)

    dispute = require_order_dispute(
        db,
        order=order,
        dispute_number=dispute_number,
    )

    dispute.found_text = payload.found_text
    dispute.master_recommendation = payload.master_recommendation
    dispute.client_response = payload.client_response

    db.commit()
    db.refresh(dispute)

    return DisputeResponse.model_validate(dispute)


def delete_dispute(
    db: Session,
    *,
    work_order_number: int,
    dispute_number: int,
) -> None:
    order = require_work_order(
        db,
        work_order_number,
    )

    ensure_disputes_editable(order)

    dispute = require_order_dispute(
        db,
        order=order,
        dispute_number=dispute_number,
    )

    dispute.deleted_at = datetime.now(UTC)

    db.commit()


def get_order_disputes(
    db: Session,
    work_order_number: int,
) -> DisputeListResponse:
    order = require_work_order(
        db,
        work_order_number,
    )

    disputes = list_disputes_for_order(
        db,
        order.id,
    )

    return DisputeListResponse(
        items=[DisputeResponse.model_validate(item) for item in disputes],
        total=len(disputes),
    )


def add_dispute_photo(
    db: Session,
    *,
    work_order_number: int,
    dispute_number: int,
    upload: UploadFile,
) -> DisputePhotoResponse:
    order = require_work_order(
        db,
        work_order_number,
    )

    ensure_disputes_editable(order)

    dispute = require_order_dispute(
        db,
        order=order,
        dispute_number=dispute_number,
    )

    original_filename = Path((upload.filename or "photo").replace("\\", "/")).name[:500]

    with upload.file:
        content = upload.file.read(_MAX_PHOTO_BYTES + 1)

    if len(content) > _MAX_PHOTO_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail="Максимальный размер фотографии — 10 МБ.",
        )

    try:
        suffix, content_type = _photo_format(BytesIO(content), validate_content=True)
    except (Image.DecompressionBombError, OSError, SyntaxError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Откройте фотографию и сохраните её в поддерживаемом формате изображения.",
        ) from exc

    target_dir = _PHOTO_ROOT / str(work_order_number) / str(dispute_number)
    target_dir.mkdir(parents=True, exist_ok=True)
    stored_path = target_dir / f"{uuid4().hex}{suffix}"

    try:
        stored_path.write_bytes(content)
        photo = DisputePhoto(
            dispute_id=dispute.id,
            original_filename=original_filename,
            stored_relative_path=stored_path.relative_to(_PROJECT_ROOT).as_posix(),
            content_type=content_type,
            size_bytes=len(content),
        )
        db.add(photo)
        db.flush()
        response = DisputePhotoResponse.model_validate(photo)
        db.commit()
    except Exception:
        db.rollback()
        stored_path.unlink(missing_ok=True)
        raise

    return response


def get_dispute_photos(
    db: Session,
    *,
    work_order_number: int,
    dispute_number: int,
) -> DisputePhotoListResponse:
    order = require_work_order(
        db,
        work_order_number,
    )

    dispute = require_order_dispute(
        db,
        order=order,
        dispute_number=dispute_number,
    )

    photos = list_photos_for_dispute(
        db,
        dispute.id,
    )

    return DisputePhotoListResponse(
        items=[DisputePhotoResponse.model_validate(photo) for photo in photos],
        total=len(photos),
    )


def get_dispute_photo_file(
    db: Session,
    *,
    work_order_number: int,
    dispute_number: int,
    photo_number: int,
) -> tuple[Path, str]:
    order = require_work_order(
        db,
        work_order_number,
    )

    dispute = require_order_dispute(
        db,
        order=order,
        dispute_number=dispute_number,
    )

    photo = require_dispute_photo(
        db,
        dispute=dispute,
        photo_number=photo_number,
    )

    path = (_PROJECT_ROOT / photo.stored_relative_path).resolve()

    photo_root = _PHOTO_ROOT.resolve()

    try:
        path.relative_to(photo_root)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Файл фотографии не найден.",
        ) from exc

    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Файл фотографии не найден.",
        )

    try:
        _, content_type = _photo_format(path)
    except (Image.DecompressionBombError, OSError, SyntaxError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Файл фотографии имеет неподдерживаемый формат.",
        ) from exc

    return path, content_type


def delete_dispute_photo(
    db: Session,
    *,
    work_order_number: int,
    dispute_number: int,
    photo_number: int,
) -> None:
    order = require_work_order(
        db,
        work_order_number,
    )

    ensure_disputes_editable(order)

    dispute = require_order_dispute(
        db,
        order=order,
        dispute_number=dispute_number,
    )

    photo = require_dispute_photo(
        db,
        dispute=dispute,
        photo_number=photo_number,
    )

    # Запись скрываем из рабочего интерфейса.
    # Сам файл физически оставляем для будущей истории изменений.
    photo.deleted_at = datetime.now(UTC)

    db.commit()
