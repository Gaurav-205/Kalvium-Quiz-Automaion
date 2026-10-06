import os, winreg
from google import genai
from google.genai import types

key = os.environ.get('GEMINI_API_KEY')
if not key:
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Environment') as k:
        key, _ = winreg.QueryValueEx(k, 'GEMINI_API_KEY')

client = genai.Client(api_key=key)
models = [
    'gemini-3.5-flash-lite',
    'gemini-3.1-flash-lite',
    'gemini-flash-lite-latest',
    'gemini-3.8-flash',
    'gemini-3.7-flash',
    'gemini-3.6-flash',
    'gemini-flash-latest',
]
for m in models:
    try:
        resp = client.models.generate_content(
            model=m,
            contents='Return JSON {"status": "ok"}',
            config=types.GenerateContentConfig(response_mime_type='application/json')
        )
        print('SUCCESS:', m, resp.text.strip())
    except Exception as e:
        print('FAILED:', m, type(e).__name__, str(e)[:100])
