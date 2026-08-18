import json
from contextlib import contextmanager

import httpx
import pytest

from app import extraction


@pytest.fixture(autouse=True)
def disable_vertex_pacing_during_tests(monkeypatch):
    monkeypatch.setenv("VERTEX_MIN_REQUEST_INTERVAL_SECONDS", "0")
    monkeypatch.setenv("VERTEX_AUTH_METHOD", "adc")
    monkeypatch.setattr(extraction, "_VERTEX_LAST_REQUEST_AT", 0.0)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)

    @contextmanager
    def fake_stream(method, url, **kwargs):
        assert method == "POST"
        yield extraction.httpx.post(url, **kwargs)

    monkeypatch.setattr(extraction.httpx, "stream", fake_stream)


class FakeDeepSeekResponse:
    status_code = 200
    headers = {}

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

    def iter_lines(self):
        content = self.json()["choices"][0]["message"]["content"]
        yield "data: " + json.dumps({
            "choices": [{"delta": {"content": content}}]
        }, ensure_ascii=False)
        yield "data: [DONE]"


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
    monkeypatch.setenv("LLM_MODEL", "deepseek-ai/deepseek-v3.1-maas")
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
        "model": "deepseek-ai/deepseek-v3.1-maas",
        "auth_method": "application_default_credentials",
        "setup_message": "需要 Google Cloud ADC 或可用的 project API key",
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
    assert config["setup_message"] == "需要 Google Cloud ADC 或可用的 project API key"


def test_vertex_provider_prefers_google_project_api_key_without_adc(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "google_vertex")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("VERTEX_AUTH_METHOD", "api_key")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-google-key")
    monkeypatch.setattr(
        extraction.google.auth,
        "default",
        lambda **kwargs: (_ for _ in ()).throw(extraction.DefaultCredentialsError()),
    )

    config = extraction.extraction_provider_config()

    assert config["enabled"] is True
    assert config["auth_method"] == "google_api_key"


def test_vertex_deepseek_uses_adc_and_regional_openapi_endpoint(monkeypatch):
    captured = {}

    class FakeCredentials:
        valid = True
        token = "test-access-token"

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["headers"] = kwargs["headers"]
        captured["payload"] = kwargs["json"]
        return FakeDeepSeekResponse()

    monkeypatch.setenv("LLM_MODEL", "deepseek-ai/deepseek-v3.1-maas")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("VERTEX_LOCATION", "us-west2")
    monkeypatch.setattr(
        extraction.google.auth,
        "default",
        lambda **kwargs: (FakeCredentials(), "detected-project"),
    )
    monkeypatch.setattr(extraction.httpx, "post", fake_post)

    result = extraction.extract_places_with_vertex_deepseek("初一經臨安。", "明朝")

    assert result.places[0].original_name == "臨安"
    assert captured["url"] == (
        "https://us-west2-aiplatform.googleapis.com/v1/projects/test-project/"
        "locations/us-west2/endpoints/openapi/chat/completions"
    )
    assert captured["headers"]["Authorization"] == "Bearer test-access-token"
    assert captured["payload"]["model"] == "deepseek-ai/deepseek-v3.1-maas"
    assert captured["payload"]["response_format"] == {"type": "json_object"}
    assert captured["payload"]["messages"][0]["role"] == "user"
    assert captured["payload"]["stream"] is True


def test_vertex_stream_reassembles_sse_and_reports_received_characters(monkeypatch):
    class FakeCredentials:
        valid = True
        token = "test-access-token"

    response_json = json.dumps({
        "places": [{
            "route_order": 1,
            "original_name": "杭州",
            "normalized_name": "杭州",
            "sentence": "至杭州。",
            "route_role": "passed",
            "confidence": 0.9,
        }]
    }, ensure_ascii=False)
    split_at = len(response_json) // 2
    captured = {}

    class StreamingResponse:
        status_code = 200
        headers = {}

        def raise_for_status(self):
            return None

        def iter_lines(self):
            for part in (response_json[:split_at], response_json[split_at:]):
                yield "data: " + json.dumps({
                    "choices": [{"delta": {"content": part}}]
                }, ensure_ascii=False)
            yield "data: [DONE]"

    @contextmanager
    def fake_stream(method, url, **kwargs):
        captured["payload"] = kwargs["json"]
        yield StreamingResponse()

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setattr(
        extraction.google.auth,
        "default",
        lambda **kwargs: (FakeCredentials(), "test-project"),
    )
    monkeypatch.setattr(extraction.httpx, "stream", fake_stream)
    events = []

    body = extraction._post_vertex_stream(
        "https://example.test",
        {"model": "deepseek-ai/deepseek-v3.2-maas", "stream": False},
        progress_callback=events.append,
    )

    assert body["choices"][0]["message"]["content"] == response_json
    assert captured["payload"]["stream"] is True
    assert events[0] == {"event": "stream_started", "received_chars": 0}
    assert events[-1] == {
        "event": "stream_progress",
        "received_chars": len(response_json),
    }


