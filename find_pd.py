
with open('app.py', 'r', encoding='utf-8') as f:
    for i, line in enumerate(f):
        if 'plate_designer.html' in line:
            print(f"{i+1}: {line.strip()}")
