"""No-network security / format tests for bounded official 400-level L2 archive."""
import gzip
import io
import importlib.util
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

src=Path(__file__).resolve().parents[1]/"data"/"okx_official_l2_download.py"
spec=importlib.util.spec_from_file_location("official_l2_downloader_test",src)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
NAME="BTC-USDT-SWAP-L2orderbook-400lv-2026-10-10.tar.gz"
URL="https://static.okx.com/cdn/okx/match/orderbook/pro/L2/400lv/daily/20261010/"+NAME


def catalog():
    return {"coverage_status":"METADATA_ONLY_NO_FILE_DOWNLOADED",
            "response_sha256":"abc",
            "request":{"module":"4","instType":"SWAP","instFamilyList":"BTC-USDT",
                       "dateAggrType":"daily","begin":"1791590400000","end":"1791590400000"},
            "raw_response":{"data":[{"details":[{
                "instType":"SWAP","instFamily":"BTC-USDT",
                "groupDetails":[{"filename":NAME,"url":URL,"sizeMB":"164.92"}]
            }]}]}}


class Response(io.BytesIO):
    status=206
    def __init__(self, payload, header, url):
        super().__init__(payload)
        self.headers={"Content-Range":header}
        self.url=url


class L2DownloaderTests(unittest.TestCase):
    def test_catalog_whitelist(self):
        name,url,size=mod.select_file(catalog())
        self.assertEqual((name,url,size),(NAME,URL,164.92))
        bad=catalog();bad["raw_response"]["data"][0]["details"][0]["groupDetails"][0]["url"]="https://example.org/huge.zip"
        with self.assertRaises(ValueError):
            mod.select_file(bad)

    def test_exact_http_range_and_bounded_response(self):
        def success(req,timeout):
            self.assertEqual(req.headers["Range"],"bytes=0-3")
            return Response(b"test","bytes 0-3/100",URL)
        data,total=mod.bounded_fetch(URL,0,3,opener=success)
        self.assertEqual((data,total),(b"test",100))
        with self.assertRaises(ValueError):
            mod.bounded_fetch(URL,0,3,opener=lambda req,timeout:Response(b"test","bytes 1-4/100",URL))
        with self.assertRaises(ValueError):
            mod.bounded_fetch(URL,0,3,opener=lambda req,timeout:Response(b"test","bytes 0-3/500000000",URL))
        with self.assertRaises(ValueError):
            mod.bounded_fetch(URL,0,3,opener=lambda req,timeout:Response(b"test","bytes 0-3/100","https://bad.invalid/"))

    def test_valid_tar_gzip_and_truncated_gzip(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"fixture.tar.gz"
            payload=b"OKX_TEST_TRADE_EVENT\n"
            with tarfile.open(p,"w:gz") as out:
                info=tarfile.TarInfo("snapshot.csv")
                info.size=len(payload)
                out.addfile(info,io.BytesIO(payload))
            members,expanded=mod.validate_tar_gz(p)
            self.assertEqual((members,expanded),(1,len(payload)))
            p.write_bytes(p.read_bytes()[:-8])
            with self.assertRaises(ValueError):
                mod.validate_tar_gz(p)

    def test_reject_unsafe_tar_member(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"unsafe.tar.gz"
            with tarfile.open(p,"w:gz") as out:
                info=tarfile.TarInfo("../../escape")
                info.size=1
                out.addfile(info,io.BytesIO(b"x"))
            with self.assertRaises(ValueError):
                mod.validate_tar_gz(p)


if __name__=="__main__":
    unittest.main()