def test_vertex_deepseek_uses_google_project_key_before_adc(monkeypatch):
    calls = []

    def fake_post(url, **kwargs):
        calls.append(kwargs["headers"])
        return FakeDeepSeekResponse()

    monkeypatch.setenv("LLM_MODEL", "deepseek-ai/deepseek-v3.1-maas")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("VERTEX_AUTH_METHOD", "api_key")
    monkeypatch.setenv("GOOGLE_API_KEY", "test-google-key")
    monkeypatch.setattr(extraction.httpx, "post", fake_post)

    result = extraction.extract_places_with_vertex_deepseek("初一經臨安。", "明朝")

    assert result.places[0].original_name == "臨安"
    assert calls[0]["x-goog-api-key"] == "test-google-key"
    assert "Authorization" not in calls[0]


def test_restricted_google_project_key_falls_back_to_adc(monkeypatch):
    calls = []

    class FakeCredentials:
        valid = True
        token = "test-access-token"

    def fake_post(url, **kwargs):
        calls.append(kwargs["headers"])
        if len(calls) == 1:
            return httpx.Response(
                403,
                request=httpx.Request("POST", url),
                json={"error": {"message": "API_KEY_SERVICE_BLOCKED"}},
            )
        return FakeDeepSeekResponse()

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("VERTEX_AUTH_METHOD", "api_key")
    monkeypatch.setenv("GOOGLE_API_KEY", "restricted-google-key")
    monkeypatch.setattr(
        extraction.google.auth,
        "default",
        lambda **kwargs: (FakeCredentials(), "test-project"),
    )
    monkeypatch.setattr(extraction.httpx, "post", fake_post)

    result = extraction.extract_places_with_vertex_deepseek("初一經臨安。", "明朝")

    assert result.places[0].original_name == "臨安"
    assert calls[0]["x-goog-api-key"] == "restricted-google-key"
    assert calls[1]["Authorization"] == "Bearer test-access-token"


def test_long_vertex_source_is_chunked_and_route_order_is_merged(monkeypatch):
    calls = []

    class FakeCredentials:
        valid = True
        token = "test-access-token"

    def fake_post(url, **kwargs):
        calls.append(kwargs["json"])
        response = FakeDeepSeekResponse()
        place_number = len(calls)
        original_json = response.json
        response.json = lambda: {
            **original_json(),
            "choices": [{"message": {"content": json.dumps({
                "places": [{
                    "route_order": 1,
                    "original_name": f"地名{place_number}",
                    "normalized_name": f"地名{place_number}",
                    "sentence": f"經地名{place_number}",
                    "route_role": "passed",
                    "confidence": 0.9,
                }]
            }, ensure_ascii=False)}}],
        }
        return response

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("VERTEX_CHUNK_CHARS", "1000")
    monkeypatch.setattr(
        extraction.google.auth,
        "default",
        lambda **kwargs: (FakeCredentials(), "test-project"),
    )
    monkeypatch.setattr(extraction.httpx, "post", fake_post)

    result = extraction.extract_places_with_vertex_deepseek(
        ("甲" * 900 + "。\n") * 3,
        "明朝",
    )

    assert len(calls) == 3
    assert [place.route_order for place in result.places] == [1, 2, 3]
    assert [place.original_name for place in result.places] == ["地名1", "地名2", "地名3"]
    assert "chunk 1 of 3" in calls[0]["messages"][0]["content"]


