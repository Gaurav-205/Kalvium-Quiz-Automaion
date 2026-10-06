import re
from pathlib import Path

content = Path("errors/20261006_012204_Integrated_Work_-_III_2.12.html").read_text(encoding='utf-8')
pr_idx = content.find('id="pr"')
if pr_idx != -1:
    print(content[pr_idx - 100:pr_idx + 800])
