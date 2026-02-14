import requests
from ..config import USDA_API_KEY, USDA_API_BASE

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