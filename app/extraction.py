import os
import re
import time
from pathlib import Path

import google.auth
import httpx
from google import genai
from google.auth.exceptions import DefaultCredentialsError
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.genai import types

from .schemas import PlaceExtraction

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "extract_places.txt"


PROVIDER_DEFAULT_MODELS = {
    "gemini": "gemini-3.5-flash-lite",
    "deepseek": "deepseek-v4-flash",
    "google_vertex": "deepseek-ai/deepseek-v3.2-maas",
}

RETRYABLE_VERTEX_STATUS_CODES = {429, 500, 503, 504}


def configured_extraction_provider() -> str:
    provider = os.getenv("LLM_PROVIDER", os.getenv("AI_PROVIDER", "gemini")).strip().lower()
    if provider not in PROVIDER_DEFAULT_MODELS:
        raise RuntimeError("LLM_PROVIDER 只支援 gemini、deepseek 或 google_vertex。")
    return provider


def _configured_model(provider: str) -> str:
    generic_model = os.getenv("LLM_MODEL", "").strip()
    if generic_model:
        return generic_model
    legacy_variable = {
        "gemini": "GEMINI_MODEL",
        "deepseek": "DEEPSEEK_MODEL",
        "google_vertex": "VERTEX_MODEL",
    }[provider]
    return os.getenv(legacy_variable, PROVIDER_DEFAULT_MODELS[provider])


def _vertex_credentials_detectable() -> bool:
    try:
        google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
    except DefaultCredentialsError:
        return False
    return True


def extraction_provider_config() -> dict:
    provider = configured_extraction_provider()
    if provider == "google_vertex":
        return {
            "provider": provider,
            "label": "Vertex AI DeepSeek",
            "enabled": bool(os.getenv("GOOGLE_CLOUD_PROJECT"))
            and _vertex_credentials_detectable(),
            "model": _configured_model(provider),
            "setup_message": "需要 Google Cloud 登入",
        }
    if provider == "deepseek":
        return {
            "provider": provider,
            "label": "DeepSeek",
            "enabled": bool(os.getenv("DEEPSEEK_API_KEY")),
            "model": _configured_model(provider),
            "setup_message": "需要 DeepSeek API key",
        }
    return {
        "provider": provider,
        "label": "Gemini",
        "enabled": bool(os.getenv("GEMINI_API_KEY")),
        "model": _configured_model(provider),
        "setup_message": "需要 Gemini API key",
    }


def _extraction_prompt(historical_period: str | None = None) -> str:
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    if historical_period:
        prompt += (
            "\n\nDocument context supplied by the user: "
            f"年份／朝代 = {historical_period}. Use this only as dating context."
        )
    return prompt


def extract_places_with_gemini(text: str, historical_period: str | None = None) -> PlaceExtraction:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY 未設定。")

    client = genai.Client(api_key=api_key)
    prompt = _extraction_prompt(historical_period)
    model = _configured_model("gemini")

    # Structured Outputs: one extraction stage, one schema.
    response = client.models.generate_content(
        model=model,
        contents=text,
        config=types.GenerateContentConfig(
            system_instruction=prompt,
            response_mime_type="application/json",
            response_schema=PlaceExtraction,
        ),
    )

    if isinstance(response.parsed, PlaceExtraction):
        return response.parsed
    if response.text:
        return PlaceExtraction.model_validate_json(response.text)
    raise RuntimeError("Gemini 沒有返回可解析的地名結果。")


def extract_places_with_deepseek(text: str, historical_period: str | None = None) -> PlaceExtraction:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY 未設定。")

    model = _configured_model("deepseek")
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _extraction_prompt(historical_period)},
            {"role": "user", "content": text},
        ],
        "response_format": {"type": "json_object"},
        "thinking": {"type": "disabled"},
        "temperature": 0.1,
        "max_tokens": int(os.getenv("DEEPSEEK_MAX_TOKENS", "8192")),
    }
    try:
        response = httpx.post(
            f"{base_url}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "180")),
        )
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        try:
            message = error.response.json().get("error", {}).get("message")
        except (ValueError, AttributeError):
            message = None
        raise RuntimeError(
            f"DeepSeek API {error.response.status_code}: {message or 'request failed'}"
        ) from error
    except httpx.HTTPError as error:
        raise RuntimeError(f"DeepSeek API connection failed: {error}") from error

    try:
        content = response.json()["choices"][0]["message"]["content"]
        if not content:
            raise ValueError("empty content")
        return PlaceExtraction.model_validate_json(content)
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise RuntimeError("DeepSeek 沒有返回可解析的地名 JSON。") from error


def _vertex_access_token_and_project() -> tuple[str, str]:
    try:
        credentials, detected_project = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
    except DefaultCredentialsError as error:
        raise RuntimeError(
            "Google Cloud Application Default Credentials 未設定；"
            "本機請先執行 gcloud auth application-default login。"
        ) from error

    project = os.getenv("GOOGLE_CLOUD_PROJECT") or detected_project
    if not project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT 未設定。")
    if not credentials.valid:
        credentials.refresh(GoogleAuthRequest())
    if not credentials.token:
        raise RuntimeError("Google Cloud access token 無法取得。")
    return credentials.token, project


