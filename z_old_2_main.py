import os, json, datetime, re
from typing import Optional

from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

import vertexai
from vertexai.generative_models import GenerativeModel

from google.cloud import speech
from google.cloud import firestore


# -------------------------
# Config
# -------------------------
PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("PROJECT_ID")
VERTEX_REGION = os.environ.get("VERTEX_REGION", "us-east1")  # Vertex GenAI region
USDA_TOOL_URL = os.environ.get("USDA_TOOL_URL")  # optional, for later tool-use EC

# Model: use what you confirmed works. If this ever 404s, switch back to your last working model.
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash-lite-001")

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

db = firestore.Client()
speech_client = speech.SpeechClient()


# -------------------------
# Data Models
# -------------------------
class ChatRequest(BaseModel):
    user_id: str = "demo"
    message: str
    mode: str = "estimate"  # "estimate" or "exact"


# -------------------------
# Firestore helpers
# -------------------------
def today_key() -> str:
    return datetime.date.today().isoformat()

def get_doc(user_id: str):
    return db.collection("users").document(user_id).collection("days").document(today_key())

def default_lifter_goals():
    """
    Reasonable default for a high-protein lifter cutting/lean-bulk-ish.
    You can tweak later or add a /set-goals endpoint.
    """
    return {
        "calories": 2200,
        "protein": 180,   # high protein
        "carbs": 220,
        "fat": 70
    }

def get_state(user_id: str):
    doc = get_doc(user_id).get()
    if doc.exists:
        state = doc.to_dict() or {}
        # ensure keys exist
        state.setdefault("goals", default_lifter_goals())
        state.setdefault("consumed", {"calories": 0, "protein": 0, "carbs": 0, "fat": 0})
        return state

    return {
        "goals": default_lifter_goals(),
        "consumed": {"calories": 0, "protein": 0, "carbs": 0, "fat": 0}
    }

def save_state(user_id: str, state: dict):
    get_doc(user_id).set(state, merge=True)


# -------------------------
# UI
# -------------------------
@app.get("/")
def root():
    return {"status": "ok", "open": "/ui"}

@app.get("/ui", response_class=HTMLResponse)
def ui():
    return """
    <html>
      <body style="font-family:sans-serif;max-width:760px;margin:40px;">
        <h2>MealAnalyzer AI - Prem's Personal AI Nutritional Coacht</h2>

        <label><b>Meal input</b></label><br/>
        <textarea id="msg" style="width:100%;height:90px;">I ate 2 eggs and toast</textarea><br/><br/>

        <select id="mode">
          <option value="estimate">Estimate</option>
          <option value="exact">Exact</option>
        </select>
        <button onclick="send()">Analyze</button>
        <button onclick="rec()">Record Voice</button>

        <pre id="out" style="margin-top:16px;background:#f6f6f6;padding:12px;border-radius:8px;"></pre>

        <script>
          async function send(txt=null){
            const res = await fetch("/chat",{
              method:"POST",
              headers:{"Content-Type":"application/json"},
              body:JSON.stringify({
                user_id:"demo",
                message: txt || document.getElementById("msg").value,
                mode: document.getElementById("mode").value
              })
            });
            const data = await res.json();
            document.getElementById("out").innerText = JSON.stringify(data,null,2);
          }

          async function rec(){
            const stream = await navigator.mediaDevices.getUserMedia({audio:true});
            const rec = new MediaRecorder(stream);
            let chunks=[];
            rec.ondataavailable=e=>chunks.push(e.data);
            rec.onstop=async()=>{
              const blob=new Blob(chunks,{type:"audio/webm"});
              const fd=new FormData();
              fd.append("file",blob);
              const r=await fetch("/transcribe",{method:"POST",body:fd});
              const d=await r.json();
              document.getElementById("msg").value=d.transcript;
              send(d.transcript);
            };
            rec.start();
            setTimeout(()=>rec.stop(),4000);
          }
        </script>
      </body>
    </html>
    """


# -------------------------
# Speech-to-Text
# -------------------------
@app.post("/transcribe")
async def transcribe(file: UploadFile = File(...)):
    audio_bytes = await file.read()
    audio = speech.RecognitionAudio(content=audio_bytes)
    config = speech.RecognitionConfig(
        encoding=speech.RecognitionConfig.AudioEncoding.WEBM_OPUS,
        sample_rate_hertz=48000,
        language_code="en-US",
    )
    response = speech_client.recognize(config=config, audio=audio)
    transcript = " ".join([r.alternatives[0].transcript for r in response.results]) if response.results else ""
    return {"transcript": transcript}


# -------------------------
# LLM
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
    # Removes ```json ... ``` or ``` ... ```
    cleaned = re.sub(r"```json|```", "", text).strip()
    return cleaned


# -------------------------
# Chat Endpoint
# -------------------------
@app.post("/chat")
def chat(req: ChatRequest):
    if not req.message or not req.message.strip():
        return {"error": "Empty meal description."}

    state = get_state(req.user_id)
    goals = state["goals"]
    consumed = state["consumed"]

    # Prompt differences: "exact" nudges the model to be more conservative + specific,
    # and also sets us up to later plug USDA_TOOL_URL as a real tool call.
    mode_guidance = (
        "You may use typical nutrition estimates for common foods."
        if req.mode == "estimate"
        else "Be conservative and explicit about assumptions (portion size, cooking oils). If uncertain, give a range and choose a reasonable midpoint for totals."
    )

    # IMPORTANT: force strict JSON output
    prompt = f"""
You are a nutrition assistant for a high-protein lifter.
Goal: help the user hit daily macros, prioritize protein, and give simple next-meal guidance.

User daily goals (grams): {goals}
Consumed so far today: {consumed}
Meal just eaten: {req.message}

Mode: {req.mode}. {mode_guidance}

Return STRICT JSON ONLY (no markdown, no backticks, no commentary outside JSON) with this schema:
{{
  "meal_summary": {{
    "description": "string",
    "assumptions": "string (short)",
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
    "protein_focus": "string (1-2 sentences, mention how much protein to aim for next meal/snack)",
    "next_meal_ideas": ["3 short ideas that help hit remaining protein"],
    "watch_out_for": "string (1 short sentence: common pitfall like hidden fats/oils or low protein)"
  }}
}}

Rules:
- Ensure remaining values never go below 0 (cap at 0).
- Keep numbers realistic.
"""

    result = call_gemini(prompt)

    if result.startswith("ERROR_CALLING_GEMINI:"):
        return {"error": result, "project": PROJECT_ID, "vertex_region": VERTEX_REGION, "model": GEMINI_MODEL}

    try:
        cleaned = strip_code_fences(result)
        parsed = json.loads(cleaned)

        # Persist totals (simple day tracker)
        if "updated_totals" in parsed:
            state["consumed"] = parsed["updated_totals"]
            save_state(req.user_id, state)

        return parsed

    except Exception as e:
        return {
            "error": "JSON_PARSE_FAILED",
            "details": str(e),
            "raw": result[:2000]  # avoid dumping unlimited text
        }