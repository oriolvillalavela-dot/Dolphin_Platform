
import unittest
import json
import os
import sys
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session

# Add current directory to path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Import app logic
import app as application
import database
from models import Base, PlateDesign

# --- Patch Database ---
# Use in-memory SQLite
test_engine = create_engine("sqlite:///:memory:")
Base.metadata.create_all(test_engine)
TestSessionLocal = scoped_session(sessionmaker(bind=test_engine))

# Patch the SessionLocal and engine in app module AND database module
application.SessionLocal = TestSessionLocal
application.engine = test_engine
database.SessionLocal = TestSessionLocal
database.engine = test_engine
# Mock ensure_schema since it uses Postgres-specific SQL logic
application.ensure_schema = lambda: None

class TestPlateDesigner(unittest.TestCase):
    def setUp(self):
        application.app.config['TESTING'] = True
        application.app.config['PROPAGATE_EXCEPTIONS'] = True
        self.client = application.app.test_client()
        self.session = TestSessionLocal()
        
    def tearDown(self):
        self.session.close()
        # Clean up data? In memory persists until close?
        # Recreating tables is cleaner but slower. 
        # For 2 tests it's fine.
        Base.metadata.drop_all(test_engine)
        Base.metadata.create_all(test_engine)

    def test_save_and_load_plate(self):
        # 1. Save
        payload = {
            "name": "Test Plate Unit 1",
            "plate_type": "96",
            "plate_metadata": {"eln_id": "ELN-TEST-001", "rxn_date": "2023-10-27"},
            "assignments": [
                {"scope": "row", "target": "A", "role": "solvent", "data": {"chem_id": "chem_1", "name": "Solvent A"}, "behavior": "overwrite"},
                {"scope": "col", "target": "1", "role": "aryl", "data": {"chem_id": "chem_2", "name": "Aryl B"}, "behavior": "overwrite"}
            ]
        }
        resp = self.client.post("/api/plates", json=payload)
        self.assertEqual(resp.status_code, 200, f"Save failed: {resp.get_json()}")
        data = resp.get_json()
        self.assertTrue(data["ok"])
        plate_id = data["id"]
        
        # 2. Load
        resp = self.client.get(f"/api/plates/{plate_id}")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["plate"]["name"], "Test Plate Unit 1")
        self.assertEqual(len(data["plate"]["assignments"]), 2)
        
    def test_export_surf(self):
        # We need to ensure Chemicals exist in DB for resolution logic to work?
        # The logic in export_surf tries to look up chemicals if 'chem_id' is present.
        # If not found, it falls back to assignment data.
        # So it should work even without pre-populating chemicals table, just won't find CAS etc from DB.
        
        payload = {
            "plate_type": "96",
            "meta": {"eln_id": "ELN-TEST-EXPORT", "rxn_date": "2023-10-27"},
            "assignments": [
                {"scope": "row", "target": "A", "role": "solvent", "data": {"chem_id": "chem_1", "name": "Solvent A"}}
            ]
        }
        resp = self.client.post("/plates/export_surf", json=payload)
        self.assertEqual(resp.status_code, 200, f"Export failed: {resp.data}")
        
        # Verify headers
        cd = resp.headers.get("Content-Disposition", "")
        self.assertIn("attachment", cd)
        self.assertIn("filename=SURF_ELN-TEST-EXPORT.xlsx", cd)
        
        # Verify content type
        self.assertIn("spreadsheetml", resp.content_type)
        
        # Verify non-empty body
        self.assertTrue(len(resp.data) > 0)

if __name__ == "__main__":
    # Log to file to avoid console issues
    with open("test_results_log.txt", "w") as f:
        runner = unittest.TextTestRunner(stream=f, verbosity=2)
        try:
             unittest.main(testRunner=runner, exit=False)
        except Exception as e:
             f.write(f"\nCRITICAL ERROR: {e}\n")
