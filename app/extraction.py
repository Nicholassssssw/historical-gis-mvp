import json
import os
import random
import re
import threading
import time
from pathlib import Path

import google.auth
import httpx
from google.auth.exceptions import DefaultCredentialsError
from google.auth.transport.requests import Request as GoogleAuthRequest

from .schemas import PlaceExtraction
from .text_metrics import count_words

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "extract_places.txt"


PROVIDER_DEFAULT_MODELS = {
    "deepseek": "deepseek-v4-flash",
    "google_vertex": "deepseek-ai/deepseek-v3.1-maas",
}

RETRYABLE_VERTEX_STATUS_CODES = {429, 500, 503, 504}
_VERTEX_REQUEST_LOCK = threading.Lock()
_VERTEX_LAST_REQUEST_AT = 0.0


class VertexThrottleError(RuntimeError):
    """Raised after the managed DeepSeek endpoint exhausts its shared capacity."""


class VertexModelUnavailableError(RuntimeError):
    """Raised when a managed model is disabled, unavailable, or retired."""


def configured_extraction_provider() -> str:
    provider = os.getenv(
        "LLM_PROVIDER",
        os.getenv("AI_PROVIDER", "google_vertex"),
    ).strip().lower()
    if provider not in PROVIDER_DEFAULT_MODELS:
        raise RuntimeError(
            "系統已鎖定為 DeepSeek-only；LLM_PROVIDER 只支援 "
            "google_vertex 或 deepseek。"
        )
    return provider


