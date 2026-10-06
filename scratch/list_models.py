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

for m in client.models.list():
    if "generateContent" in (m.supported_actions or []):
        print(f"Model: {m.name} | Display: {m.display_name}")
