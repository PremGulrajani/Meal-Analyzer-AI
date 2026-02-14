import os, json, datetime, re, hashlib
from typing import Optional

from fastapi import FastAPI, UploadFile, File, Request, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import vertexai
from vertexai.generative_models import GenerativeModel

from google.cloud import speech
from google.cloud import firestore
import requests


# -------------------------
# Config
# -------------------------
PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("PROJECT_ID")
VERTEX_REGION = os.environ.get("VERTEX_REGION", "us-east1")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash-lite-001")

USDA_API_KEY = os.environ.get("USDA_API_KEY")
USDA_API_BASE = "https://api.nal.usda.gov/fdc/v1"

DEMO_MODE = os.environ.get("DEMO_MODE", "1") == "1"
BASIC_AUTH_TOKEN = os.environ.get("BASIC_AUTH_TOKEN", "")  # used only if DEMO_MODE=0

MAX_INPUT_CHARS = int(os.environ.get("MAX_INPUT_CHARS", "400"))
MAX_REQUESTS_PER_DAY = int(os.environ.get("MAX_REQUESTS_PER_DAY", "50"))

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # keep open for demo; tighten for production
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

db = firestore.Client()
speech_client = speech.SpeechClient()


# -------------------------
# Data Models
# -------------------------
class Goals(BaseModel):
    calories: int
    protein: int
    carbs: int
    fat: int

class ChatRequest(BaseModel):
    user_id: str = "demo"
    message: str
    # mode removed from UI; keep optional for backward compatibility
    mode: Optional[str] = None

class SetGoalsRequest(BaseModel):
    user_id: str = "demo"
    goals: Goals


# -------------------------
# Security helpers
# -------------------------
def require_auth(req: Request):
    if DEMO_MODE:
        return
    if not BASIC_AUTH_TOKEN:
        raise HTTPException(status_code=500, detail="Server misconfigured: BASIC_AUTH_TOKEN missing.")
    token = req.headers.get("x-auth-token", "")
    if token != BASIC_AUTH_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")

def sanitize_user_text(text: str) -> str:
    t = (text or "").strip()
    t = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F]", "", t)
    if len(t) > MAX_INPUT_CHARS:
        t = t[:MAX_INPUT_CHARS]
    return t

def rate_limit_or_raise(user_id: str):
    doc = get_doc(user_id)
    snap = doc.get()
    data = snap.to_dict() or {}
    meta = data.get("meta", {})
    used = int(meta.get("requests_today", 0))

    if used >= MAX_REQUESTS_PER_DAY:
        raise HTTPException(status_code=429, detail=f"Rate limit exceeded ({MAX_REQUESTS_PER_DAY}/day).")

    meta["requests_today"] = used + 1
    doc.set({"meta": meta}, merge=True)


# -------------------------
# Firestore helpers
# -------------------------
def today_key() -> str:
    return datetime.date.today().isoformat()

def get_doc(user_id: str):
    return db.collection("users").document(user_id).collection("days").document(today_key())

def default_lifter_goals():
    return {"calories": 2200, "protein": 180, "carbs": 220, "fat": 70}

def get_state(user_id: str):
    snap = get_doc(user_id).get()
    if snap.exists:
        state = snap.to_dict() or {}
        state.setdefault("goals", default_lifter_goals())
        state.setdefault("consumed", {"calories": 0, "protein": 0, "carbs": 0, "fat": 0})
        return state
    return {
        "goals": default_lifter_goals(),
        "consumed": {"calories": 0, "protein": 0, "carbs": 0, "fat": 0},
    }

def save_state(user_id: str, state: dict):
    get_doc(user_id).set(state, merge=True)


# -------------------------
# LLM helpers
# -------------------------
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


# -------------------------
# Tool functions (external + local)
# -------------------------
def usda_search(query: str):
    if not USDA_API_KEY:
        return {"ok": False, "error": "USDA_API_KEY not set"}

    url = f"{USDA_API_BASE}/foods/search"
    params = {"api_key": USDA_API_KEY}
    payload = {"query": query, "pageSize": 3}

    r = requests.post(url, params=params, json=payload, timeout=10)
    if r.status_code != 200:
        return {"ok": False, "error": r.text[:300]}

    data = r.json()
    foods = []
    for f in data.get("foods", []):
        foods.append({"description": f.get("description"), "fdcId": f.get("fdcId")})
    return {"ok": True, "results": foods}

