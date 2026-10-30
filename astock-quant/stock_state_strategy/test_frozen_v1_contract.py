"""Implementation repairs must preserve the user-mandated frozen V1 rules."""
import hashlib
from pathlib import Path
import unittest


class FrozenV1ContractTests(unittest.TestCase):
    def test_frozen_strategy_document_and_machine_rules_are_byte_identical(self):
        root = Path(__file__).resolve().parent
        expected = {
            "LOCK_TO_MARKUP_PAPER_STRATEGY.md": "7ff70fff76c3f2bf0bbc61288baf94b41ac9c402b7c622efd90a12b82b7129af",
            "lock_to_markup_paper_strategy.json": "7d1eab2cf611957eedd5846c1349d36374b410234dcef1450ca922da4b8a6e31",
        }
        for filename, digest in expected.items():
            with self.subTest(filename=filename):
                self.assertEqual(hashlib.sha256((root / filename).read_bytes()).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
