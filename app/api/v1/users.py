from fastapi import APIRouter, Depends

from app.api.dependencies.auth import get_current_user, require_admin
from app.models.user import User
from app.schemas.user import UserResponse

router = APIRouter()


@router.get("/me", response_model=UserResponse)  # type: ignore[misc]
async def get_me(current_user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse.model_validate(current_user)


@router.get("/{user_id}", response_model=UserResponse, dependencies=[Depends(require_admin)])  # type: ignore[misc]
async def get_user(user_id: str, _: User = Depends(require_admin)) -> UserResponse:
    # TODO: implement via UserService
    raise NotImplementedError
