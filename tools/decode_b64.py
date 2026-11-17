import json
import base64
import sys

def main():
    src = sys.argv[1]
    dst = sys.argv[2]
    with open(src, 'r', encoding='utf-8') as f:
        text = f.read().strip()
    if '```json' in text:
        start = text.find('```json') + len('```json')
        end = text.rfind('```')
        text = text[start:end].strip()
    elif 'Result:\n' in text:
        text = text.split('Result:\n', 1)[1].strip()
    b64_data = json.loads(text)
    if isinstance(b64_data, str) and ',' in b64_data:
        b64_data = b64_data.split(',', 1)[1]
    raw = base64.b64decode(b64_data)
    with open(dst, 'wb') as out:
        out.write(raw)
    print(f"Decoded {len(raw)} bytes to {dst}")

if __name__ == '__main__':
    main()
