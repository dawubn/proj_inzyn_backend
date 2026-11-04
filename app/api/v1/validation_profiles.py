from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import require_admin
from app.db.session import get_db
from app.models.user import User
from app.repositories.validation_profile import ValidationProfileRepository
from app.schemas.validation_profile import ValidationProfileCreate, ValidationProfileResponse
from app.services.validation_profile import ValidationProfileService

router = APIRouter()


def _validation_profile_service(db: AsyncSession = Depends(get_db)) -> ValidationProfileService:
    return ValidationProfileService(ValidationProfileRepository(db))


@router.post(  # type: ignore[misc]
    "",
    response_model=ValidationProfileResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_profile(
    data: ValidationProfileCreate,
    _: User = Depends(require_admin),
    svc: ValidationProfileService = Depends(_validation_profile_service),
) -> ValidationProfileResponse:
    profile = await svc.create_profile(data)
    return ValidationProfileResponse.model_validate(profile)


@router.get("", response_model=list[ValidationProfileResponse])  # type: ignore[misc]
async def list_profiles(
    _: User = Depends(require_admin),
    svc: ValidationProfileService = Depends(_validation_profile_service),
) -> list[ValidationProfileResponse]:
    profiles = await svc.list_profiles()
    return [ValidationProfileResponse.model_validate(profile) for profile in profiles]