def usda_details(fdc_id: int):
    if not USDA_API_KEY:
        return {"ok": False, "error": "USDA_API_KEY not set"}

    url = f"{USDA_API_BASE}/food/{fdc_id}"
    params = {"api_key": USDA_API_KEY}

    r = requests.get(url, params=params, timeout=10)
    if r.status_code != 200:
        return {"ok": False, "error": r.text[:300]}
    return {"ok": True, "data": r.json()}

def local_lookup(meal: str):
    db_local = {
        "egg": {"calories": 70, "protein": 6, "carbs": 0, "fat": 5},
        "rice": {"calories": 200, "protein": 4, "carbs": 45, "fat": 1},
        "toast": {"calories": 90, "protein": 3, "carbs": 17, "fat": 1},
        "chicken": {"calories": 280, "protein": 50, "carbs": 0, "fat": 6},
        "steak": {"calories": 420, "protein": 45, "carbs": 0, "fat": 28},
    }
    hits = []
    text = (meal or "").lower()
    for k, v in db_local.items():
        if k in text:
            hits.append({"item": k, "macros": v})
    return {"ok": True, "results": hits}


# -------------------------
# UI
# -------------------------
@app.get("/")
def root():
    return {"status": "ok", "open": "/ui"}

@app.get("/ui", response_class=HTMLResponse)
def ui():
    usda_enabled = bool(USDA_API_KEY)
    return f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <title>MealAnalyzer AI - Personal Nutritional Coach</title>
  <style>
    body {{ font-family: system-ui, -apple-system, sans-serif; max-width: 900px; margin: 36px auto; padding: 0 16px; }}
    h2 {{ margin-bottom: 12px; }}
    .row {{ display: flex; gap: 12px; flex-wrap: wrap; align-items: end; }}
    .card {{ border: 1px solid #e5e5e5; border-radius: 10px; padding: 14px; background: #fff; }}
    .card h3 {{ margin: 0 0 8px; font-size: 14px; color: #333; }}
    label {{ font-size: 12px; color: #555; display:block; margin-bottom: 4px; }}
    input, textarea, button {{ font-size: 14px; padding: 8px 10px; border-radius: 8px; border: 1px solid #ccc; }}
    textarea {{ width: 100%; min-height: 90px; }}
    button {{ cursor: pointer; }}
    button.primary {{ background: #111; color: #fff; border-color: #111; }}
    pre {{ background: #f7f7f7; padding: 12px; border-radius: 10px; overflow:auto; }}
    .grid4 {{ display: grid; grid-template-columns: repeat(4, minmax(120px, 1fr)); gap: 10px; }}
    .muted {{ color:#666; font-size: 12px; }}
    .pill {{ display:inline-block; padding: 2px 8px; border-radius: 999px; background:#f1f1f1; font-size:12px; margin-left:6px; }}
    .ok {{ color:#0a7; }}
    .warn {{ color:#c70; }}

    .banner {{ padding: 10px 12px; border-radius: 10px; margin-bottom: 10px; font-size: 13px; border: 1px solid #ddd; }}
    .banner-green {{ background: #e9f7ef; border-color: #b7e3c6; color: #135b2b; }}
    .banner-yellow {{ background: #fff7e6; border-color: #ffe2a8; color: #6a4a00; }}
    .badge {{ display:inline-block; padding:2px 8px; border-radius:999px; border:1px solid #ddd; font-size:12px; }}
  </style>
</head>
<body>
  <h2>MealAnalyzer AI - Prem's Nutritional Coach</h2>
  <div class="muted">
    Voice → Speech-to-Text → Tool lookup (USDA/local) → Gemini reasoning → Firestore daily totals
    <span class="pill">Demo mode: {"ON" if DEMO_MODE else "OFF"}</span>
    <span class="pill">USDA: {"ENABLED" if usda_enabled else "DISABLED"}</span>
  </div>

  <div class="row" style="margin-top:14px;">
    <div class="card" style="flex: 1 1 520px;">
      <h3>Meal input</h3>
      <textarea id="msg">Please type or speak!</textarea>
      <div class="row" style="margin-top:10px;">
        <button onclick="recordVoice()">Record Voice (4s)</button>
        <button class="primary" onclick="analyze()">Analyze</button>
        <button onclick="resetMeals()">Reset Today's Meals</button>
      </div>
      <div class="muted" style="margin-top:8px;">
        Note: all estimates are conservative to support a sustainable fitness journey.
      </div>
    </div>

    <div class="card" style="flex: 1 1 320px;">
      <h3>Daily goals <span class="pill">High-protein lifter default</span></h3>
      <div class="grid4">
        <div><label>Calories</label><input id="g_cal" type="number" value="2200"/></div>
        <div><label>Protein (g)</label><input id="g_pro" type="number" value="180"/></div>
        <div><label>Carbs (g)</label><input id="g_carbs" type="number" value="220"/></div>
        <div><label>Fat (g)</label><input id="g_fat" type="number" value="70"/></div>
      </div>
      <div class="row" style="margin-top:10px;">
        <button onclick="saveGoals()">Save Goals</button>
        <span id="goalStatus" class="muted"></span>
      </div>
      <div class="muted" style="margin-top:8px;">
        Stored in Firestore (per-day doc). Re-run Analyze to use new goals.
      </div>
    </div>
  </div>

  <div class="row" style="margin-top:14px;">
    <div class="card" style="flex: 1 1 900px;">
      <h3>Results</h3>
      <div id="pretty"></div>
      <details style="margin-top:10px;">
        <summary class="muted">Show raw JSON</summary>
        <pre id="out"></pre>
      </details>
    </div>
  </div>

<script>
  function esc(s){{ return (s ?? "").toString().replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;"); }}

  function sourceBanner(toolUsed){{
    const t = (toolUsed || "gemini").toLowerCase();
    // green = USDA verified lookup; yellow = local fallback or gemini-only approx
    if (t.includes("usda")) {{
      return {{
        cls: "banner banner-green",
        text: "Data source: USDA (verified lookup) + Gemini (reasoning)"
      }};
    }}
    if (t.includes("local")) {{
      return {{
        cls: "banner banner-yellow",
        text: "Data source: Local lookup (approx) + Gemini (reasoning)"
      }};
    }}
    return {{
      cls: "banner banner-yellow",
      text: "Data source: Gemini approximation (no external lookup)"
    }};
  }}

  function renderPretty(data){{
    if(data.error){{
      document.getElementById("pretty").innerHTML = `<div class="warn"><b>Error:</b> ${{esc(data.error)}}</div>`;
      return;
    }}
    const tool = data.tool_used || "gemini";
    const b = sourceBanner(tool);

    const ms = data.meal_summary || {{}};
    const ut = data.updated_totals || {{}};
    const rem = data.remaining || {{}};
    const adv = data.advice || {{}};

    document.getElementById("pretty").innerHTML = `
      <div class="${{b.cls}}">
        <b>${{esc(b.text)}}</b>
        <span class="badge" style="margin-left:8px;">tool_used: ${{esc(tool)}}</span>
      </div>

      <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;">
        <div class="card" style="border-color:#eee;">
          <h3>Meal</h3>
          <div><b>${{esc(ms.description || "")}}</b></div>
          <div class="muted">${{esc(ms.assumptions || "")}}</div>
          <div style="margin-top:8px;">
            Calories: <b>${{esc(ms.calories)}}</b><br/>
            Protein: <b>${{esc(ms.protein_g)}}g</b> | Carbs: <b>${{esc(ms.carbs_g)}}g</b> | Fat: <b>${{esc(ms.fat_g)}}g</b>
          </div>
        </div>

        <div class="card" style="border-color:#eee;">
          <h3>Remaining today</h3>
          <div>
            Calories: <b>${{esc(rem.calories)}}</b><br/>
            Protein: <b>${{esc(rem.protein)}}g</b> | Carbs: <b>${{esc(rem.carbs)}}g</b> | Fat: <b>${{esc(rem.fat)}}g</b>
          </div>
        </div>

        <div class="card" style="border-color:#eee;">
          <h3>Updated totals</h3>
          <div>
            Calories: <b>${{esc(ut.calories)}}</b><br/>
            Protein: <b>${{esc(ut.protein)}}g</b> | Carbs: <b>${{esc(ut.carbs)}}g</b> | Fat: <b>${{esc(ut.fat)}}g</b>
          </div>
        </div>

        <div class="card" style="border-color:#eee;">
          <h3>Advice</h3>
          <div>${{esc(adv.protein_focus || "")}}</div>
          <div class="muted" style="margin-top:6px;"><b>Next meal ideas:</b></div>
          <ul style="margin:6px 0 0 18px;">
            ${{(adv.next_meal_ideas || []).map(x=>`<li>${{esc(x)}}</li>`).join("")}}
          </ul>
          <div class="muted" style="margin-top:6px;"><b>Watch out:</b> ${{esc(adv.watch_out_for || "")}}</div>
        </div>
      </div>
    `;
  }}

  async function analyze(txt=null){{
    const res = await fetch("/chat", {{
      method:"POST",
      headers:{{"Content-Type":"application/json"}},
      body:JSON.stringify({{
        user_id:"demo",
        message: txt || document.getElementById("msg").value
      }})
    }});
    const data = await res.json();
    document.getElementById("out").innerText = JSON.stringify(data,null,2);
    renderPretty(data);
  }}

  async function saveGoals(){{
    const payload = {{
      user_id: "demo",
      goals: {{
        calories: Number(document.getElementById("g_cal").value),
        protein: Number(document.getElementById("g_pro").value),
        carbs: Number(document.getElementById("g_carbs").value),
        fat: Number(document.getElementById("g_fat").value),
      }}
    }};
    const res = await fetch("/set_goals", {{
      method:"POST",
      headers:{{"Content-Type":"application/json"}},
      body: JSON.stringify(payload)
    }});
    const data = await res.json();
    const el = document.getElementById("goalStatus");
    el.innerHTML = (data.status === "ok") ? '<span class="ok">Saved ✓</span>' : '<span class="warn">Save failed</span>';
  }}

  async function resetMeals(){{
    const res = await fetch("/reset_meals?user_id=demo", {{ method:"POST" }});
    const data = await res.json();
    document.getElementById("out").innerText = JSON.stringify(data,null,2);
    document.getElementById("pretty").innerHTML = "<div class='ok'><b>Meals reset for today.</b></div>";
  }}

  async function recordVoice(){{
    const stream = await navigator.mediaDevices.getUserMedia({{audio:true}});
    const rec = new MediaRecorder(stream);
    let chunks=[];
    rec.ondataavailable=e=>chunks.push(e.data);
    rec.onstop=async()=>{{
      const blob=new Blob(chunks,{{type:"audio/webm"}});
      const fd=new FormData();
      fd.append("file",blob);
      const r=await fetch("/transcribe",{{method:"POST",body:fd}});
      const d=await r.json();
      document.getElementById("msg").value=d.transcript || "";
      analyze(d.transcript || "");
    }};
    rec.start();
    setTimeout(()=>rec.stop(),4000);
  }}
</script>
</body>
</html>
"""


# -------------------------
# Set Goals Endpoint
# -------------------------
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


# -------------------------
# Speech-to-Text
# -------------------------
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


# -------------------------
# Reset Meals For Today
# -------------------------
@app.post("/reset_meals")
def reset_meals(request: Request, user_id: str = "demo"):
    require_auth(request)
    rate_limit_or_raise(user_id)

    state = get_state(user_id)
    state["consumed"] = {"calories": 0, "protein": 0, "carbs": 0, "fat": 0}
    save_state(user_id, state)

    return {"status": "ok", "message": "Daily meals reset. Goals preserved.", "state": state}


# -------------------------
# Chat Endpoint (Tool use + security)
# -------------------------
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

    # -------- tool selection --------
    # We always TRY USDA if available; otherwise local.
    # (No estimate/exact UI anymore.)
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