
import unittest
from unittest.mock import MagicMock, patch
import app as application
from models import Chemical, Batch

class TestAnalysisTool(unittest.TestCase):
    def setUp(self):
        application.app.config['TESTING'] = True
        self.client = application.app.test_client()

    @patch('app.ensure_schema')
    @patch('app.Chem.SDMolSupplier')
    @patch('app.SessionLocal')
    @patch('app.Chem.MolToInchiKey')
    @patch('app.Chem.MolToSmiles')
    @patch('app.Chem.MolToInchi')
    def test_analysis_process(self, mock_inchi, mock_smiles, mock_ikey, mock_session_cls, mock_supplier, mock_schema):
        # Mock RDKit
        mock_mol1 = MagicMock()
        mock_mol1.GetPropsAsDict.return_value = {
            "Supplier Name of Min Lead Time BS": "Roche Basel", 
            "Supplier Substance ID of Min Lead Time BS": "RO12345", 
            "IUPAC Name": "Test Mol 1"
        }
        
        mock_mol2 = MagicMock()
        mock_mol2.GetPropsAsDict.return_value = {
            "Supplier Name of Min Lead Time BS": "roche", 
            "Supplier Substance ID of Min Lead Time BS": "99999", 
            "IUPAC Name": "Test Mol 2"
        }
        
        mock_mol3 = MagicMock()
        mock_mol3.GetPropsAsDict.return_value = {
            "Supplier Name of Min Lead Time BS": "Sigma", 
            "Supplier Substance ID of Min Lead Time BS": "S100", 
            "IUPAC Name": "Test Mol 3"
        }

        # Iterator for Supplier
        mock_supplier.return_value = [mock_mol1, mock_mol2, mock_mol3]
        
        # Mock Structures
        mock_ikey.side_effect = ["KEY1", "KEY2", "KEY3"]
        mock_smiles.return_value = "C"
        mock_inchi.return_value = "InChI=..."

        # Mock DB
        mock_session = MagicMock()
        mock_session_cls.return_value = mock_session
        
        # Query Side Effects
        # 1. Match KEY1 (Mosaic) -> Found
        # 2. Match KEY2 (RMM) -> Not Found
        # 3. Match KEY3 (External) -> Not Found
        
        # We need to handle the chaining: session.query().filter().first()
        # It's called 3 times for Chemicals
        
        match_chem = Chemical(chem_id="chem_1", common_name_abb="Test", inchi_key="KEY1")
        
        # Logic is loop:
        # Mol1: query(Chemical).filter(KEY1).first() -> match_chem
        #       query(Batch).filter(chem_1).all() -> [Batch(loc="A1")]
        # Mol2: query(Chemical).filter(KEY2).first() -> None
        # Mol3: query(Chemical).filter(KEY3).first() -> None
        
        def query_side_effect(model):
            q = MagicMock()
            if model == Chemical:
                def filter_side_effect(*args, **kwargs):
                    # args[0] is binary expression. complex to check.
                    # We can just return a mock that returns specific values on first()
                    f = MagicMock()
                    # We can use a counter or just return different things based on call order if we assume sequential execution
                    return f
                q.filter.side_effect = filter_side_effect
                return q
            elif model == Batch:
                 q = MagicMock()
                 return q
            return q

        # Simplified Mocking:
        # We'll just patch the query return values in order of execution.
        # Calls:
        # 1. Chemical (Mol1) -> Match
        # 2. Batch (Mol1) -> [Batch]
        # 3. Chemical (Mol2) -> None
        # 4. Chemical (Mol3) -> None
        
        # The app uses session.query(Chemical).filter(...).first()
        # So: session.query().filter().first() is called.
        
        # Let's mock the final `first()` and `all()` calls
        
        # We can differentiate by what `filter` was called with, but that's hard in mocks.
        # Let's try to mock `session.query` to return a fresh mock each time, and configure those mocks.
        
        # Actually proper side_effect on `first()` is better.
        
        # But wait, `session.query` is called with Model.
        
        # Let's setup:
        # query_return_chem = MagicMock()
        # query_return_batch = MagicMock()
        # mock_session.query.side_effect = lambda m: query_return_chem if m == Chemical else query_return_batch
        
        # query_return_chem.filter.return_value.first.side_effect = [match_chem, None, None]
        # query_return_batch.filter.return_value.all.return_value = [Batch(location="Shelf A", status="Available")]
        
        # This assumes sequential single-threaded execution which is true for tests.
        
        # REFINED MOCKING:
        q_chem = MagicMock()
        q_batch = MagicMock()
        
        def get_query(model):
            if model == Chemical: return q_chem
            if model == Batch: return q_batch
            return MagicMock()
        
        mock_session.query.side_effect = get_query
        
        # Chemical Query Logic
        # It chains: filter(...) -> first()
        # Mol1: InChIKey (Match)
        # Mol2: InChIKey (None) -> SMILES (None)
        # Mol3: InChIKey (None) -> SMILES (None)
        # Total 5 calls maximum.
        q_chem.filter.return_value.first.side_effect = [match_chem, None, None, None, None, None, None] # Pad extra
        
        # Batch Query Logic
        # filter(...) -> all()
        # Called only if match found (Mol1)
        b1 = Batch(location="Fridge 1", status="Available")
        q_batch.filter.return_value.all.return_value = [b1]

        # File
        from io import BytesIO
        data = {'file': (BytesIO(b"FAKE CONTENT"), 'test.sdf')}
        
        resp = self.client.post('/api/analysis/process', data=data, content_type='multipart/form-data')
        
        if resp.status_code != 200:
            print("Response 500 Error:", resp.get_json())
        self.assertEqual(resp.status_code, 200)
        json_data = resp.get_json()
        
        self.assertTrue(json_data['ok'])
        stats = json_data['stats']
        self.assertEqual(stats['internal'], 2)
        self.assertEqual(stats['external'], 1)
        self.assertEqual(stats['matches'], 1)
        self.assertEqual(stats['orders'], 2) # Mol2 and Mol3
        
        # Check Mosaic
        mosaic = json_data['internal_mosaic']
        self.assertEqual(len(mosaic), 1)
        self.assertEqual(mosaic[0]['supplier_id'], "RO12345")
        self.assertTrue(mosaic[0]['matched'])
        self.assertEqual(mosaic[0]['batches'], 1)
        self.assertIn("Fridge 1", mosaic[0]['location'])
        
        # Check RMM
        rmm = json_data['internal_rmm']
        self.assertEqual(len(rmm), 1)
        self.assertEqual(rmm[0]['supplier_id'], "99999")
        self.assertFalse(rmm[0]['matched'])
        
        # Check External
        ext = json_data['external']
        self.assertEqual(len(ext), 1)
        self.assertEqual(ext[0]['supplier_id'], "S100")
        self.assertFalse(ext[0]['matched'])

if __name__ == "__main__":
    import sys
    with open('explicit_error.log', 'w') as f:
        runner = unittest.TextTestRunner(stream=f, verbosity=2)
        unittest.main(testRunner=runner, exit=False)
