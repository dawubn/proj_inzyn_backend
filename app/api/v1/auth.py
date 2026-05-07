from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.repositories.user import UserRepository
from app.schemas.auth import LoginRequest, RefreshRequest, TokenResponse
from app.schemas.user import UserCreate, UserResponse
from app.services.auth import AuthService

router = APIRouter()


def _auth_service(db: AsyncSession = Depends(get_db)) -> AuthService:
    return AuthService(UserRepository(db))


@router.post("/register", response_model=UserResponse, status_code=201)
async def register(data: UserCreate, svc: AuthService = Depends(_auth_service)) -> UserResponse:
    user = await svc.register(data)
    return UserResponse.model_validate(user)


@router.post("/login", response_model=TokenResponse)
async def login(data: LoginRequest, svc: AuthService = Depends(_auth_service)) -> TokenResponse:
    """
    Logowanie użytkownika.

    W trybie development (APP_ENV=development, APP_DEBUG=true) endpoint najpierw
    sprawdza plik storage/mock_users.json. Jeśli email istnieje w pliku:
      - weryfikuje hasło plain-text z pliku
      - generuje prawdziwy JWT token (ten sam mechanizm co produkcja)
      - NIE odpytuje bazy danych
    Jeśli emaila nie ma w pliku — fallback do normalnej logiki z bazą danych.

    Plik mock_users.json ma strukturę:
    [
      {
        "id": "uuid",
        "email": "admin@example.com",
        "password": "plain_text_password",
        "full_name": "Admin User",
        "role": "admin",
        "is_active": true,
        "is_verified": true
      }
    ]
    """
    if settings.APP_ENV == "development" and settings.APP_DEBUG:
        from app.core.mock_users import MOCK_USERS
        from app.core.exceptions import UnauthorizedError
        from app.core.security import create_access_token, create_refresh_token

        entry = MOCK_USERS.get(data.email)
        if entry is not None:
            user, plain_password = entry
            if data.password != plain_password:
                raise UnauthorizedError("Invalid credentials")
            return TokenResponse(
                access_token=create_access_token(str(user.id), extra_claims={"role": user.role}),
                refresh_token=create_refresh_token(str(user.id)),
                token_type="bearer",
            )

    return await svc.login(data)


@router.post("/refresh", response_model=TokenResponse)
async def refresh(data: RefreshRequest, svc: AuthService = Depends(_auth_service)) -> TokenResponse:
    return await svc.refresh(data.refresh_token)
