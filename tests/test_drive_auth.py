import json
from types import SimpleNamespace

import pytest

from data_pipeline import drive_auth as auth


def secret(**changes):
    return json.dumps({"client_id": "test-client", "client_secret": "private-client-secret",
                       "refresh_token": "private-refresh-token", **changes})


def test_refresh_cannot_use_an_untrusted_token_endpoint():
    with pytest.raises(auth.SetupError):
        auth.parse_credentials(secret(token_uri="https://example.org/collect"))


def test_missing_refresh_token_is_rejected():
    with pytest.raises(auth.SetupError):
        auth.parse_credentials(secret(refresh_token=""))


def test_secret_transmitted_via_stdin_and_not_echoed(monkeypatch, capsys):
    calls = []
    def run(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(auth.subprocess, "run", run)
    info = json.loads(secret())
    auth.save_github_secret("owner/repository", info)
    args, kwargs = calls[0]
    assert json.loads(kwargs["input"]) == info
    assert info["refresh_token"] not in str(args)
    assert info["client_secret"] not in str(args)
    assert kwargs["capture_output"] is True
    assert not capsys.readouterr().out


def test_provider_error_with_credentials_is_not_logged(monkeypatch, capsys):
    monkeypatch.setenv(auth.SECRET_NAME, secret())
    def fail(*args):
        raise RuntimeError("private-refresh-token private-client-secret")
    monkeypatch.setattr(auth, "refresh_and_verify", fail)
    assert auth.main(["verify"]) == 1
    out = capsys.readouterr()
    assert "FAILED" in out.out
    assert "private-refresh-token" not in out.out + out.err
    assert "private-client-secret" not in out.out + out.err


def test_verify_requires_refresh_credentials(monkeypatch, capsys):
    monkeypatch.delenv(auth.SECRET_NAME, raising=False)
    assert auth.main(["verify"]) == 1
    assert "GDRIVE_OAUTH_JSON" in capsys.readouterr().out


@pytest.mark.parametrize("status,mime,can_add", [
    (403, "application/vnd.google-apps.folder", True),
    (200, "application/octet-stream", True),
    (200, "application/vnd.google-apps.folder", False),
])
def test_inaccessible_or_nonwritable_target_fails(status, mime, can_add):
    session = SimpleNamespace(get=lambda *a, **k: SimpleNamespace(
        status_code=status, json=lambda: {"id": "folder", "mimeType": mime,
                                          "capabilities": {"canAddChildren": can_add}}))
    with pytest.raises(auth.SetupError):
        auth.verify_folder(session, "folder")


def test_authorize_does_not_save_secret_before_folder_validation(monkeypatch, tmp_path):
    client = tmp_path / "desktop.json"
    client.write_text(json.dumps({"installed": {
        "auth_uri": "https://accounts.google.com/o/oauth2/auth", "token_uri": auth.TOKEN_URI,
    }}))
    credentials = SimpleNamespace(client_id="client", client_secret="private", refresh_token="refresh")
    monkeypatch.setattr(auth.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0))
    monkeypatch.setattr(auth.InstalledAppFlow, "from_client_config", lambda *a, **k:
                        SimpleNamespace(run_local_server=lambda **kw: credentials))
    def fail(*args):
        raise auth.SetupError("无法读取目标目录。")
    monkeypatch.setattr(auth, "refresh_and_verify", fail)
    saved = []
    monkeypatch.setattr(auth, "save_github_secret", lambda *a: saved.append(a))
    with pytest.raises(auth.SetupError):
        auth.authorize(client, "owner/repository", "folder")
    assert not saved


def test_client_json_inside_repository_is_rejected():
    with pytest.raises(auth.SetupError):
        auth.authorize(auth.ROOT / "client_secret.json", "owner/repository", "folder")
