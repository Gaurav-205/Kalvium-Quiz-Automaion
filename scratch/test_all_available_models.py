import os
import winreg
from google import genai
from google.genai import types

key = os.environ.get("GEMINI_API_KEY")
if not key:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as k:
            key, _ = winreg.QueryValueEx(k, "GEMINI_API_KEY")
    except Exception:
        pass

client = genai.Client(api_key=key)

candidates = [
    "gemini-3.1-flash-lite",
    "gemini-3.1-flash-lite-preview",
    "gemini-3.5-flash-lite",
    "gemini-3.5-flash",
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3-flash-preview",
    "gemini-3.1-pro-preview",
    "gemini-pro-latest",
    "gemma-4-31b-it",
    "gemma-4-26b-a4b-it",
]

working = []
for m in candidates:
    try:
        resp = client.models.generate_content(
            model=m,
            contents='Return JSON: {"answer": 1}',
            config=types.GenerateContentConfig(response_mime_type="application/json")
        )
        print(f"SUCCESS {m}: {resp.text.strip()[:60]}")
        working.append(m)
    except Exception as e:
        err = f"{type(e).__name__}: {str(e)[:80]}"
        print(f"FAIL    {m}: {err}")

print("\nWORKING MODELS:", working)
