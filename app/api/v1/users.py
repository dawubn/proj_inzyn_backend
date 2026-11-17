from fastapi import APIRouter, Depends, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.cookies import set_auth_cookies
from app.api.dependencies.auth import get_current_user, require_admin
from app.db.session import get_db
from app.models.user import User
from app.repositories.password_reset_token import PasswordResetTokenRepository
from app.repositories.user import UserRepository
from app.schemas.auth import ChangePasswordRequest
from app.schemas.user import UserProfileUpdate, UserResponse
from app.services.auth import AuthService
from app.services.password_reset import PasswordResetService

router = APIRouter()


@router.get("/me", response_model=UserResponse)  # type: ignore[misc]
async def get_me(current_user: User = Depends(get_current_user)) -> UserResponse:
    return UserResponse.model_validate(current_user)


@router.patch("/me", response_model=UserResponse)  # type: ignore[misc]
async def update_me(
    data: UserProfileUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UserResponse:
    """Update the profile of the logged-in user. Only the fields sent are changed."""
    for field, value in data.model_dump(exclude_unset=True).items():
        setattr(current_user, field, value)

    user = await UserRepository(db).update(current_user)
    return UserResponse.model_validate(user)


@router.post(
    "/me/password",
    status_code=204,
    responses={400: {"description": "Current password is incorrect"}},
)  # type: ignore[misc]
async def change_my_password(
    data: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """Change the password of the logged-in user.

    All other sessions of the user stop working. This session continues:
    new tokens are set in the cookies.
    """
    service = PasswordResetService(UserRepository(db), PasswordResetTokenRepository(db))
    user = await service.change_password(current_user, data.current_password, data.new_password)

    response = Response(status_code=204)
    set_auth_cookies(response, AuthService.issue_tokens(user))
    return response


@router.get("/{user_id}", response_model=UserResponse, dependencies=[Depends(require_admin)])  # type: ignore[misc]
async def get_user(user_id: str, _: User = Depends(require_admin)) -> UserResponse:
    # TODO: implement via UserService
    raise NotImplementedError
