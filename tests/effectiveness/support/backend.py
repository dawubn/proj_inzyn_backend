"""HTTP client for executing the effectiveness benchmark."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from .models import (
    AnalysisResult,
    GeneratedCase,
    JsonObject,
    LogEvent,
    RunConfig,
    RunPaths,
    utc_now,
)

EventHandler = Callable[[LogEvent], None]


class BackendError(RuntimeError):
    """Backend request or processing failure."""


class AnalysisTimeoutError(BackendError):
    """Analysis did not finish before the configured deadline."""


class BackendClient:
    def __init__(
        self,
        config: RunConfig,
        *,
        access_token: str | None = None,
        email: str | None = None,
        password: str | None = None,
        on_event: EventHandler | None = None,
    ) -> None:
        self.config = config
        self._access_token = access_token
        self._email = email
        self._password = password
        self._on_event = on_event
        self._authenticated = False

        self._client = httpx.Client(
            base_url=config.backend_url.rstrip("/"),
            timeout=120.0,
            trust_env=False,
            follow_redirects=False,
        )

    def __enter__(self) -> BackendClient:
        return self

    def __exit__(
        self,
        exc_type: Any,
        exc_value: Any,
        traceback: Any,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _emit(
        self,
        stage: str,
        message: str,
        *,
        case_id: str | None = None,
        level: str = "INFO",
        details: JsonObject | None = None,
    ) -> None:
        if self._on_event is not None:
            self._on_event(
                LogEvent(
                    stage=stage,
                    message=message,
                    level=level,
                    case_id=case_id,
                    details=details or {},
                )
            )

    def _request(
        self,
        method: str,
        path: str,
        *,
        case_id: str | None = None,
        quiet: bool = False,
        **kwargs: Any,
    ) -> httpx.Response:
        started = time.monotonic()

        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.RequestError as exc:
            self._emit(
                "http",
                "Backend request failed.",
                case_id=case_id,
                level="ERROR",
                details={
                    "method": method,
                    "path": path,
                    "error_type": type(exc).__name__,
                },
            )
            raise BackendError(f"{method} {path}: {type(exc).__name__}.") from None

        elapsed = round(time.monotonic() - started, 3)

        if not quiet or not response.is_success:
            self._emit(
                "http",
                f"{method} {path}: HTTP {response.status_code}.",
                case_id=case_id,
                level="INFO" if response.is_success else "ERROR",
                details={
                    "status_code": response.status_code,
                    "elapsed_seconds": elapsed,
                },
            )

        if not response.is_success:
            raise BackendError(f"{method} {path}: HTTP {response.status_code}.")

        return response

    @staticmethod
    def _json_object(response: httpx.Response) -> JsonObject:
        try:
            payload = response.json()
        except ValueError:
            raise BackendError("Backend response is not valid JSON.") from None

        if not isinstance(payload, dict):
            raise BackendError("Backend response must be a JSON object.")

        return payload

    @staticmethod
    def _required_id(payload: JsonObject, key: str) -> str:
        value = payload.get(key)

        if not isinstance(value, str) or not value.strip():
            raise BackendError(f"Backend response has no valid {key!r}.")

        return value

    def check_health(self) -> None:
        self._request("GET", "/health", timeout=10.0)

    def authenticate(self) -> None:
        if self._authenticated:
            return

        if self._access_token:
            self._client.headers["Authorization"] = f"Bearer {self._access_token}"
            self._authenticated = True
            self._emit("authentication", "Access token configured.")
            return

        if not self._email or self._password is None:
            raise BackendError("Provide an access token or test account credentials.")

        response = self._request(
            "POST",
            "/api/v1/auth/login",
            json={
                "email": self._email,
                "password": self._password,
            },
        )
        payload = self._json_object(response)
        token = payload.get("access_token")

        if not isinstance(token, str) or not token:
            raise BackendError("Authentication response has no access token.")

        self._client.headers["Authorization"] = f"Bearer {token}"
        self._authenticated = True
        self._emit("authentication", "Authentication completed.")

    def upload_document(
        self,
        case: GeneratedCase,
        documents_directory: Path,
    ) -> str:
        path = documents_directory / case.filename

        if not path.is_file():
            raise FileNotFoundError(f"Generated document does not exist: {path}")

        with path.open("rb") as stream:
            response = self._request(
                "POST",
                "/api/v1/documents",
                case_id=case.case_id,
                data={"document_type": case.document_type},
                files={
                    "file": (path.name, stream, "application/pdf"),
                },
            )

        return self._required_id(
            self._json_object(response),
            "id",
        )

    def trigger_analysis(
        self,
        document_id: str,
        *,
        case_id: str,
    ) -> str:
        response = self._request(
            "POST",
            "/api/v1/redactions/legal-analysis",
            case_id=case_id,
            params={"document_id": document_id},
        )

        return self._required_id(
            self._json_object(response),
            "analysis_id",
        )

    def wait_for_analysis(
        self,
        result: AnalysisResult,
    ) -> JsonObject:
        if result.analysis_id is None:
            raise BackendError("Cannot poll without an analysis ID.")

        deadline = time.monotonic() + self.config.timeout_seconds
        previous_state: tuple[str, str, Any] | None = None
        last_event_at = 0.0

        while True:
            remaining = deadline - time.monotonic()

            if remaining <= 0:
                raise AnalysisTimeoutError(
                    f"Analysis {result.analysis_id} exceeded "
                    f"{self.config.timeout_seconds:g} seconds. "
                    "It may still be running on the backend."
                )

            response = self._request(
                "GET",
                f"/api/v1/redactions/{result.analysis_id}",
                case_id=result.case_id,
                quiet=True,
                timeout=min(120.0, remaining),
            )
            payload = self._json_object(response)
            result.analysis_payload = payload

            status = str(payload.get("status") or "unknown").lower()
            stage = str(payload.get("processing_stage") or "unknown")
            step = payload.get("processing_step")
            state = (status, stage, step)

            now = time.monotonic()

            if state != previous_state or now - last_event_at >= 15:
                self._emit(
                    "processing",
                    f"Analysis status: {status}; stage: {stage}.",
                    case_id=result.case_id,
                    details={
                        "status": status,
                        "processing_stage": stage,
                        "processing_step": step,
                    },
                )
                previous_state = state
                last_event_at = now

            if status == "completed":
                return payload

            if status in {"failed", "error", "ocr_failed", "cancelled"}:
                raise BackendError(f"Analysis ended with status {status!r}.")

            remaining = deadline - time.monotonic()

            if remaining > 0:
                time.sleep(min(self.config.poll_interval_seconds, remaining))

    def get_report(
        self,
        analysis_id: str,
        *,
        case_id: str,
    ) -> JsonObject:
        response = self._request(
            "GET",
            f"/api/v1/reports/{analysis_id}",
            case_id=case_id,
        )

        return self._json_object(response)

    def download_report_pdf(
        self,
        analysis_id: str,
        destination: Path,
        *,
        case_id: str,
    ) -> None:
        response = self._request(
            "GET",
            f"/api/v1/reports/{analysis_id}/pdf",
            case_id=case_id,
        )

        content = response.content
        content_type = response.headers.get("content-type", "")
        media_type = content_type.split(";", 1)[0].strip().lower()

        if media_type != "application/pdf":
            raise BackendError("Report export did not return application/pdf.")

        if not content.startswith(b"%PDF-"):
            raise BackendError("Report export has no valid PDF header.")

        destination.parent.mkdir(parents=True, exist_ok=True)

        if destination.exists():
            raise FileExistsError(f"Refusing to overwrite report: {destination}")

        temporary = destination.with_suffix(".pdf.tmp")

        try:
            temporary.write_bytes(content)
            temporary.replace(destination)
        finally:
            temporary.unlink(missing_ok=True)

    def process_case(
        self,
        case: GeneratedCase,
        paths: RunPaths,
    ) -> AnalysisResult:
        """Execute one analysis and collect its report.

        Processing failures are returned in the result.
        KeyboardInterrupt is deliberately allowed to propagate.
        """

        started = time.monotonic()

        result = AnalysisResult(
            case_id=case.case_id,
            document_type=case.document_type,
        )

        self._emit(
            "document",
            "Document processing started.",
            case_id=case.case_id,
            details={"document_type": case.document_type},
        )

        try:
            result.stage = "authentication"
            self.authenticate()

            result.stage = "upload"
            result.document_id = self.upload_document(
                case,
                paths.documents,
            )

            result.stage = "trigger"
            result.analysis_id = self.trigger_analysis(
                result.document_id,
                case_id=case.case_id,
            )

            result.stage = "processing"
            self.wait_for_analysis(result)
            result.processed = True

            result.stage = "report_json"
            result.report_payload = self.get_report(
                result.analysis_id,
                case_id=case.case_id,
            )

            result.stage = "report_pdf"
            pdf_path = paths.reports / f"{case.case_id}.pdf"

            try:
                self.download_report_pdf(
                    result.analysis_id,
                    pdf_path,
                    case_id=case.case_id,
                )
                result.report_pdf_path = pdf_path
                result.pdf_downloaded = True
            except Exception as exc:
                result.export_error = f"{type(exc).__name__}: {exc}"
                self._emit(
                    "report_pdf",
                    "PDF export failed.",
                    case_id=case.case_id,
                    level="ERROR",
                    details={"error": result.export_error},
                )

            result.stage = "finished"

        except Exception as exc:
            result.error = f"{result.stage}: {type(exc).__name__}: {exc}"

            self._emit(
                result.stage,
                "Document processing failed.",
                case_id=case.case_id,
                level="ERROR",
                details={
                    "error": result.error,
                    "stop_submissions": self.must_stop(result),
                },
            )

        finally:
            result.finished_at = utc_now()
            result.elapsed_seconds = round(
                time.monotonic() - started,
                3,
            )

        return result

    @staticmethod
    def must_stop(result: AnalysisResult) -> bool:
        """Avoid further submissions after ambiguous processing failures."""

        if result.error is None:
            return False

        return result.stage in {"authentication", "trigger"} or (
            result.stage == "processing" and not result.processed
        )
