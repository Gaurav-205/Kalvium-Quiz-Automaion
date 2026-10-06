import re
from pathlib import Path

content = Path("errors/20261006_005721_Design_and_Analysis_of_Algorithms_5.2.html").read_text(encoding='utf-8')
# Find the fixed inset-0 z-50 element and its top-right buttons
matches = [m.start() for m in re.finditer(r'fixed\s+inset-0', content)]
for idx in matches:
    print("\n--- MODAL HEADER ---")
    snippet = content[idx:idx + 1500]
    print(snippet)
