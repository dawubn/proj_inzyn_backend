from celery import Celery

from app.core.config import settings

celery_app = Celery(
    "cerber_doc",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
    include=[
        "app.tasks.local_ocr",
        "app.tasks.azure_ocr",
        "app.tasks.analysis",
        "app.tasks.full_ocr",
        "app.tasks.legal_analysis",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_routes={
        "app.tasks.local_ocr.*": {"queue": "ocr"},
        "app.tasks.azure_ocr.*": {"queue": "ocr"},
        "app.tasks.analysis.*": {"queue": "analysis"},
        "app.tasks.full_ocr.*": {"queue": "ocr"},
        "app.tasks.legal_analysis.*": {"queue": "ocr"},
    },
)
