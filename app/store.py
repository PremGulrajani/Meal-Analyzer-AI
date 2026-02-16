import datetime
from google.cloud import firestore
import hashlib


db = firestore.Client()

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

def _normalize_meal_text(meal_text: str) -> str:
    return " ".join((meal_text or "").strip().lower().split())

def _food_cache_doc(meal_text: str):
    key = hashlib.sha1(_normalize_meal_text(meal_text).encode("utf-8")).hexdigest()
    return db.collection("food_cache").document(key)

def get_food_cache(meal_text: str):
    snap = _food_cache_doc(meal_text).get()
    if not snap.exists:
        return None
    d = snap.to_dict() or {}
    return d.get("tool_data")

def set_food_cache(meal_text: str, tool_data: dict):
    _food_cache_doc(meal_text).set(
        {
            "meal_text": _normalize_meal_text(meal_text),
            "tool_data": tool_data,
            "updated_at": firestore.SERVER_TIMESTAMP,
        },
        merge=True,
    )