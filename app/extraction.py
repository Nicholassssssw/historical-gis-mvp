import os
from pathlib import Path
from google import genai
from google.genai import types

from .schemas import PlaceExtraction

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "extract_places.txt"


def extract_places_with_gemini(text: str) -> PlaceExtraction:
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY 未設定。")

    client = genai.Client(api_key=api_key)
    prompt = PROMPT_PATH.read_text(encoding="utf-8")
    model = os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")

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
