import re
from pathlib import Path

content = Path("errors/20261006_010209_Design_and_Analysis_of_Algorithms_-_Lab_2.2.html").read_text(encoding='utf-8')

# Look for monaco or code areas
print("Has monaco-editor:", 'monaco-editor' in content)
# Find buttons
btns = re.findall(r'<button[^>]*>(.*?)</button>', content, re.DOTALL | re.IGNORECASE)
for b in btns:
    t = re.sub(r'<[^>]+>', '', b).strip()
    if t:
        print("Button:", t)
