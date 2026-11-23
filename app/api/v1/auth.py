from fastapi import APIRouter, BackgroundTasks, Depends
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import rate_limit as limits
from app.api.cookies import clear_auth_cookies, set_auth_cookies
from app.api.rate_limit import rate_limit
from app.db.session import get_db
from app.repositories.email_verification_token import EmailVerificationTokenRepository
from app.repositories.password_reset_token import PasswordResetTokenRepository
from app.repositories.user import UserRepository
from app.schemas.auth import (
    ForgotPasswordRequest,
    LoginRequest,
    RefreshRequest,
    ResendVerificationRequest,
    ResetPasswordRequest,
    TokenResponse,
    VerifyEmailRequest,
)
from app.schemas.common import MessageResponse
from app.schemas.user import UserCreate, UserResponse
from app.services.auth import AuthService
from app.services.email import send_password_reset_email, send_verification_email
from app.services.email_verification import EmailVerificationService, build_verification_url
from app.services.password_reset import PasswordResetService, build_reset_url

router = APIRouter()


def _auth_service(db: AsyncSession = Depends(get_db)) -> AuthService:
    return AuthService(UserRepository(db))


def _password_reset_service(db: AsyncSession = Depends(get_db)) -> PasswordResetService:
    return PasswordResetService(UserRepository(db), PasswordResetTokenRepository(db))


def _email_verification_service(db: AsyncSession = Depends(get_db)) -> EmailVerificationService:
    return EmailVerificationService(UserRepository(db), EmailVerificationTokenRepository(db))


@router.post(
    "/register",
    response_model=UserResponse,
    status_code=201,
    responses={429: limits.TOO_MANY_REQUESTS},
    dependencies=[Depends(rate_limit(limits.REGISTER))],
)  # type: ignore[misc]
async def register(
    data: UserCreate,
    background_tasks: BackgroundTasks,
    svc: AuthService = Depends(_auth_service),
    verification: EmailVerificationService = Depends(_email_verification_service),
) -> UserResponse:
    """Create an account.

    When email verification is required (`is_verified` is false in the response),
    a confirmation link is sent to the email and the account can log in after opening it.
    """
    user = await svc.register(data)
    if not user.is_verified:
        token = await verification.create_link_token(user)
        background_tasks.add_task(
            send_verification_email, user.email, build_verification_url(token)
        )
    return UserResponse.model_validate(user)


@router.post(
    "/login",
    response_model=TokenResponse,
    responses={
        200: {
            "description": "User logged in. Tokens returned in body AND set as HTTP-only cookies."
        },
        401: {"description": "Invalid credentials"},
        403: {
            "description": "Email address not confirmed yet "
            '(`code` is "email_not_verified"); a new link can be requested'
        },
        429: limits.TOO_MANY_REQUESTS,
    },
    dependencies=[Depends(rate_limit(limits.LOGIN))],
)  # type: ignore[misc]
async def login(data: LoginRequest, svc: AuthService = Depends(_auth_service)) -> Response:
    """Login — returns tokens in JSON body and sets secure HTTP-only cookies.

    Use the returned `access_token` as `Authorization: Bearer <token>` in Swagger UI,
    or rely on the cookies that are automatically set for browser clients.
    """
    _user, tokens = await svc.login(data)

    response = JSONResponse(
        content={
            "access_token": tokens.access_token,
            "refresh_token": tokens.refresh_token,
            "token_type": "bearer",
        }
    )

    set_auth_cookies(response, tokens)

    return response


@router.post(
    "/refresh",
    status_code=204,
    responses={
        204: {
            "description": "Tokens refreshed. New tokens set in secure HTTP-only cookies "
            "(access_token with SameSite=Lax, refresh_token with SameSite=Strict)."
        },
        401: {"description": "Invalid or expired refresh token"},
    },
)  # type: ignore[misc]
async def refresh(data: RefreshRequest, svc: AuthService = Depends(_auth_service)) -> Response:
    """Refresh tokens and set secure cookies.

    Takes refresh_token (from cookie or request body) and returns new tokens in secure cookies.
    """
    _user, tokens = await svc.refresh(data.refresh_token)

    response = Response(status_code=204)

    set_auth_cookies(response, tokens)

    return response


@router.post("/logout", status_code=204)  # type: ignore[misc]
async def logout() -> Response:
    """Logout by clearing secure cookies."""
    response = Response(status_code=204)
    clear_auth_cookies(response)
    return response


@router.post(
    "/forgot-password",
    response_model=MessageResponse,
    status_code=202,
    responses={429: limits.TOO_MANY_REQUESTS},
    dependencies=[Depends(rate_limit(limits.FORGOT_PASSWORD))],
)  # type: ignore[misc]
async def forgot_password(
    data: ForgotPasswordRequest,
    background_tasks: BackgroundTasks,
    svc: PasswordResetService = Depends(_password_reset_service),
) -> MessageResponse:
    """Send a password reset link to the given email.

    The response is the same whether or not an account with this email exists,
    so the endpoint cannot be used to check which emails are registered.
    """
    result = await svc.request_reset(data.email)
    if result:
        user, token = result
        background_tasks.add_task(send_password_reset_email, user.email, build_reset_url(token))

    return MessageResponse(
        message="If an account with this email exists, a password reset link has been sent."
    )


@router.post(
    "/verify-email",
    status_code=204,
    responses={
        400: {"description": "Verification link is invalid, already used or expired"},
        429: limits.TOO_MANY_REQUESTS,
    },
    dependencies=[Depends(rate_limit(limits.VERIFY_EMAIL))],
)  # type: ignore[misc]
async def verify_email(
    data: VerifyEmailRequest,
    svc: EmailVerificationService = Depends(_email_verification_service),
) -> Response:
    """Confirm the email of an account with the token from the verification link."""
    await svc.verify(data.token)
    return Response(status_code=204)


@router.post(
    "/resend-verification",
    response_model=MessageResponse,
    status_code=202,
    responses={429: limits.TOO_MANY_REQUESTS},
    dependencies=[Depends(rate_limit(limits.RESEND_VERIFICATION))],
)  # type: ignore[misc]
async def resend_verification(
    data: ResendVerificationRequest,
    background_tasks: BackgroundTasks,
    svc: EmailVerificationService = Depends(_email_verification_service),
) -> MessageResponse:
    """Send a new verification link to an account that has not confirmed its email.

    The response is the same for every email, so it cannot be used to check
    which emails are registered or verified.
    """
    result = await svc.request_new_link(data.email)
    if result:
        user, token = result
        background_tasks.add_task(
            send_verification_email, user.email, build_verification_url(token)
        )

    return MessageResponse(
        message="If this account still needs to be verified, a new link has been sent."
    )


@router.post(
    "/reset-password",
    status_code=204,
    responses={
        400: {"description": "Reset link is invalid, already used or expired"},
        429: limits.TOO_MANY_REQUESTS,
    },
    dependencies=[Depends(rate_limit(limits.RESET_PASSWORD))],
)  # type: ignore[misc]
async def reset_password(
    data: ResetPasswordRequest,
    svc: PasswordResetService = Depends(_password_reset_service),
) -> Response:
    """Set a new password using the token from the reset link.

    The link works once. All existing sessions of the user stop working,
    and the session cookies of this browser are cleared.
    """
    await svc.reset_password(data.token, data.new_password)

    response = Response(status_code=204)
    clear_auth_cookies(response)
    return response
