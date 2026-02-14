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