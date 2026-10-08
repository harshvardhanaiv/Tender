import json

path = r'C:\Users\harsh\.gemini\antigravity-ide\brain\3e418c0f-9cdc-42f5-b9e9-2ed063186aa1\.system_generated\logs\transcript.jsonl'
with open(path, 'r', encoding='utf-8') as f:
    for i, line in enumerate(f):
        data = json.loads(line)
        if data.get('type') == 'USER_INPUT':
            print(f"=== INPUT {i} ===")
            print(data.get('content'))
            print("="*40)
