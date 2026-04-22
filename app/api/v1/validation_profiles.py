from fastapi import APIRouter, Depends, status

from app.api.dependencies.auth import require_admin
from app.models.user import User
from app.schemas.validation_profile import ValidationProfileCreate, ValidationProfileResponse

router = APIRouter()


@router.post("", response_model=ValidationProfileResponse, status_code=status.HTTP_201_CREATED)
async def create_profile(
    data: ValidationProfileCreate,
    _: User = Depends(require_admin),
) -> ValidationProfileResponse:
    # TODO: implement via ValidationProfileService
    raise NotImplementedError


@router.get("", response_model=list[ValidationProfileResponse])
async def list_profiles(_: User = Depends(require_admin)) -> list[ValidationProfileResponse]:
    # TODO: implement via ValidationProfileService
    raise NotImplementedError
