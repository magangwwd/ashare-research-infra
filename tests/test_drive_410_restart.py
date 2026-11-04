"""Bounded HTTP 410 recovery tests: never switch production on ambiguous uploads."""
from types import SimpleNamespace

import pytest

from data_pipeline import drive_publish as dp


def response(code, *, headers=None, body=None):
    return SimpleNamespace(status_code=code, headers=headers or {}, json=lambda: body or {})


class ExpiringSession:
    def __init__(self, *, expired=("id1",), metadata="missing", post_expired=False):
        self.ids = iter(("id1", "id2", "id3"))
        self.expired = set(expired)
        self.metadata = metadata
        self.post_expired = post_expired
        self.posts = []
        self.puts = []
        self.lookups = []

    def request(self, method, url, **kwargs):
        if method == "GET" and url.endswith("/generateIds"):
            return response(200, body={"ids": [next(self.ids)]})
        if method == "GET" and "/drive/v3/files/id" in url:
            ident = url.rsplit("/", 1)[-1]
            self.lookups.append(ident)
            if self.metadata == "missing":
                return response(404)
            if self.metadata == "mismatch":
                return response(200, body={"id": ident, "name": "wrong.parquet"})
            return response(200, body={
                "id": ident, "name": "part.parquet", "mimeType": "application/octet-stream",
                "size": "7", "parents": ["staging"], "trashed": False})
        if method == "POST":
            if self.post_expired:
                return response(410)
            ident = kwargs["json"]["id"]
            self.posts.append(ident)
            return response(200, headers={"Location": dp.UPLOAD + "?upload_id=" + ident})
        if method == "PUT":
            ident = url.split("upload_id=", 1)[1]
            self.puts.append(ident)
            return response(410 if ident in self.expired else 200)
        raise AssertionError((method, url))


def test_410_expired_session_restarts_once_after_confirmed_404(tmp_path):
    path = tmp_path / "part.parquet"
    path.write_bytes(b"payload")
    session = ExpiringSession()
    result = dp.Drive(session).upload("staging", path.name, path)
    assert result == "id2"
    assert session.posts == ["id1", "id2"]
    assert session.lookups == ["id1"]
    assert session.puts.count("id1") == 2  # chunk and status probe


def test_410_second_expiry_fails_bounded_and_does_not_start_third(tmp_path):
    path = tmp_path / "part.parquet"
    path.write_bytes(b"payload")
    session = ExpiringSession(expired=("id1", "id2"))
    with pytest.raises(dp.PublishError, match="expired twice"):
        dp.Drive(session).upload("staging", path.name, path)
    assert session.posts == ["id1", "id2"]
    assert session.lookups == ["id1", "id2"]


def test_410_already_committed_matching_file_reuses_original_id(tmp_path, monkeypatch):
    path = tmp_path / "part.parquet"
    path.write_bytes(b"payload")
    session = ExpiringSession(metadata="matching")
    drive = dp.Drive(session)
    monkeypatch.setattr(drive, "remote_digest", lambda ident: dp.digest(path))
    assert drive.upload("staging", path.name, path) == "id1"
    assert session.posts == ["id1"]


def test_410_unverified_remote_file_blocks_new_session(tmp_path):
    path = tmp_path / "part.parquet"
    path.write_bytes(b"payload")
    session = ExpiringSession(metadata="mismatch")
    with pytest.raises(dp.PublishError, match="unverified file"):
        dp.Drive(session).upload("staging", path.name, path)
    assert session.posts == ["id1"]


def test_410_start_session_is_not_treated_as_expired_put(tmp_path):
    path = tmp_path / "part.parquet"
    path.write_bytes(b"payload")
    session = ExpiringSession(post_expired=True)
    with pytest.raises(dp.PublishError, match="operation=start_resumable_upload"):
        dp.Drive(session).upload("staging", path.name, path)
    assert session.lookups == []
