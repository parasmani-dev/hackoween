import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from simulation.provenance import prepare_attempt


class ProvenanceTests(unittest.TestCase):
    def test_slot_claim_is_persistent_and_exclusive_without_credentials(self):
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            attempt = root / "attempt-one"
            attempt.mkdir()
            args = SimpleNamespace(provider="claude", command="Fetch the round thing",
                                   max_decisions=3, official_slot=1, official_ledger=root / "ledger")
            with patch.dict("os.environ", {"CLAUDE_MODEL": "offline-model", "ANTHROPIC_API_KEY": "must-not-log"}):
                record = prepare_attempt(args, attempt)
            self.assertEqual(record["model"], "offline-model")
            self.assertNotIn("must-not-log", (attempt / "provenance.json").read_text())
            second = root / "attempt-two"
            second.mkdir()
            with self.assertRaises(FileExistsError):
                prepare_attempt(args, second)
            claim = json.loads((root / "ledger/slot-1.json").read_text())
            self.assertEqual(claim["attempt_id"], "attempt-one")
            self.assertFalse((second / "provenance.json").exists())


if __name__ == "__main__":
    unittest.main()
