import json
import re
from typing import Optional

import vertexai
from vertexai.generative_models import GenerativeModel

from .config import PROJECT_ID, VERTEX_REGION, GEMINI_MODEL

def call_gemini(prompt: str) -> str:
    try:
        vertexai.init(project=PROJECT_ID, location=VERTEX_REGION)
        model = GenerativeModel(GEMINI_MODEL)
        resp = model.generate_content(prompt)
        return resp.text
    except Exception as e:
        return f"ERROR_CALLING_GEMINI: {type(e).__name__}: {str(e)}"

def strip_code_fences(text: str) -> str:
    return re.sub(r"```json|```", "", (text or "")).strip()

def try_parse_json(text: str) -> Optional[dict]:
    try:
        return json.loads(strip_code_fences(text))
    except Exception:
        return None