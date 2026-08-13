import json

import httpx

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
