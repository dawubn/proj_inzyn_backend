"""How the legal analysis calls the chosen Azure OpenAI deployment."""

from unittest.mock import MagicMock

import httpx
import pytest
import structlog
from openai import BadRequestError

from app.core.ai_models import AI_MODEL_PROFILES
from app.tasks.legal_analysis import _complete_json

LOG = structlog.get_logger()


def _client(*effects: object) -> MagicMock:
    client = MagicMock()
    client.chat.completions.create.side_effect = list(effects)
    return client


def _answer(text: str) -> MagicMock:
    response = MagicMock()
    response.choices = [MagicMock(message=MagicMock(content=text))]
    return response


def _temperature_rejected() -> BadRequestError:
    request = httpx.Request("POST", "https://example.openai.azure.com")
    return BadRequestError(
        "Unsupported value: 'temperature' does not support 0.5 with this model.",
        response=httpx.Response(400, request=request),
        body=None,
    )


def test_regular_model_is_called_with_temperature_and_json_mode() -> None:
    client = _client(_answer('{"errors": []}'))

    assert _complete_json(client, "gpt-4.1", "prompt", LOG) == '{"errors": []}'

    kwargs = client.chat.completions.create.call_args.kwargs
    assert kwargs["model"] == "gpt-4.1"
    assert kwargs["temperature"] == 0.5
    assert kwargs["response_format"] == {"type": "json_object"}


@pytest.mark.parametrize(
    "model",
    [name for name, profile in AI_MODEL_PROFILES.items() if not profile.supports_temperature],
)
def test_reasoning_model_is_called_without_temperature(model: str) -> None:
    client = _client(_answer("{}"))

    _complete_json(client, model, "prompt", LOG)

    assert client.chat.completions.create.call_count == 1
    assert "temperature" not in client.chat.completions.create.call_args.kwargs


def test_unknown_model_rejecting_temperature_is_retried_without_it() -> None:
    client = _client(_temperature_rejected(), _answer("{}"))

    assert _complete_json(client, "custom-deployment", "prompt", LOG) == "{}"

    first, second = client.chat.completions.create.call_args_list
    assert first.kwargs["temperature"] == 0.5
    assert "temperature" not in second.kwargs


def test_other_bad_request_is_not_retried() -> None:
    request = httpx.Request("POST", "https://example.openai.azure.com")
    error = BadRequestError(
        "The prompt is too long.", response=httpx.Response(400, request=request), body=None
    )
    client = _client(error)

    with pytest.raises(BadRequestError):
        _complete_json(client, "custom-deployment", "prompt", LOG)
    assert client.chat.completions.create.call_count == 1
