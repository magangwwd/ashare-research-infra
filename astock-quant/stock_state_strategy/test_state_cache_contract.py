import tempfile
from pathlib import Path
import unittest
from state_cache_contract import fingerprint, record_cache, verify_cache


class StateCacheContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.cache = self.root / "state.parquet"
        self.cache.write_bytes(b"synthetic cache bytes")
        self.source = self.root / "source.json"
        self.source.write_text("version one")

    def signature(self, end="2026-10-29"):
        return fingerprint({"end": end}, {"source": self.source})

    def test_old_cache_without_metadata_refuses(self):
        with self.assertRaisesRegex(RuntimeError, "rebuild"):
            verify_cache(self.cache, self.signature())

    def test_identical_inputs_and_bytes_reuse(self):
        signature = self.signature()
        record_cache(self.cache, signature)
        verify_cache(self.cache, signature)

    def test_changed_source_refuses(self):
        record_cache(self.cache, self.signature())
        self.source.write_text("version two")
        with self.assertRaises(RuntimeError): verify_cache(self.cache, self.signature())

    def test_changed_end_date_refuses(self):
        record_cache(self.cache, self.signature())
        with self.assertRaises(RuntimeError): verify_cache(self.cache, self.signature("2026-10-30"))

    def test_corrupted_output_refuses(self):
        record_cache(self.cache, self.signature())
        self.cache.write_bytes(b"partial write")
        with self.assertRaises(RuntimeError): verify_cache(self.cache, self.signature())

    def test_malformed_metadata_refuses(self):
        self.cache.with_suffix(".meta.json").write_text("not json")
        with self.assertRaises(RuntimeError): verify_cache(self.cache, self.signature())


if __name__ == "__main__": unittest.main()
