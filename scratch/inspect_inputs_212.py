import re
from pathlib import Path

content = Path("errors/20261006_012204_Integrated_Work_-_III_2.12.html").read_text(encoding='utf-8')
matches = [m.start() for m in re.finditer(r'<input', content, re.IGNORECASE)]
for idx in matches:
    snippet = content[max(0, idx - 200):min(len(content), idx + 400)]
    print("\n--- INPUT SURROUNDING ---")
    print(snippet)
