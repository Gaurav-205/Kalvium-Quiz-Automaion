import re
from pathlib import Path
from html.parser import HTMLParser

html_paths = [
    "errors/20261006_010209_Design_and_Analysis_of_Algorithms_-_Lab_2.2.html",
    "errors/20261006_011952_Integrated_Work_-_III_2.9.html",
    "errors/20261006_012204_Integrated_Work_-_III_2.12.html",
    "errors/20261006_012808_Integrated_Work_-_III_2.19.html",
]

for p in html_paths:
    path = Path(p)
    if not path.exists():
        continue
    content = path.read_text(encoding='utf-8')
    print(f"\n==================== {path.name} ====================")
    
    # Buttons
    btns = re.findall(r'<button[^>]*>(.*?)</button>', content, re.DOTALL | re.IGNORECASE)
    clean_btns = [re.sub(r'<[^>]+>', '', b).strip() for b in btns if re.sub(r'<[^>]+>', '', b).strip()]
    print("Buttons:", clean_btns[:15])
    
    # Inputs
    inputs = re.findall(r'<input[^>]*>', content, re.IGNORECASE)
    print("Inputs count:", len(inputs))
    for inp in inputs[:10]:
        print("  Input:", inp[:120])
        
    # Textareas
    tas = re.findall(r'<textarea[^>]*>', content, re.IGNORECASE)
    print("Textareas count:", len(tas))
    for ta in tas:
        print("  Textarea:", ta[:150])
        
    # Contenteditable
    ces = re.findall(r'<[^>]+contenteditable[^>]*>', content, re.IGNORECASE)
    print("Contenteditable elements:", len(ces))
    for ce in ces[:5]:
        print("  CE:", ce[:120])
        
    # Iframes
    iframes = re.findall(r'<iframe[^>]*src=[\'"]([^\'"]*)[\'"]', content, re.IGNORECASE)
    print("Iframes:", iframes)
    
    # Interesting classes
    classes = set(re.findall(r'class=[\'"]([^\'"]*(?:editor|monaco|cm-|workspace|submit|solve)[^\'"]*)[\'"]', content, re.IGNORECASE))
    print("Matching classes:", list(classes)[:10])
