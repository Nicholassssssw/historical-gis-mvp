import json

from app import extraction


class FakeDeepSeekResponse:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "places": [{
                            "route_order": 1,
                            "original_name": "臨安",
                            "normalized_name": "臨安",
                            "date_text": "初一",
                            "sentence": "初一經臨安。",
                            "route_role": "passed",
                            "place_type": "settlement",
                            "historical_region": "杭州府",
                            "confidence": 0.9,
                        }]
                    }, ensure_ascii=False)
                }
            }]
        }


def test_deepseek_json_output_is_validated(monkeypatch):
    captured = {}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["payload"] = kwargs["json"]
        return FakeDeepSeekResponse()

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-flash")
    monkeypatch.setattr(extraction.httpx, "post", fake_post)

    result = extraction.extract_places_with_deepseek("初一經臨安。", "明朝")

    assert result.places[0].original_name == "臨安"
    assert result.places[0].route_role == "passed"
    assert captured["url"] == "https://api.deepseek.com/chat/completions"
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert "JSON" in captured["payload"]["messages"][0]["content"]


def test_provider_selection(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "google_vertex")
    monkeypatch.setenv("LLM_MODEL", "deepseek-ai/deepseek-v3.2-maas")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setattr(
        extraction.google.auth,
        "default",
        lambda **kwargs: (object(), "detected-project"),
    )
    config = extraction.extraction_provider_config()
    assert config == {
        "provider": "google_vertex",
        "label": "Vertex AI DeepSeek",
        "enabled": True,
        "model": "deepseek-ai/deepseek-v3.2-maas",
        "setup_message": "需要 Google Cloud 登入",
    }


def test_vertex_provider_is_not_ready_without_adc(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "google_vertex")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setattr(
        extraction.google.auth,
        "default",
        lambda **kwargs: (_ for _ in ()).throw(extraction.DefaultCredentialsError()),
    )

    config = extraction.extraction_provider_config()

    assert config["enabled"] is False
    assert config["setup_message"] == "需要 Google Cloud 登入"


def test_vertex_deepseek_uses_adc_and_global_openapi_endpoint(monkeypatch):
    captured = {}

    class FakeCredentials:
        valid = True
        token = "test-access-token"

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs["headers"]
        captured["payload"] = kwargs["json"]
        return FakeDeepSeekResponse()

    monkeypatch.setenv("LLM_MODEL", "deepseek-ai/deepseek-v3.2-maas")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("VERTEX_LOCATION", "global")
    monkeypatch.setattr(
        extraction.google.auth,
        "default",
        lambda **kwargs: (FakeCredentials(), "detected-project"),
    )
    monkeypatch.setattr(extraction.httpx, "post", fake_post)

    result = extraction.extract_places_with_vertex_deepseek("初一經臨安。", "明朝")

    assert result.places[0].original_name == "臨安"
    assert captured["url"] == (
        "https://aiplatform.googleapis.com/v1/projects/test-project/locations/"
        "global/endpoints/openapi/chat/completions"
    )
    assert captured["headers"]["Authorization"] == "Bearer test-access-token"
    assert captured["payload"]["model"] == "deepseek-ai/deepseek-v3.2-maas"
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert captured["payload"]["messages"][0]["role"] == "user"
