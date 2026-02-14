import json
from fastapi import FastAPI, UploadFile, File, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from google.cloud import speech

from .models import ChatRequest, SetGoalsRequest
from .security import require_auth, sanitize_user_text, rate_limit_or_raise
from .store import get_state, save_state
from .llm import call_gemini, try_parse_json
from .config import USDA_API_KEY
from .tools.local import local_lookup
from .tools.usda import usda_search, usda_details
from .ui import build_ui_html

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten for production
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

speech_client = speech.SpeechClient()

@app.get("/")
def root():
    return {"status": "ok", "open": "/ui"}

@app.get("/ui", response_class=HTMLResponse)
def ui():
    return build_ui_html()

@app.post("/set_goals")
def set_goals(req: SetGoalsRequest, request: Request):
    require_auth(request)
    rate_limit_or_raise(req.user_id)

    state = get_state(req.user_id)
    state["goals"] = {
        "calories": int(req.goals.calories),
        "protein": int(req.goals.protein),
        "carbs": int(req.goals.carbs),
        "fat": int(req.goals.fat),
    }
    state.setdefault("consumed", {"calories": 0, "protein": 0, "carbs": 0, "fat": 0})
    save_state(req.user_id, state)
    return {"status": "ok", "goals": state["goals"]}

@app.post("/reset_meals")
def reset_meals(request: Request, user_id: str = "demo"):
    require_auth(request)
    rate_limit_or_raise(user_id)

    state = get_state(user_id)
    state["consumed"] = {"calories": 0, "protein": 0, "carbs": 0, "fat": 0}
    save_state(user_id, state)
    return {"status": "ok", "message": "Daily meals reset. Goals preserved.", "state": state}

@app.post("/transcribe")
async def transcribe(request: Request, file: UploadFile = File(...)):
    require_auth(request)

    audio_bytes = await file.read()
    audio = speech.RecognitionAudio(content=audio_bytes)
    config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.WEBM_OPUS,
        sample_rate_hertz=48000,
        language_code="en-US",
    )
    response = speech_client.recognize(config=config, audio=audio)
    transcript = " ".join([r.alternatives[0].transcript for r in response.results]) if response.results else ""
    transcript = sanitize_user_text(transcript)
    return {"transcript": transcript}

@app.post("/chat")
def chat(req: ChatRequest, request: Request):
    require_auth(request)
    rate_limit_or_raise(req.user_id)

    meal = sanitize_user_text(req.message)
    if not meal:
        return {"error": "Empty input"}

    state = get_state(req.user_id)
    goals = state["goals"]
    consumed = state["consumed"]

    # Tool selection: always TRY USDA if available; else local.
    tool_used = "local"
    tool_debug = {}
    tool_data = None

    if USDA_API_KEY:
        s = usda_search(meal)
        tool_debug["usda_search_ok"] = bool(s.get("ok"))
        tool_debug["usda_results_count"] = len(s.get("results", [])) if s.get("ok") else 0

        if s.get("ok") and s.get("results"):
            first = s["results"][0]
            tool_debug["usda_choice"] = first.get("description")
            d = usda_details(first["fdcId"])
            if d.get("ok"):
                tool_used = "usda"
                tool_data = d
            else:
                tool_used = "local_fallback"
                tool_data = local_lookup(meal)
        else:
            tool_used = "local_fallback"
            tool_data = local_lookup(meal)
    else:
        tool_data = local_lookup(meal)

    prompt = f"""
You are a nutrition assistant for a high-protein lifter.
Goal: help hit daily macros, prioritize protein, and give simple next-meal guidance.

User daily goals: {goals}
Consumed so far today: {consumed}
Meal just eaten: {meal}

Tool data (if available):
{json.dumps(tool_data)[:6000]}

Return STRICT JSON ONLY:
{{
  "meal_summary": {{
    "description": "string",
    "assumptions": "string",
    "calories": int,
    "protein_g": int,
    "carbs_g": int,
    "fat_g": int
  }},
  "updated_totals": {{
    "calories": int,
    "protein": int,
    "carbs": int,
    "fat": int
  }},
  "remaining": {{
    "calories": int,
    "protein": int,
    "carbs": int,
    "fat": int
  }},
  "advice": {{
    "protein_focus": "string (mention how much protein to aim for next meal/snack)",
    "next_meal_ideas": ["3 short ideas that help hit remaining protein"],
    "watch_out_for": "string"
  }}
}}

Rules:
- remaining values must not go below 0
- keep numbers realistic
- all estimates should be conservative to support a sustainable fitness journey
- emphasize protein first (e.g., 30–50g next meal if behind)
"""

    result = call_gemini(prompt)
    if result.startswith("ERROR_CALLING_GEMINI"):
        return {"error": result, "tool_used": tool_used, "tool_debug": tool_debug}

    parsed = try_parse_json(result)
    if not parsed:
        return {"error": "JSON_PARSE_FAILED", "raw": result[:1500], "tool_used": tool_used, "tool_debug": tool_debug}

    # Data minimization: store totals only (not raw meal text)
    state["consumed"] = parsed.get("updated_totals", consumed)
    save_state(req.user_id, state)

    parsed["tool_used"] = tool_used
    parsed["tool_debug"] = tool_debug
    return parsed