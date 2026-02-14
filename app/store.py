import datetime
from google.cloud import firestore

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