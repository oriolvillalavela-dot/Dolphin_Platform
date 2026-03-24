
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
from models import Base, Chemical

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
# Mock ensure_schema
application.ensure_schema = lambda: None

class TestApiSearch(unittest.TestCase):
    def setUp(self):
        application.app.config['TESTING'] = True
        self.client = application.app.test_client()
        self.session = TestSessionLocal()
        
        # Seed some data
        c1 = Chemical(chem_id="chem_1", common_name_abb="Benzene", cas="71-43-2", smiles="c1ccccc1")
        c2 = Chemical(chem_id="chem_2", common_name_abb="Acetone", cas="67-64-1", smiles="CC(=O)C")
        self.session.add(c1)
        self.session.add(c2)
        self.session.commit()
        
    def tearDown(self):
        self.session.close()
        Base.metadata.drop_all(test_engine)
        Base.metadata.create_all(test_engine)

    def test_api_search_chemicals(self):
        # 1. Broad search
        resp = self.client.get("/api/chemicals/search?q=ben")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['ok'])
        self.assertEqual(len(data['results']), 1)
        self.assertEqual(data['results'][0]['common_name'], "Benzene")
        
        # 2. CAS search
        resp = self.client.get("/api/chemicals/search?q=67-64")
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(len(data['results']), 1)
        self.assertEqual(data['results'][0]['common_name'], "Acetone")

    def test_api_autofill(self):
        # Match
        resp = self.client.post("/api/autofill", json={"value": "chem_1"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertTrue(data['ok'])
        self.assertEqual(data['item']['common_name_abb'], "Benzene")
        
        # No match
        resp = self.client.post("/api/autofill", json={"value": "chem_999"})
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertFalse(data['ok'])

if __name__ == "__main__":
    unittest.main()
