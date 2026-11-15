from pydantic import BaseModel, EmailStr, Field, field_validator

# bcrypt only uses the first 72 bytes of a password
_MAX_PASSWORD_BYTES = 72


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"  # noqa: S105


class RefreshRequest(BaseModel):
    refresh_token: str


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class _NewPassword(BaseModel):
    new_password: str = Field(min_length=8)

    @field_validator("new_password")
    @classmethod
    def _fits_bcrypt(cls, value: str) -> str:
        if len(value.encode()) > _MAX_PASSWORD_BYTES:
            raise ValueError(f"must be at most {_MAX_PASSWORD_BYTES} bytes long")
        return value


class ResetPasswordRequest(_NewPassword):
    token: str = Field(min_length=1, max_length=255)


class ChangePasswordRequest(_NewPassword):
    current_password: str = Field(min_length=1)
