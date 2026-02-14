from typing import Optional
from pydantic import BaseModel

class Goals(BaseModel):
    calories: int
    protein: int
    carbs: int
    fat: int

class ChatRequest(BaseModel):
    user_id: str = "demo"
    message: str
    mode: Optional[str] = None  # kept for backward compat (UI no longer shows it)

class SetGoalsRequest(BaseModel):
    user_id: str = "demo"
    goals: Goals