def _split_source_text(text: str, max_chars: int) -> list[str]:
    """Split long sources near paragraph/sentence boundaries without overlap."""
    if len(text) <= max_chars:
        return [text]

    chunks = []
    start = 0
    while start < len(text):
        hard_end = min(start + max_chars, len(text))
        if hard_end == len(text):
            end = hard_end
        else:
            search_start = start + int(max_chars * 0.65)
            window = text[search_start:hard_end]
            break_positions = [
                window.rfind("\n\n"),
                window.rfind("\n"),
            ]
            sentence_matches = list(re.finditer(r"[。！？；]", window))
            if sentence_matches:
                break_positions.append(sentence_matches[-1].end())
            best = max(break_positions)
            end = search_start + best if best > 0 else hard_end
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        start = end
    return chunks


def _vertex_error_message(response) -> str:
    try:
        payload = response.json()
    except (ValueError, AttributeError):
        payload = None

    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        message = error.get("message") or error.get("status")
        if message:
            return str(message)
    if isinstance(error, str) and error:
        return error
    if isinstance(payload, dict):
        message = payload.get("message") or payload.get("detail")
        if message:
            return str(message)
    text = getattr(response, "text", "")
    return text.strip()[:1000] or "request failed"


def _vertex_retry_delay(response, attempt: int) -> float:
    headers = getattr(response, "headers", {}) or {}
    retry_after = headers.get("retry-after") or headers.get("Retry-After")
    try:
        return max(1.0, float(retry_after))
    except (TypeError, ValueError):
        return float(5 * (3 ** attempt))


def _post_vertex_json(endpoint: str, payload: dict) -> dict:
    max_retries = max(0, min(2, int(os.getenv("VERTEX_MAX_RETRIES", "2"))))
    last_error = None
    for attempt in range(max_retries + 1):
        access_token, _ = _vertex_access_token_and_project()
        try:
            response = httpx.post(
                endpoint,
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                json=payload,
                timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "300")),
            )
            if (
                response.status_code in RETRYABLE_VERTEX_STATUS_CODES
                and attempt < max_retries
            ):
                time.sleep(_vertex_retry_delay(response, attempt))
                continue
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as error:
            message = _vertex_error_message(error.response)
            last_error = RuntimeError(
                f"Vertex AI API {error.response.status_code}: {message}"
            )
            if (
                error.response.status_code in RETRYABLE_VERTEX_STATUS_CODES
                and attempt < max_retries
            ):
                time.sleep(_vertex_retry_delay(error.response, attempt))
                continue
            raise last_error from error
        except httpx.HTTPError as error:
            last_error = RuntimeError(f"Vertex AI connection failed: {error}")
            if attempt < max_retries:
                time.sleep(float(5 * (3 ** attempt)))
                continue
            raise last_error from error
        except ValueError as error:
            raise RuntimeError("Vertex AI 沒有返回合法 JSON response。") from error
    raise last_error or RuntimeError("Vertex AI request failed.")


def extract_places_with_vertex_deepseek(
    text: str,
    historical_period: str | None = None,
) -> PlaceExtraction:
    _, project = _vertex_access_token_and_project()
    location = os.getenv("VERTEX_LOCATION", "global").strip().lower()
    model = _configured_model("google_vertex")
    hostname = (
        "aiplatform.googleapis.com"
        if location == "global"
        else f"{location}-aiplatform.googleapis.com"
    )
    endpoint = (
        f"https://{hostname}/v1/projects/{project}/locations/{location}"
        "/endpoints/openapi/chat/completions"
    )
    chunk_chars = max(1000, int(os.getenv("VERTEX_CHUNK_CHARS", "45000")))
    chunks = _split_source_text(text, chunk_chars)
    merged_places = []

    for chunk_index, chunk in enumerate(chunks, start=1):
        user_content = (
            f"{_extraction_prompt(historical_period)}\n\n"
            f"SOURCE TEXT TO EXTRACT (chunk {chunk_index} of {len(chunks)}):\n"
            f"{chunk}"
        )
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": user_content}],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
            "max_tokens": int(os.getenv("LLM_MAX_TOKENS", "32768")),
            "stream": False,
        }
        body = _post_vertex_json(endpoint, payload)
        try:
            content = body["choices"][0]["message"]["content"]
            if not content:
                raise ValueError("empty content")
            chunk_result = PlaceExtraction.model_validate_json(content)
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise RuntimeError(
                f"Vertex AI DeepSeek 第 {chunk_index}/{len(chunks)} 部分"
                "沒有返回可解析的地名 JSON。"
            ) from error

        for item in sorted(chunk_result.places, key=lambda place: place.route_order):
            item.route_order = len(merged_places) + 1
            merged_places.append(item)

    return PlaceExtraction(places=merged_places)


def extract_places(text: str, historical_period: str | None = None) -> PlaceExtraction:
    provider = configured_extraction_provider()
    if provider == "google_vertex":
        return extract_places_with_vertex_deepseek(text, historical_period)
    if provider == "deepseek":
        return extract_places_with_deepseek(text, historical_period)
    return extract_places_with_gemini(text, historical_period)
