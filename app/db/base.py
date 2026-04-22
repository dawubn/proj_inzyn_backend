from app.db.session import Base  # noqa: F401 — imported to register models with metadata

# Import all models here so Alembic can detect them during autogenerate
from app.models.user import User  # noqa: F401
from app.models.document import Document  # noqa: F401
from app.models.document_analysis import DocumentAnalysis  # noqa: F401
from app.models.validation_profile import ValidationProfile  # noqa: F401
from app.models.validation_rule import ValidationRule  # noqa: F401
from app.models.analysis_report import AnalysisReport  # noqa: F401
