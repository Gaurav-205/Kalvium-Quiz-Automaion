import re
from pathlib import Path

content = Path("errors/20261006_012204_Integrated_Work_-_III_2.12.html").read_text(encoding='utf-8')
vid_idx = content.find('id="video"')
if vid_idx != -1:
    print(content[vid_idx - 100:vid_idx + 800])
