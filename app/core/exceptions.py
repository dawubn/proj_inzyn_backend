from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse


class AppError(Exception):
    status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR
    detail: str = "Internal server error"
    # Stable name of the error for clients that react to it, sent next to the detail
    code: str | None = None

    def __init__(self, detail: str | None = None) -> None:
        self.detail = detail or self.__class__.detail
        self.headers: dict[str, str] | None = None


class NotFoundError(AppError):
    status_code = status.HTTP_404_NOT_FOUND
    detail = "Resource not found"


class UnauthorizedError(AppError):
    status_code = status.HTTP_401_UNAUTHORIZED
    detail = "Authentication required"


class ForbiddenError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    detail = "Insufficient permissions"


class ConflictError(AppError):
    status_code = status.HTTP_409_CONFLICT
    detail = "Resource already exists"


class InvalidResetTokenError(AppError):
    status_code = status.HTTP_400_BAD_REQUEST
    detail = "Password reset link is invalid or has expired"


class InvalidVerificationTokenError(AppError):
    status_code = status.HTTP_400_BAD_REQUEST
    detail = "Verification link is invalid or has expired"


class EmailNotVerifiedError(AppError):
    status_code = status.HTTP_403_FORBIDDEN
    detail = "Email address is not verified"
    code = "email_not_verified"


class TooManyRequestsError(AppError):
    status_code = status.HTTP_429_TOO_MANY_REQUESTS
    detail = "Too many requests. Try again later."
    code = "too_many_requests"

    def __init__(self, retry_after_seconds: int) -> None:
        super().__init__()
        self.headers = {"Retry-After": str(retry_after_seconds)}


class InvalidPasswordError(AppError):
    status_code = status.HTTP_400_BAD_REQUEST
    detail = "Current password is incorrect"


class ValidationError(AppError):
    status_code = status.HTTP_422_UNPROCESSABLE_CONTENT
    detail = "Validation failed"


class FileTooLargeError(AppError):
    status_code = status.HTTP_413_CONTENT_TOO_LARGE
    detail = "Uploaded file exceeds size limit"


class UnsupportedFileTypeError(AppError):
    status_code = status.HTTP_415_UNSUPPORTED_MEDIA_TYPE
    detail = "File type not supported"


class OCRServiceError(AppError):
    status_code = status.HTTP_502_BAD_GATEWAY
    detail = "OCR service unavailable"


class ProcessingTimeoutError(AppError):
    status_code = status.HTTP_408_REQUEST_TIMEOUT
    detail = "Processing timed out"


class ClassifierNotReadyError(AppError):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    detail = "Classifier model not loaded"


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)  # type: ignore[misc]
    async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
        content = {"detail": exc.detail}
        if exc.code:
            content["code"] = exc.code
        return JSONResponse(status_code=exc.status_code, content=content, headers=exc.headers)
