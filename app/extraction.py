import os
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


def extract_places_with_vertex_deepseek(
    text: str,
    historical_period: str | None = None,
) -> PlaceExtraction:
    access_token, project = _vertex_access_token_and_project()
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
    user_content = (
        f"{_extraction_prompt(historical_period)}\n\n"
        "SOURCE TEXT TO EXTRACT:\n"
        f"{text}"
    )
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": user_content}],
        "response_format": {"type": "json_object"},
        "temperature": 0.1,
        "max_tokens": int(os.getenv("LLM_MAX_TOKENS", "32768")),
        "stream": False,
    }
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
        response.raise_for_status()
    except httpx.HTTPStatusError as error:
        try:
            message = error.response.json().get("error", {}).get("message")
        except (ValueError, AttributeError):
            message = None
        raise RuntimeError(
            f"Vertex AI API {error.response.status_code}: {message or 'request failed'}"
        ) from error
    except httpx.HTTPError as error:
        raise RuntimeError(f"Vertex AI connection failed: {error}") from error

    try:
        content = response.json()["choices"][0]["message"]["content"]
        if not content:
            raise ValueError("empty content")
        return PlaceExtraction.model_validate_json(content)
    except (KeyError, IndexError, TypeError, ValueError) as error:
        raise RuntimeError("Vertex AI DeepSeek 沒有返回可解析的地名 JSON。") from error


def extract_places(text: str, historical_period: str | None = None) -> PlaceExtraction:
    provider = configured_extraction_provider()
    if provider == "google_vertex":
        return extract_places_with_vertex_deepseek(text, historical_period)
    if provider == "deepseek":
        return extract_places_with_deepseek(text, historical_period)
    return extract_places_with_gemini(text, historical_period)
