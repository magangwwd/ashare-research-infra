"""Deterministic REST pagination tests: no live network or Drive credentials."""
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

target = Path(__file__).resolve().parents[1] / "data" / "okx_history_trades.py"
spec = importlib.util.spec_from_file_location("okx_history_module", target)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
INSTRUMENT = "BTC-USDT-SWAP"


def row(n):
    return {"instId": INSTRUMENT, "tradeId": str(n), "ts": "1790000000000",
            "side": "buy", "px": "62000.0", "sz": "0.01"}


class FakeOpener:
    def __init__(self, pages):
        self.pages = list(pages)
        self.requests = []

    def __call__(self, request, timeout):
        self.requests.append(request.full_url)
        if not self.pages:
            raise AssertionError("Unexpected network request")
        return io.BytesIO(json.dumps({"code": "0", "data": self.pages.pop(0)}).encode())


class ArchiveTests(unittest.TestCase):
    def test_successful_cross_run_resume_and_empty_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            first = FakeOpener([[row(100), row(99)]])
            state = module.download_pages(out, page_limit=1, opener=first, sleeper=lambda _: None)
            self.assertEqual((state["pages"], state["after"]), (1, "99"))
            self.assertNotIn("after=", first.requests[0])
            second = FakeOpener([[row(98), row(97)], []])
            state = module.download_pages(out, page_limit=2, opener=second, sleeper=lambda _: None)
            self.assertEqual((state["pages"], state["after"], state["complete"]), (2, "97", True))
            self.assertIn("after=99", second.requests[0])
            self.assertIn("after=97", second.requests[1])
            self.assertEqual(len(list(out.glob("*page_*.jsonl"))), 2)

    def test_recover_orphan_file_before_checkpoint_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            module.download_pages(out, page_limit=1,
                                  opener=FakeOpener([[row(100), row(99)]]), sleeper=lambda _: None)
            path = out / module.page_name(INSTRUMENT, 1)
            path.write_text(json.dumps(row(98)) + "\n" + json.dumps(row(97)) + "\n")
            no_net = FakeOpener([])
            state = module.download_pages(out, page_limit=1, opener=no_net, sleeper=lambda _: None)
            self.assertEqual((state["pages"], state["after"]), (2, "97"))
            self.assertEqual(no_net.requests, [])

    def test_tampered_committed_page_fails_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            module.download_pages(out, page_limit=1,
                                  opener=FakeOpener([[row(100)]]), sleeper=lambda _: None)
            page = out / module.page_name(INSTRUMENT, 0)
            page.write_text(json.dumps(row(101)) + "\n")
            with self.assertRaises(ValueError):
                module.download_pages(out, page_limit=1, opener=FakeOpener([]))

    def test_reject_invalid_order_and_cross_instrument(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                module.download_pages(Path(tmp), page_limit=1,
                                      opener=FakeOpener([[row(98), row(99)]]), sleeper=lambda _: None)
            bad = row(100)
            bad["instId"] = "OTHER"
            with self.assertRaises(ValueError):
                module.download_pages(Path(tmp), page_limit=1,
                                      opener=FakeOpener([[bad]]), sleeper=lambda _: None)

    def test_bounds_and_missing_checkpoint_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            with self.assertRaises(ValueError):
                module.download_pages(out, page_limit=31, opener=FakeOpener([]))
            module.download_pages(out, page_limit=1,
                                  opener=FakeOpener([[row(100)]]), sleeper=lambda _: None)
            (out / module.page_name(INSTRUMENT, 0)).unlink()
            with self.assertRaises(ValueError):
                module.download_pages(out, page_limit=1, opener=FakeOpener([]))


if __name__ == "__main__":
    unittest.main()
