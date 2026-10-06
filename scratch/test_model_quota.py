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

candidates = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
]

for m in candidates:
    try:
        resp = client.models.generate_content(model=m, contents="Reply with 'OK'")
        print(f"SUCCESS {m}: {resp.text.strip()}")
    except Exception as e:
        print(f"FAILED {m}: {type(e).__name__}: {str(e)[:120]}")
