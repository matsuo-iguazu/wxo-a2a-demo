import json

with open(r'C:\Users\35576\AppData\Local\Temp\bob-tool-outputs\tooluse_jtL7ncsUN2BgJ7Rd3v8aeh.txt', encoding='utf-8') as f:
    data = json.load(f)

for a in data.get('native', []):
    collab = a.get('collaborators', [])
    kb = a.get('knowledge_base', [])
    kb_names = [k.get('name', '') if isinstance(k, dict) else k for k in kb] if isinstance(kb, list) else []
    print(f"name={a['name']}")
    print(f"  display_name={a.get('display_name', '')}")
    print(f"  id={a['id']}")
    print(f"  collaborators={collab}")
    print(f"  knowledge_bases={kb_names}")
    print()
