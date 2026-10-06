import re
from pathlib import Path

html = Path("snapshots/20261005_024602_learning_path.html").read_text(encoding="utf-8")
pattern = re.compile(r'href="([^"]+)".*?bg-\[#([0-9a-fA-F]+)\].*?<span[^>]*>(\d+\.\d+)</span>', re.DOTALL)

for m in pattern.finditer(html):
    href, color, num = m.groups()
    is_bright_green = color.upper() == "16A34A"
    print(f"LU {num:>4} | Color: #{color} | Bright Green: {is_bright_green} | href: {href}")