def _configured_model(provider: str) -> str:
    configured_provider = configured_extraction_provider()
    generic_model = os.getenv("LLM_MODEL", "").strip()
    if generic_model and provider == configured_provider:
        return generic_model
    legacy_variable = {
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


def _google_cloud_api_key() -> str:
    """Return the Google project key without exposing it to clients or logs."""
    if os.getenv("VERTEX_AUTH_METHOD", "adc").strip().lower() != "api_key":
        return ""
    return (
        os.getenv("GOOGLE_API_KEY", "").strip()
        or os.getenv("GOOGLE_CLOUD_API_KEY", "").strip()
    )


def _vertex_auth_method() -> str:
    if _google_cloud_api_key():
        return "google_api_key"
    if _vertex_credentials_detectable():
        return "application_default_credentials"
    return "unavailable"


def extraction_provider_config() -> dict:
    provider = configured_extraction_provider()
    if provider == "google_vertex":
        auth_method = _vertex_auth_method()
        return {
            "provider": provider,
            "label": "Vertex AI DeepSeek",
            "enabled": bool(os.getenv("GOOGLE_CLOUD_PROJECT"))
            and auth_method != "unavailable",
            "model": _configured_model(provider),
            "auth_method": auth_method,
            "setup_message": "需要 Google Cloud ADC 或可用的 project API key",
        }
    if provider == "deepseek":
        return {
            "provider": provider,
            "label": "DeepSeek",
            "enabled": bool(os.getenv("DEEPSEEK_API_KEY")),
            "model": _configured_model(provider),
            "auth_method": "deepseek_api_key",
            "setup_message": "需要 DeepSeek API key",
        }
    raise RuntimeError("系統已鎖定為 DeepSeek-only。")


def _extraction_prompt(
    historical_period: str | None = None,
    document_title_hint: str | None = None,
) -> str:
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    supplied_context = []
    if document_title_hint:
        supplied_context.append(f"文本名稱候選：{document_title_hint}")
    if historical_period:
        supplied_context.append(f"時期候選：{historical_period}")
    if supplied_context:
        prompt += (
            "\n\n使用者提供的待核資料："
            + "；".join(supplied_context)
            + "。必須在 SOURCE TEXT 內搜尋紀錄並核對；"
            "不得因使用者填寫便直接複製到輸出。若文本無證據或互相矛盾，"
            "以文本證據為準，無法確認則輸出 null。這些待核資料亦不可單獨"
            "作為任何地名之 historical_region 證據。"
        )
    return prompt


def extract_places_with_deepseek(
    text: str,
    historical_period: str | None = None,
    document_title_hint: str | None = None,
) -> PlaceExtraction:
    api_key = os.getenv("DEEPSEEK_API_KEY")
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY 未設定。")

    model = _configured_model("deepseek")
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
    payload = {
        "model": model,
        "messages": [
            {
                "role": "system",
                "content": _extraction_prompt(historical_period, document_title_hint),
            },
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


def _vertex_project() -> str:
    project = os.getenv("GOOGLE_CLOUD_PROJECT", "").strip()
    if project:
        return project
    try:
        _, detected_project = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
    except DefaultCredentialsError as error:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT 未設定。") from error
    if not detected_project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT 未設定。")
    return detected_project


def _vertex_request_headers(auth_method: str) -> dict[str, str]:
    headers = {"Content-Type": "application/json; charset=utf-8"}
    if auth_method == "google_api_key":
        api_key = _google_cloud_api_key()
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY 未設定。")
        headers["x-goog-api-key"] = api_key
        return headers
    access_token, _ = _vertex_access_token_and_project()
    headers["Authorization"] = f"Bearer {access_token}"
    return headers


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
    if (
        len(chunks) > 1
        and len(chunks[-1]) < max_chars * 0.2
        and len(chunks[-2]) + len(chunks[-1]) + 1 <= max_chars
    ):
        chunks[-2] = f"{chunks[-2]}\n{chunks[-1]}"
        chunks.pop()
    return chunks


def vertex_extraction_plan(text: str, word_count: int | None = None) -> dict:
    """Plan model reads after upload metrics are known; extraction uses the same plan."""
    words = count_words(text) if word_count is None else word_count
    words_per_read = max(1000, int(os.getenv("VERTEX_WORDS_PER_READ", "20000")))
    hard_chunk_chars = max(1000, int(os.getenv("VERTEX_CHUNK_CHARS", "24000")))
    reads_by_words = max(1, (words + words_per_read - 1) // words_per_read)
    reads_by_chars = max(1, (len(text) + hard_chunk_chars - 1) // hard_chunk_chars)
    minimum_read_count = max(reads_by_words, reads_by_chars)
    target_chunk_chars = min(
        hard_chunk_chars,
        max(1000, (len(text) + minimum_read_count - 1) // minimum_read_count),
    )
    read_count = len(_split_source_text(text, target_chunk_chars))
    estimated_source_tokens = max(words, (len(text) + 3) // 4)
    estimated_prompt_tokens_per_read = int(
        os.getenv("VERTEX_PROMPT_TOKENS_PER_READ", "2600")
    )
    return {
        "read_count": read_count,
        "words_per_read": words_per_read,
        "target_chunk_chars": target_chunk_chars,
        "estimated_input_tokens": (
            estimated_source_tokens + read_count * estimated_prompt_tokens_per_read
        ),
    }


def _vertex_error_message(response) -> str:
    try:
        payload = response.json()
    except (ValueError, AttributeError):
        payload = None

    payloads = payload if isinstance(payload, list) else [payload]
    for item in payloads:
        if not isinstance(item, dict):
            continue
        error = item.get("error")
        if isinstance(error, dict):
            message = error.get("message") or error.get("status")
            if message:
                return str(message)
        if isinstance(error, str) and error:
            return error
        message = item.get("message") or item.get("detail")
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
        status_code = getattr(response, "status_code", None)
        base_variable = (
            "VERTEX_429_BASE_DELAY_SECONDS"
            if status_code == 429
            else "VERTEX_RETRY_BASE_DELAY_SECONDS"
        )
        default_base = "10" if status_code == 429 else "5"
        base_delay = max(
            1.0,
            float(os.getenv(base_variable, default_base)),
        )
        max_delay = max(
            base_delay,
            float(os.getenv("VERTEX_RETRY_MAX_DELAY_SECONDS", "60")),
        )
        jitter = random.uniform(0, min(3.0, base_delay * 0.25))
        return min(max_delay, base_delay * (2 ** attempt) + jitter)


def _wait_for_vertex_request_slot() -> None:
    global _VERTEX_LAST_REQUEST_AT
    minimum_interval = max(
        0.0,
        float(os.getenv("VERTEX_MIN_REQUEST_INTERVAL_SECONDS", "2")),
    )
    remaining = minimum_interval - (time.monotonic() - _VERTEX_LAST_REQUEST_AT)
    if remaining > 0:
        time.sleep(remaining)
    _VERTEX_LAST_REQUEST_AT = time.monotonic()


def _post_vertex_stream(endpoint: str, payload: dict, progress_callback=None) -> dict:
    """Read an OpenAI-compatible Vertex response as SSE and rebuild its JSON body."""
    max_retries = max(0, min(8, int(os.getenv("VERTEX_MAX_RETRIES", "1"))))
    auth_method = (
        "google_api_key" if _google_cloud_api_key()
        else "application_default_credentials"
    )
    stream_payload = {**payload, "stream": True}
    with _VERTEX_REQUEST_LOCK:
        last_error = None
        attempt = 0
        while attempt <= max_retries:
            _wait_for_vertex_request_slot()
            try:
                with httpx.stream(
                    "POST",
                    endpoint,
                    headers=_vertex_request_headers(auth_method),
                    json=stream_payload,
                    timeout=float(os.getenv("LLM_TIMEOUT_SECONDS", "300")),
                ) as response:
                    # A restricted project key must not prevent the same project
                    # from using its production-safe Application Default Credentials.
                    if (
                        auth_method == "google_api_key"
                        and response.status_code in {401, 403}
                    ):
                        response.read()
                        try:
                            _vertex_request_headers("application_default_credentials")
                        except RuntimeError:
                            pass
                        else:
                            auth_method = "application_default_credentials"
                            continue

                    if response.status_code in RETRYABLE_VERTEX_STATUS_CODES:
                        response.read()
                        if attempt < max_retries:
                            delay = _vertex_retry_delay(response, attempt)
                            if progress_callback:
                                progress_callback({
                                    "event": "retrying",
                                    "attempt": attempt + 1,
                                    "max_retries": max_retries,
                                    "message": _vertex_error_message(response),
                                })
                            time.sleep(delay)
                            attempt += 1
                            continue

                    if response.status_code >= 400:
                        response.read()
                    response.raise_for_status()
                    if progress_callback:
                        progress_callback({"event": "stream_started", "received_chars": 0})

                    content_parts = []
                    received_chars = 0
                    last_reported_chars = 0
                    last_reported_at = time.monotonic()
                    for line in response.iter_lines():
                        if not line or line.startswith(":"):
                            continue
                        raw_event = line[5:].strip() if line.startswith("data:") else line.strip()
                        if not raw_event:
                            continue
                        if raw_event == "[DONE]":
                            break
                        try:
                            event = json.loads(raw_event)
                        except ValueError:
                            continue
                        if isinstance(event, dict) and event.get("error"):
                            error = event["error"]
                            message = error.get("message") if isinstance(error, dict) else str(error)
                            raise RuntimeError(f"Vertex AI stream error: {message or 'request failed'}")

                        choices = event.get("choices") if isinstance(event, dict) else None
                        if not choices:
                            continue
                        choice = choices[0] if isinstance(choices[0], dict) else {}
                        delta = choice.get("delta") or {}
                        delta_content = delta.get("content") if isinstance(delta, dict) else None
                        if not delta_content and not content_parts:
                            message = choice.get("message") or {}
                            delta_content = message.get("content") if isinstance(message, dict) else None
                        if not isinstance(delta_content, str) or not delta_content:
                            continue
                        content_parts.append(delta_content)
                        received_chars += len(delta_content)
                        now = time.monotonic()
                        if progress_callback and (
                            received_chars - last_reported_chars >= 256
                            or now - last_reported_at >= 0.5
                        ):
                            progress_callback({
                                "event": "stream_progress",
                                "received_chars": received_chars,
                            })
                            last_reported_chars = received_chars
                            last_reported_at = now

                    content = "".join(content_parts)
                    if progress_callback and received_chars != last_reported_chars:
                        progress_callback({
                            "event": "stream_progress",
                            "received_chars": received_chars,
                        })
                    if not content:
                        raise RuntimeError("Vertex AI 串流完成，但沒有返回內容。")
                    return {"choices": [{"message": {"content": content}}]}
            except httpx.HTTPStatusError as error:
                message = _vertex_error_message(error.response)
                if error.response.status_code == 429:
                    last_error = VertexThrottleError(
                        "Vertex AI 暫時繁忙（429），系統已排隊並自動重試"
                        f" {max_retries} 次：{message}"
                    )
                elif error.response.status_code in {403, 404}:
                    last_error = VertexModelUnavailableError(
                        "Vertex AI DeepSeek 模型不可用"
                        f"（{error.response.status_code}）：{message}"
                    )
                else:
                    last_error = RuntimeError(
                        f"Vertex AI API {error.response.status_code}: {message}"
                    )
                if (
                    error.response.status_code in RETRYABLE_VERTEX_STATUS_CODES
                    and attempt < max_retries
                ):
                    time.sleep(_vertex_retry_delay(error.response, attempt))
                    attempt += 1
                    continue
                raise last_error from error
            except httpx.HTTPError as error:
                last_error = RuntimeError(f"Vertex AI connection failed: {error}")
                if attempt < max_retries:
                    retry_response = getattr(error, "response", None)
                    if progress_callback:
                        progress_callback({
                            "event": "retrying",
                            "attempt": attempt + 1,
                            "max_retries": max_retries,
                            "message": str(error),
                        })
                    time.sleep(_vertex_retry_delay(retry_response, attempt))
                    attempt += 1
                    continue
                raise last_error from error
            except ValueError as error:
                raise RuntimeError("Vertex AI 沒有返回合法 JSON response。") from error
        raise last_error or RuntimeError("Vertex AI request failed.")


def _post_vertex_json(endpoint: str, payload: dict, progress_callback=None) -> dict:
    """Compatibility wrapper retained for callers that do not need live progress."""
    return _post_vertex_stream(endpoint, payload, progress_callback=progress_callback)


def extract_places_with_vertex_deepseek(
    text: str,
    historical_period: str | None = None,
    document_title_hint: str | None = None,
    *,
    start_read: int = 0,
    existing_places: list | None = None,
    chunk_chars: int | None = None,
    progress_callback=None,
    stream_callback=None,
) -> PlaceExtraction:
    project = _vertex_project()
    location = os.getenv("VERTEX_LOCATION", "us-west2").strip().lower()
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
    plan = vertex_extraction_plan(text)
    chunks = _split_source_text(text, chunk_chars or plan["target_chunk_chars"])
    if not 0 <= start_read <= len(chunks):
        raise RuntimeError("已儲存的閱讀進度與目前文件不一致，請重新上載文件。")
    merged_places = list(existing_places or [])
    detected_document_title = None
    detected_historical_dynasty = None
    detected_historical_year_text = None
    use_direct_deepseek = False

    for zero_based_index in range(start_read, len(chunks)):
        chunk_index = zero_based_index + 1
        chunk = chunks[zero_based_index]
        user_content = (
            f"{_extraction_prompt(historical_period, document_title_hint)}\n\n"
            f"SOURCE TEXT TO EXTRACT (chunk {chunk_index} of {len(chunks)}):\n"
            f"{chunk}"
        )
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": user_content}],
            "response_format": {"type": "json_object"},
            "temperature": 0.1,
            "max_tokens": int(os.getenv("LLM_MAX_TOKENS", "32768")),
            "stream": True,
        }
        if use_direct_deepseek:
            chunk_result = extract_places_with_deepseek(
                chunk,
                historical_period,
                document_title_hint,
            )
        else:
            try:
                def report_stream_progress(event):
                    if stream_callback:
                        stream_callback({
                            **event,
                            "current_read": chunk_index,
                            "total_reads": len(chunks),
                        })

                body = _post_vertex_json(
                    endpoint,
                    payload,
                    progress_callback=report_stream_progress,
                )
                content = body["choices"][0]["message"]["content"]
                if not content:
                    raise ValueError("empty content")
                chunk_result = PlaceExtraction.model_validate_json(content)
            except RuntimeError as deepseek_error:
                if os.getenv("DEEPSEEK_API_KEY", "").strip():
                    try:
                        chunk_result = extract_places_with_deepseek(
                            chunk,
                            historical_period,
                            document_title_hint,
                        )
                        use_direct_deepseek = True
                    except Exception as error:
                        raise RuntimeError(
                            "Google Cloud DeepSeek 失敗，直連 DeepSeek 亦失敗："
                            f"Vertex DeepSeek: {deepseek_error}; "
                            f"Direct DeepSeek: {error}"
                        ) from error
                else:
                    raise RuntimeError(
                        "Google Cloud DeepSeek 暫時無法完成請求，而系統已設定為"
                        "只可使用 DeepSeek。"
                        f"Vertex AI 回覆：{deepseek_error}。"
                        "請稍後重試，或設定 DEEPSEEK_API_KEY。"
                    ) from deepseek_error
            except (KeyError, IndexError, TypeError, ValueError) as error:
                raise RuntimeError(
                    f"Vertex AI DeepSeek 第 {chunk_index}/{len(chunks)} 部分"
                    "沒有返回可解析的地名 JSON。"
                ) from error

        # Keep the first explicit source-text finding across chunks. Later
        # chunks may contain cited works or different quoted periods and must
        # not silently replace an earlier document-level identification.
        detected_document_title = (
            detected_document_title or chunk_result.document_title
        )
        detected_historical_dynasty = (
            detected_historical_dynasty or chunk_result.historical_dynasty
        )
        detected_historical_year_text = (
            detected_historical_year_text or chunk_result.historical_year_text
        )
        for item in sorted(chunk_result.places, key=lambda place: place.route_order):
            item.route_order = len(merged_places) + 1
            merged_places.append(item)
        if progress_callback:
            progress_callback(chunk_index, len(chunks), merged_places)

    return PlaceExtraction(
        document_title=detected_document_title,
        historical_dynasty=detected_historical_dynasty,
        historical_year_text=detected_historical_year_text,
        places=merged_places,
    )


def extract_places(
    text: str,
    historical_period: str | None = None,
    document_title_hint: str | None = None,
) -> PlaceExtraction:
    provider = configured_extraction_provider()
    if provider == "google_vertex":
        return extract_places_with_vertex_deepseek(
            text,
            historical_period,
            document_title_hint,
        )
    if provider == "deepseek":
        return extract_places_with_deepseek(
            text,
            historical_period,
            document_title_hint,
        )
    raise RuntimeError("系統已鎖定為 DeepSeek-only。")
