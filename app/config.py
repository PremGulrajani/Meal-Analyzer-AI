import os

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("PROJECT_ID")
VERTEX_REGION = os.environ.get("VERTEX_REGION", "us-east1")
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-2.0-flash-lite-001")

USDA_API_KEY = os.environ.get("USDA_API_KEY")
USDA_API_BASE = "https://api.nal.usda.gov/fdc/v1"

DEMO_MODE = os.environ.get("DEMO_MODE", "1") == "1"
BASIC_AUTH_TOKEN = os.environ.get("BASIC_AUTH_TOKEN", "")  # used only if DEMO_MODE=0

MAX_INPUT_CHARS = int(os.environ.get("MAX_INPUT_CHARS", "400"))
MAX_REQUESTS_PER_DAY = int(os.environ.get("MAX_REQUESTS_PER_DAY", "50"))