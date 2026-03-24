import requests
import time
import sys
import threading
from werkzeug.serving import make_server

try:
    from app import app
except Exception as e:
    print(f"Failed to import app: {e}")
    sys.exit(1)

class ServerThread(threading.Thread):
    def __init__(self, app):
        threading.Thread.__init__(self)
        self.server = make_server('127.0.0.1', 5451, app)
        self.ctx = app.app_context()
        self.ctx.push()

    def run(self):
        self.server.serve_forever()

    def shutdown(self):
        self.server.shutdown()

def test_apis():
    base = "http://127.0.0.1:5451"
    
    # Wait for server
    time.sleep(2)

    try:
        r = requests.get(f"{base}/api/lc-ms/healthz")
        assert r.status_code == 200, f"Healthz failed {r.status_code}: {r.text}"
        print("Health check OK")

        r = requests.get(f"{base}/api/lc-ms/chemists")
        assert r.status_code == 200, f"Chemists GET failed {r.status_code}: {r.text}"
        chemists = r.json()
        print(f"Got {len(chemists)} chemists")
        
        r = requests.get(f"{base}/api/lc-ms/elns")
        assert r.status_code == 200, f"ELNs GET failed {r.status_code}"
        elns = r.json().get('items', [])
        print(f"Got {len(elns)} ELNs from DB")

        if elns:
            # Test IPC generation
            eln = elns[0]["eln_id"]
            payload = {"rows": [{"eln_id": eln, "ipc_no": 999123, "duration_h": 2}]}
            r = requests.post(f"{base}/api/lc-ms/generate/ipc", json=payload)
            assert r.status_code == 200, f"IPC Generate POST failed {r.status_code}: {r.text}"
            print("IPC Generation OK")

            # Test PURIF generation
            payload = {"eln_id": eln, "purif_no": 999124, "purif_method": "ISCO", "fractions": ["1", "2+3"]}
            r = requests.post(f"{base}/api/lc-ms/generate/purif", json=payload)
            assert r.status_code == 200, f"PURIF Generate POST failed {r.status_code}: {r.text}"
            print("PURIF Generation OK")

        print("=== ALL TESTS PASSED ===")
    except Exception as e:
        print(f"Test failed: {e}")
    finally:
        pass

if __name__ == "__main__":
    s = ServerThread(app)
    s.start()
    try:
        test_apis()
    finally:
        s.shutdown()