def test_vertex_read_plan_uses_completed_word_count(monkeypatch):
    monkeypatch.setenv("VERTEX_WORDS_PER_READ", "40000")
    monkeypatch.setenv("VERTEX_CHUNK_CHARS", "45000")

    plan = extraction.vertex_extraction_plan("山" * 100000, word_count=100000)

    assert plan["read_count"] >= 3
    assert plan["words_per_read"] == 40000
    assert plan["target_chunk_chars"] <= 45000
    assert plan["estimated_input_tokens"] == 100000 + plan["read_count"] * 2600


def test_vertex_read_plan_keeps_short_document_to_one_read(monkeypatch):
    monkeypatch.setenv("VERTEX_WORDS_PER_READ", "40000")
    monkeypatch.setenv("VERTEX_CHUNK_CHARS", "45000")

    plan = extraction.vertex_extraction_plan("初一經臨安。", word_count=6)

    assert plan["read_count"] == 1


def test_vertex_resume_skips_completed_reads(monkeypatch):
    calls = []

    class FakeCredentials:
        valid = True
        token = "test-access-token"

    def fake_post(url, **kwargs):
        calls.append(kwargs["json"])
        read_number = len(calls) + 1
        response = FakeDeepSeekResponse()
        response.json = lambda: {
            "choices": [{"message": {"content": json.dumps({
                "places": [{
                    "route_order": 1,
                    "original_name": f"地名{read_number}",
                    "normalized_name": f"地名{read_number}",
                    "sentence": f"經地名{read_number}",
                    "route_role": "passed",
                    "confidence": 0.9,
                }]
            }, ensure_ascii=False)}}]
        }
        return response

    completed_place = extraction.PlaceExtraction.model_validate({
        "places": [{
            "route_order": 1,
            "original_name": "地名1",
            "normalized_name": "地名1",
            "sentence": "經地名1",
            "route_role": "passed",
            "confidence": 0.9,
        }]
    }).places
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setattr(
        extraction.google.auth,
        "default",
        lambda **kwargs: (FakeCredentials(), "test-project"),
    )
    monkeypatch.setattr(extraction.httpx, "post", fake_post)

    result = extraction.extract_places_with_vertex_deepseek(
        ("甲" * 900 + "。\n") * 3,
        "明朝",
        start_read=1,
        existing_places=completed_place,
        chunk_chars=1000,
    )

    assert len(calls) == 2
    assert [place.route_order for place in result.places] == [1, 2, 3]
    assert [place.original_name for place in result.places] == ["地名1", "地名2", "地名3"]
    assert "chunk 2 of 3" in calls[0]["messages"][0]["content"]


