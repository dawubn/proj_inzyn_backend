from fastapi import APIRouter

from app.api.v1 import analyses, auth, classifications, documents, redactions, reports, users, validation_profiles

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["auth"])
api_router.include_router(users.router, prefix="/users", tags=["users"])
api_router.include_router(documents.router, prefix="/documents", tags=["documents"])
api_router.include_router(
    analyses.router,
    prefix="/documents/{document_id}/analyses",
    tags=["analyses"],
)
api_router.include_router(
    validation_profiles.router,
    prefix="/validation-profiles",
    tags=["validation-profiles"],
)
api_router.include_router(reports.router, prefix="/reports", tags=["reports"])
api_router.include_router(redactions.router, prefix="/redactions", tags=["redactions"])
api_router.include_router(classifications.router, prefix="/classify", tags=["classifications"])
