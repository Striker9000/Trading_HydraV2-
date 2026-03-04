import os, sys
sys.stdout.reconfigure(encoding='utf-8')

def ok(ch):
    try:
        ch.encode('cp1252')
        return True
    except Exception:
        return False

for root, dirs, files in os.walk('src/trading_hydra'):
    dirs[:] = [d for d in dirs if d not in ['tests', '__pycache__']]
    for fname in files:
        if not fname.endswith('.py'):
            continue
        path = os.path.join(root, fname)
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        for i, line in enumerate(content.splitlines(), 1):
            bad = [ch for ch in line if ord(ch) > 127 and not ok(ch)]
            if bad:
                print(f"{path}:{i}: {[hex(ord(c)) for c in set(bad)]} | {line.strip()[:80]}")
