import os
import winreg
from google import genai

key = os.environ.get("GEMINI_API_KEY")
if not key:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as k:
            key, _ = winreg.QueryValueEx(k, "GEMINI_API_KEY")
    except Exception:
        pass

client = genai.Client(api_key=key)

test_models = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-1.5-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.5-flash-lite",
]

for m in test_models:
    try:
        resp = client.models.generate_content(model=m, contents="Say hello in one word")
        print(f"SUCCESS with {m}: {resp.text.strip()}")
    except Exception as e:
        print(f"FAILED {m}: {type(e).__name__}: {str(e)[:150]}")
