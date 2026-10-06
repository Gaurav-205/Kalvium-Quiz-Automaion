import re
import sys
from html.parser import HTMLParser

filename = sys.argv[1] if len(sys.argv) > 1 else 'errors/20261005_034633_Introduction_to_Philosophy_5.6.html'
html = open(filename, encoding='utf-8').read()

print("File:", filename)
# Find h1, h2
h1s = re.findall(r'<h1[^>]*>(.*?)</h1>', html, re.DOTALL | re.IGNORECASE)
h2s = re.findall(r'<h2[^>]*>(.*?)</h2>', html, re.DOTALL | re.IGNORECASE)
print("H1:", [re.sub(r'<[^>]+>', '', h).strip() for h in h1s])
print("H2:", [re.sub(r'<[^>]+>', '', h).strip() for h in h2s])

# Find buttons
btns = re.findall(r'<button[^>]*>(.*?)</button>', html, re.DOTALL | re.IGNORECASE)
cleaned_btns = [re.sub(r'<[^>]+>', '', b).strip() for b in btns]
print("Buttons:", [b for b in cleaned_btns if b][:15])

# Find any kqb marks
print("data-kqb-opt:", len(re.findall(r'data-kqb-opt', html)))
print("data-kqb-btn:", len(re.findall(r'data-kqb-btn', html)))

# Find text around data-kqb-opt
for m in re.finditer(r'<[^>]+data-kqb-opt="(\d+)"[^>]*>(.*?)(?=<[^>]+data-kqb-opt|</div|$)', html, re.DOTALL):
    opt_idx = m.group(1)
    content = re.sub(r'<[^>]+>', ' ', m.group(2)).strip()
    print(f"Opt {opt_idx}: {content[:100]}")
