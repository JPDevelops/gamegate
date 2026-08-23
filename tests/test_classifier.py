"""Step 10 acceptance: validated structured output, and EVERY failure mode
(timeout, garbage JSON, schema violation, outage) lands on the deterministic
fallback. No live LLM calls anywhere."""
import httpx
import pytest

from app.main import app
from app.models.event import Event
from app.services.classifier import (
    Classification,
    ClassifierError,
    DeterministicClassifier,
    OpenAIClassifier,
    SafeClassifier,
)
from tests.test_events import make_event


def event(**overrides) -> Event:
    return Event(**make_event(**overrides))


def test_deterministic_rules():
    det = DeterministicClassifier()
    assert det.classify(event(priority="urgent")).urgency == 9
    assert det.classify(event(priority="actionable")).category == "action_required"
    assert det.classify(event(priority="ignore", requires_action=False)).category == "ignore"
    assert (
        det.classify(event(priority="informational", requires_action=False)).category
        == "fyi"
    )


class ExplodingClassifier:
    def __init__(self, exc):
        self.exc = exc

    def classify(self, _event):
        raise self.exc


@pytest.mark.parametrize(
    "failure",
    [
        ClassifierError("invalid JSON from model"),
        TimeoutError("LLM timed out"),
        RuntimeError("provider outage"),
    ],
)
def test_every_failure_falls_back(failure):
    safe = SafeClassifier(ExplodingClassifier(failure), DeterministicClassifier())
    result = safe.classify(event(priority="actionable"))
    assert isinstance(result, Classification)
    assert result.category == "action_required"


def test_no_primary_means_deterministic_only():
    safe = SafeClassifier(None, DeterministicClassifier())
    assert safe.classify(event()).category == "action_required"


def _openai_with_mocked_response(body: str, status: int = 200) -> OpenAIClassifier:
    def handler(request):
        return httpx.Response(
            status, json={"choices": [{"message": {"content": body}}]}
        ) if status == 200 else httpx.Response(status, text="error")

    transport = httpx.MockTransport(handler)
    return OpenAIClassifier(api_key="test", client=httpx.Client(transport=transport))


def test_openai_valid_json_is_parsed():
    body = (
        '{"category": "action_required", "urgency": 8, "summary": "s",'
        ' "suggested_action": "a", "reason": "r"}'
    )
    result = _openai_with_mocked_response(body).classify(event())
    assert result.urgency == 8


def test_openai_invalid_json_raises_classifier_error():
    with pytest.raises(ClassifierError):
        _openai_with_mocked_response("I think this email is important!").classify(event())


def test_openai_schema_violation_raises_classifier_error():
    body = (
        '{"category": "action_required", "urgency": 47, "summary": "s",'
        ' "suggested_action": "a", "reason": "r"}'
    )
    with pytest.raises(ClassifierError):
        _openai_with_mocked_response(body).classify(event())


def test_openai_http_error_raises_classifier_error():
    with pytest.raises(ClassifierError):
        _openai_with_mocked_response("", status=503).classify(event())


def test_classify_endpoint_uses_fallback(client):
    created = client.post("/events", json=make_event()).json()

    app.dependency_overrides = {}
    from app.services.classifier import build_classifier

    app.dependency_overrides[build_classifier] = lambda: SafeClassifier(
        ExplodingClassifier(ClassifierError("down")), DeterministicClassifier()
    )
    try:
        response = client.post(f"/events/{created['id']}/classify")
        assert response.status_code == 200
        assert response.json()["category"] == "action_required"
        assert client.post("/events/nope/classify").status_code == 404
    finally:
        app.dependency_overrides = {}
