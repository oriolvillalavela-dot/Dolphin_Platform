
with open('app.py', 'r', encoding='utf-8') as f:
    for i, line in enumerate(f):
        if 'autofill' in line.lower():
            print(f"{i+1}: {line.strip()}")
