"""Offline immutable original archive availability selection tests."""
import datetime as dt
import importlib.util
import io
import sys
from pathlib import Path
from unittest import TestCase
from urllib.error import HTTPError

p=Path(__file__).resolve().parents[1]/"data"/"select_available_day.py"
spec=importlib.util.spec_from_file_location("btc_availability_selector_test",p)
mod=importlib.util.module_from_spec(spec)
sys.modules[spec.name]=mod
spec.loader.exec_module(mod)


class FakeResponse:
    def __init__(self,data=b"PKxx"):
        self.data=data
    def __enter__(self):
        return self
    def __exit__(self,*args):
        return False
    def read(self,n):
        return self.data[:n]


class AvailabilityTests(TestCase):
    def test_latest_published(self):
        today=dt.date(2026,10,16)
        def go(req,timeout):
            return FakeResponse()
        actual,checked=mod.select_latest_published(today,3,go)
        self.assertEqual(actual,today)
        self.assertEqual(len(checked),1)

    def test_404_fallback_is_explicit(self):
        today=dt.date(2026,10,16)
        def go(req,timeout):
            if "2026-10-16" in req.full_url:
                raise HTTPError(req.full_url,404,"Not Found",{},None)
            return FakeResponse()
        actual,checked=mod.select_latest_published(today,3,go)
        self.assertEqual(actual,dt.date(2026,10,15))
        self.assertEqual([x["http"] for x in checked],[404,200])

    def test_non_404_fails_closed(self):
        today=dt.date(2026,10,16)
        def go(req,timeout):
            raise HTTPError(req.full_url,500,"Bad",{},None)
        with self.assertRaises(HTTPError):
            mod.select_latest_published(today,3,go)

    def test_not_zip_never_accepted(self):
        with self.assertRaises(ValueError):
            mod.select_latest_published(dt.date(2026,10,16),0,lambda req,timeout:FakeResponse(b"HTML"))
