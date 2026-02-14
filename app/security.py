import re
from fastapi import Request, HTTPException
from .config import DEMO_MODE, BASIC_AUTH_TOKEN, MAX_INPUT_CHARS, MAX_REQUESTS_PER_DAY
from .store import get_doc

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
    """
    Lightweight per-user per-day limiter stored in Firestore:
    users/{user}/days/{yyyy-mm-dd}/meta.requests_today
    """
    doc = get_doc(user_id)
    snap = doc.get()
    data = snap.to_dict() or {}
    meta = data.get("meta", {})
    used = int(meta.get("requests_today", 0))

    if used >= MAX_REQUESTS_PER_DAY:
        raise HTTPException(status_code=429, detail=f"Rate limit exceeded ({MAX_REQUESTS_PER_DAY}/day).")

    meta["requests_today"] = used + 1
    doc.set({"meta": meta}, merge=True)