"""Behavior checks for source failures, chronology, revisions and publication safety."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from overseas_research import collect as c
from overseas_research import drive as d

NOW = datetime(2026, 10, 29, 1, tzinfo=timezone.utc)
TSMC = b'<h1>2026 Monthly Revenue</h1><p>Millions of New Taiwan Dollars</p><table><tr><td>Sept.</td><td>511,857</td><td>54.6%</td></tr><tr><td>Oct.</td><td></td><td></td></tr></table>'
TREASURY = b'<feed><entry><content><properties><NEW_DATE>2026-10-28T00:00:00</NEW_DATE><TC_10YEAR>2.67</TC_10YEAR></properties></content></entry></feed>'


def eia():
    return json.dumps({"metadata": {"release_date": "2026-10-28", "release_time": "10:30 am"},
        "data": {"U.S.": {"sourcekey": "WCESTUS1", "units": "thousand barrels", "time_series": [
            {"date": "2026-10-16", "value": 420000, "suppression_flag": None},
            {"date": "2026-10-23", "value": 421000, "suppression_flag": None}]}}}).encode()


def sec():
    sub = {"cik": "1045810", "filings": {"recent": {"accessionNumber": ["0001045810-26-000001"],
        "filingDate": ["2026-10-28"], "form": ["10-Q"], "primaryDocument": ["report.htm"],
        "acceptanceDateTime": ["2026-10-28T20:00:00Z"]}}}
    points = [{"start": "2026-07-01", "end": "2026-09-30", "filed": "2026-10-28", "val": 100,
               "form": "10-Q", "accn": "0001045810-26-000001"},
              {"start": "2026-01-01", "end": "2026-09-30", "filed": "2026-10-28", "val": 250,
               "form": "10-Q", "accn": "0001045810-26-000001"}]
    facts = {"cik": 1045810, "facts": {"us-gaap": {"Revenues": {"units": {"USD": points}}}}}
    return json.dumps(sub).encode(), json.dumps(facts).encode()


class Sources(unittest.TestCase):
    def test_tsmc_blank_future_month_is_not_zero(self):
        r = c.parse_tsmc(TSMC, NOW)
        self.assertEqual([(x["period"], x["actual"], x["unit"]) for x in r], [("2026-09-30", 511857.0, "TWD_million")])

    def test_challenge_page_is_not_data(self):
        with self.assertRaises(ValueError):
            c.parse_tsmc(b'<html>Please verify you are human</html>', NOW)

    def test_wrong_currency_fails(self):
        with self.assertRaises(ValueError):
            c.parse_tsmc(TSMC.replace(b'New Taiwan Dollars', b'US Dollars'), NOW)

    def test_treasury_real_not_nominal_tag(self):
        self.assertEqual(c.parse_treasury(TREASURY, NOW)[0]["actual"], 2.67)
        with self.assertRaises(ValueError):
            c.parse_treasury(TREASURY.replace(b'TC_10YEAR', b'BC_10YEAR'), NOW)

    def test_future_observation_rejected(self):
        with self.assertRaises(ValueError):
            c.parse_treasury(TREASURY.replace(b'2026-10-28', b'2026-11-01'), NOW)

    def test_stale_never_promoted_to_observations(self):
        records = c.parse_treasury(TREASURY.replace(b'2026-10-28', b'2026-10-01'), NOW)
        self.assertEqual(c.freshness('treasury', records, NOW)['status'], 'stale')

    def test_eia_spr_and_units_checked(self):
        r = c.parse_eia(eia(), NOW)
        self.assertEqual(r[-1]['change'], 1000)
        self.assertEqual(r[-1]['published_at'], '2026-10-28T14:30:00+00:00')
        self.assertIsNone(r[0]['published_at'])
        with self.assertRaises(ValueError):
            c.parse_eia(eia().replace(b'WCESTUS1', b'WCRSTUS1'), NOW)

    def test_duplicate_eia_dates_rejected(self):
        with self.assertRaises(ValueError):
            c.parse_eia(eia().replace(b'2026-10-16', b'2026-10-23'), NOW)

    def test_quarter_and_ytd_remain_distinct(self):
        r = c.parse_sec(*sec(), NOW, 'NVDA', '0001045810')
        self.assertEqual({x['duration_kind'] for x in r}, {'quarter', 'year_to_date'})
        self.assertTrue(all(x['consensus'] is None for x in r))

    def test_sec_wrong_company_rejected(self):
        with self.assertRaises(ValueError):
            c.parse_sec(*sec(), NOW, 'AAPL', '0000320193')

    def test_bootstrap_and_delayed_capture_not_new_signal(self):
        r = [c.observation('x', '2026-10-28', 1, 'USD', published_at='2026-10-28T00:00:00+00:00')]
        c.capture(r, 'test', NOW, {})
        self.assertFalse(r[0]['eligible_as_new_event'])
        original = json.loads(json.dumps(r))
        c.capture(r, 'test', NOW, {'generated_at_utc': c.stamp(NOW), 'observations': original})
        self.assertEqual(r[0]['capture_kind'], 'unchanged')
        late = [c.observation('x', '2026-10-27', 9, 'USD', published_at='2026-10-27T00:00:00+00:00')]
        c.capture(late, 'test', NOW, {'generated_at_utc': '2026-10-28T12:00:00+00:00', 'observations': original})
        self.assertFalse(late[0]['eligible_as_new_event'])

    def test_revision_is_retained_not_counted_as_new_publication(self):
        r = [c.observation('x', '2026-10-28', 1, 'USD')]
        c.capture(r, 'test', NOW, {})
        previous = {'generated_at_utc': c.stamp(NOW), 'observations': json.loads(json.dumps(r))}
        r[0]['actual'] = 2
        c.capture(r, 'test', NOW, previous)
        self.assertEqual(r[0]['capture_kind'], 'revision')
        self.assertEqual(r[0]['previous_actual'], 1)
        self.assertFalse(r[0]['eligible_as_new_event'])

    def test_one_source_failure_is_visible_and_others_survive(self):
        def fetch(url):
            if 'tsmc' in url:
                return TSMC
            if 'treasury' in url:
                return TREASURY
            raise TimeoutError('source temporarily unavailable')
        with tempfile.TemporaryDirectory() as root:
            s = c.collect(root, NOW, fetch=fetch, companies={})
            self.assertEqual(s['quality'], 'passed_with_gaps')
            self.assertEqual(s['sources'][-1]['status'], 'missing_or_invalid')
            self.assertNotIn('eia', {o['source_id'] for o in s['observations']})
            self.assertTrue(all(not o['eligible_as_new_event'] for o in s['observations']))
            d.validate_bundle(Path(root))

    def test_all_failed_blocks_publication(self):
        with tempfile.TemporaryDirectory() as root:
            s = c.collect(root, NOW, fetch=lambda _: b'not a feed', companies={})
            self.assertEqual(s['quality'], 'blocked')
            with self.assertRaises(ValueError):
                d.validate_bundle(Path(root))

    def test_late_source_can_be_fetched_on_next_attempt(self):
        with tempfile.TemporaryDirectory() as root:
            first = c.collect(Path(root)/'first', NOW, fetch=lambda _: b'invalid', companies={})
            def recovered(url):
                return TSMC if 'tsmc' in url else TREASURY if 'treasury' in url else eia()
            later = c.collect(Path(root)/'later', NOW, fetch=recovered, previous=first, companies={})
            self.assertEqual(later['quality'], 'passed')
            self.assertTrue(all(not o['eligible_as_new_event'] for o in later['observations']))


class Response:
    def __init__(self, doc=None, raw=b'', status=200, headers=None):
        self._doc, self.content, self.status_code, self.headers = doc, raw, status, headers or {}
    def json(self):
        return self._doc if self._doc is not None else json.loads(self.content)


class FolderSession:
    def __init__(self):
        self.pointer_writes = []
    def get(self, url, **kwargs):
        return Response({'id': d.FOLDER_ID, 'mimeType': 'application/vnd.google-apps.folder',
                         'capabilities': {'canAddChildren': True}})
    def post(self, url, **kwargs):
        return Response({'id': 'version-folder'})
    def patch(self, *args, **kwargs):
        self.pointer_writes.append(kwargs)
        return Response({})


class Publication(unittest.TestCase):
    def bundle(self, root):
        c.collect(root, NOW, fetch=lambda u: TSMC if 'tsmc' in u else TREASURY if 'treasury' in u else eia(), companies={})

    def test_changed_raw_is_rejected_before_any_drive_write(self):
        with tempfile.TemporaryDirectory() as root:
            self.bundle(root)
            (Path(root)/'raw/tsmc_0.bin').write_bytes(b'changed')
            with self.assertRaises(ValueError):
                d.validate_bundle(Path(root))

    def test_failed_upload_preserves_existing_pointer(self):
        session = FolderSession()
        with tempfile.TemporaryDirectory() as root:
            self.bundle(root)
            old = {'generated_at_utc': '2026-10-28T00:00:00+00:00'}
            pointer = {'file_id': 'existing-pointer', 'etag': 'existing-etag'}
            with patch.object(d, 'read_previous', return_value=(old, pointer)), patch.object(d, 'lookup', return_value=None), \
                 patch.object(d, 'upload_verified', side_effect=RuntimeError('remote hash mismatch')):
                with self.assertRaises(RuntimeError):
                    d.publish(root, session, 'test-1')
        self.assertEqual(session.pointer_writes, [])

    def test_inconsistent_quality_report_is_blocked(self):
        with tempfile.TemporaryDirectory() as root:
            self.bundle(root)
            p = Path(root)/'quality.json'
            doc = json.loads(p.read_text())
            doc['quality'] = 'blocked'
            p.write_text(json.dumps(doc))
            with self.assertRaises(ValueError):
                d.validate_bundle(Path(root))

    def test_remote_byte_corruption_is_rejected(self):
        class CorruptSession:
            def get(self, url, **kwargs):
                return Response(raw=b'corrupted')
            def post(self, url, **kwargs):
                return Response({}, headers={'Location': 'https://www.googleapis.com/upload/allowed'})
            def put(self, url, **kwargs):
                return Response({'id': 'uploaded-id'})
        with patch.object(d, 'lookup', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'remote_readback_hash_mismatch'):
                d.upload_verified(CorruptSession(), 'snapshot.json', b'original', 'folder')

    def test_older_snapshot_cannot_overwrite_current(self):
        session = FolderSession()
        with tempfile.TemporaryDirectory() as root:
            self.bundle(root)
            with patch.object(d, 'read_previous', return_value=({'generated_at_utc': '2026-10-30T00:00:00+00:00'}, {})):
                with self.assertRaises(ValueError):
                    d.publish(root, session, 'test-1')
        self.assertEqual(session.pointer_writes, [])


if __name__ == '__main__':
    unittest.main()