def test_vertex_429_retries_and_surfaces_google_message(monkeypatch):
    calls = 0
    sleeps = []

    class FakeCredentials:
        valid = True
        token = "test-access-token"

    def fake_post(url, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            request = httpx.Request("POST", url)
            return httpx.Response(
                429,
                request=request,
                headers={"Retry-After": "1"},
                json={"error": {"message": "shared capacity exhausted"}},
            )
        return FakeDeepSeekResponse()

    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setenv("VERTEX_MAX_RETRIES", "2")
    monkeypatch.setattr(
        extraction.google.auth,
        "default",
        lambda **kwargs: (FakeCredentials(), "test-project"),
    )
    monkeypatch.setattr(extraction.httpx, "post", fake_post)
    monkeypatch.setattr(extraction.time, "sleep", sleeps.append)

    result = extraction.extract_places_with_vertex_deepseek("初一經臨安。", "明朝")

    assert result.places[0].original_name == "臨安"
    assert calls == 2
    assert sleeps == [1.0]


def test_vertex_error_message_reads_google_error_body():
    response = httpx.Response(
        429,
        request=httpx.Request("POST", "https://example.test"),
        json={"error": {"message": "Dynamic shared quota exhausted"}},
    )

    assert extraction._vertex_error_message(response) == "Dynamic shared quota exhausted"


def test_vertex_error_message_reads_google_list_error_body():
    response = httpx.Response(
        429,
        request=httpx.Request("POST", "https://example.test"),
        json=[{"error": {
            "code": 429,
            "message": "The request is throttled due to too many concurrent requests.",
            "status": "RESOURCE_EXHAUSTED",
        }}],
    )

    assert extraction._vertex_error_message(response) == (
        "The request is throttled due to too many concurrent requests."
    )


def test_vertex_429_uses_one_retry_before_deepseek_only_failure(monkeypatch):
    calls = 0
    sleeps = []

    class FakeCredentials:
        valid = True
        token = "test-access-token"

    def always_throttled(url, **kwargs):
        nonlocal calls
        calls += 1
        return httpx.Response(
            429,
            request=httpx.Request("POST", url),
            json=[{"error": {"message": "too many concurrent requests"}}],
        )

    monkeypatch.delenv("VERTEX_MAX_RETRIES", raising=False)
    monkeypatch.setenv("VERTEX_429_BASE_DELAY_SECONDS", "10")
    monkeypatch.setenv("VERTEX_RETRY_MAX_DELAY_SECONDS", "60")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setattr(extraction.random, "uniform", lambda *_: 0)
    monkeypatch.setattr(
        extraction.google.auth,
        "default",
        lambda **kwargs: (FakeCredentials(), "test-project"),
    )
    monkeypatch.setattr(extraction.httpx, "post", always_throttled)
    monkeypatch.setattr(extraction.time, "sleep", sleeps.append)

    with pytest.raises(extraction.VertexThrottleError, match="自動重試 1 次"):
        extraction._post_vertex_json("https://example.test", {"model": "test"})

    assert calls == 2
    assert sleeps == [10.0]


def test_vertex_429_never_uses_non_deepseek_provider(monkeypatch):
    def throttled_primary(*args, **kwargs):
        raise extraction.VertexThrottleError("DeepSeek shared capacity exhausted")

    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setattr(extraction, "_post_vertex_json", throttled_primary)

    with pytest.raises(RuntimeError, match="只可使用 DeepSeek"):
        extraction.extract_places_with_vertex_deepseek("初一經臨安。", "明朝")


def test_vertex_429_pins_direct_deepseek_when_key_exists(monkeypatch):
    primary_calls = 0
    direct_calls = 0

    def throttled_primary(*args, **kwargs):
        nonlocal primary_calls
        primary_calls += 1
        raise extraction.VertexThrottleError("DeepSeek shared capacity exhausted")

    def working_direct_deepseek(text, historical_period):
        nonlocal direct_calls
        direct_calls += 1
        return extraction.PlaceExtraction.model_validate({
            "places": [{
                "route_order": 1,
                "original_name": f"DeepSeek地名{direct_calls}",
                "normalized_name": f"DeepSeek地名{direct_calls}",
                "sentence": f"經DeepSeek地名{direct_calls}",
                "route_role": "passed",
                "confidence": 0.9,
            }]
        })

    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-key")
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "test-project")
    monkeypatch.setattr(extraction, "_post_vertex_json", throttled_primary)
    monkeypatch.setattr(
        extraction,
        "extract_places_with_deepseek",
        working_direct_deepseek,
    )

    result = extraction.extract_places_with_vertex_deepseek(
        ("甲" * 900 + "。\n") * 3,
        "明朝",
        chunk_chars=1000,
    )

    assert primary_calls == 1
    assert direct_calls == 3
    assert [place.original_name for place in result.places] == [
        "DeepSeek地名1", "DeepSeek地名2", "DeepSeek地名3",
    ]


def test_gemini_provider_is_rejected(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "gemini")

    with pytest.raises(RuntimeError, match="DeepSeek-only"):
        extraction.configured_extraction_provider()


def test_direct_deepseek_fallback_uses_provider_specific_model(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "google_vertex")
    monkeypatch.setenv("LLM_MODEL", "deepseek-ai/deepseek-v3.1-maas")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-chat")

    assert extraction._configured_model("deepseek") == "deepseek-chat"


def test_extraction_prompt_keeps_places_from_research_documents():
    prompt = extraction._extraction_prompt("明朝")

    assert "研究論文、學位論文、目錄、註釋" in prompt
    assert "places 不得回傳空陣列" in prompt
    assert "先判斷文本來源時期" in prompt
    assert "前 3 句及後 3 句" in prompt
    assert 'route_role 必須是 "passed" 或 "mentioned_only"' in prompt
    assert "normalized_name 必須與 original_name 完全相同" in prompt
    assert "不得使用模型記憶" in prompt
    assert "年份／朝代 = 明朝" in prompt
