
try:
    from app import app
    print("App imported successfully")
except Exception as e:
    print(f"Failed to import app: {e}")
