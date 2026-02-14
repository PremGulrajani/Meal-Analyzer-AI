import os, json, datetime
from typing import Optional, Dict, Any

from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import vertexai
from vertexai.generative_models import GenerativeModel
from google.cloud import speech
from google.cloud import firestore
from fastapi.responses import HTMLResponse
import re

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("PROJECT_ID")
VERTEX_REGION = os.environ.get("VERTEX_REGION", "us-east1")
USDA_TOOL_URL = os.environ.get("USDA_TOOL_URL")

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

class ChatRequest(BaseModel):
    user_id: str = "demo"
    message: str
    mode: str = "estimate"

def today_key():
    return datetime.date.today().isoformat()

def get_doc(user_id):
    return db.collection("users").document(user_id).collection("days").document(today_key())

def get_state(user_id):
    doc = get_doc(user_id).get()
    if doc.exists:
        return doc.to_dict()
    return {
        "goals": {"calories":2000,"protein":150,"carbs":200,"fat":60},
        "consumed": {"calories":0,"protein":0,"carbs":0,"fat":0}
    }

def save_state(user_id, state):
    get_doc(user_id).set(state, merge=True)


@app.get("/")
def root():
    return {"status":"ok","open":"/ui"}

@app.get("/ui", response_class=HTMLResponse)
def ui():
    return """
    <html>
    <body style="font-family:sans-serif;max-width:700px;margin:40px;">
    <h2>Meal Analyzer (Track B)</h2>
    <textarea id="msg" style="width:100%;height:80px;">I ate 2 eggs and toast</textarea><br><br>
    <select id="mode">
      <option value="estimate">Estimate</option>
      <option value="exact">Exact</option>
    </select>
    <button onclick="send()">Analyze</button>
    <button onclick="rec()">Record Voice</button>
    <pre id="out"></pre>

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

def call_gemini(prompt):
    try:
        vertexai.init(project=PROJECT_ID, location=VERTEX_REGION)
        model = GenerativeModel("gemini-2.0-flash-lite-001")
        resp = model.generate_content(prompt)
        return resp.text
    except Exception as e:
        return f"ERROR_CALLING_GEMINI: {type(e).__name__}: {str(e)}"

@app.post("/chat")
def chat(req: ChatRequest):
    state = get_state(req.user_id)
    goals = state["goals"]
    consumed = state["consumed"]

    prompt = f"""
    User goals: {goals}
    Consumed so far: {consumed}
    Meal: {req.message}

    Return STRICT JSON only:
    {{
        "meal_summary": {{
            "description": "...",
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
        }}
    }}
    """

    result = call_gemini(prompt)

    if result.startswith("ERROR_CALLING_GEMINI:"):
        return {"error": result}

    try:
        # Remove ```json blocks if present
        cleaned = re.sub(r"```json|```", "", result).strip()
        parsed = json.loads(cleaned)

        # Update Firestore
        state["consumed"] = parsed["updated_totals"]
        save_state(req.user_id, state)

        return parsed

    except Exception as e:
        return {
            "error": "JSON_PARSE_FAILED",
            "raw": result,
            "details": str(e)
        }