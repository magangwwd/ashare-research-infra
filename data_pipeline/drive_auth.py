"""Prepare Drive OAuth on the user's computer; verify Runner access without writes."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess

from google.auth.transport.requests import AuthorizedSession, Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
import yaml

SCOPES = ["https://www.googleapis.com/auth/drive"]
TOKEN_URI = "https://oauth2.googleapis.com/token"
SECRET_NAME = "GDRIVE_OAUTH_JSON"
ROOT = Path(__file__).resolve().parents[1]


class SetupError(Exception):
    """Only safe, predefined messages may be displayed to users."""


def folder_id(config_path: Path) -> str:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    value = config.get("storage", {}).get("drive", {}).get("folder_id", "")
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", value):
        raise SetupError("config.yaml 中 storage.drive.folder_id 无效。")
    return value


def parse_credentials(value: str) -> Credentials:
    try:
        info = json.loads(value)
        required = ("client_id", "client_secret", "refresh_token")
        if not isinstance(info, dict) or any(not isinstance(info.get(k), str) or not info[k] for k in required):
            raise ValueError
        if info.get("token_uri", TOKEN_URI) != TOKEN_URI:
            raise ValueError
    except (TypeError, ValueError):
        raise SetupError("GDRIVE_OAUTH_JSON 格式无效，请重新运行 authorize。") from None
    return Credentials(token=None, refresh_token=info["refresh_token"], token_uri=TOKEN_URI,
                       client_id=info["client_id"], client_secret=info["client_secret"], scopes=SCOPES)


def verify_folder(session, target: str) -> dict:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", target):
        raise SetupError("Drive 文件夹 ID 无效。")
    response = session.get(f"https://www.googleapis.com/drive/v3/files/{target}", params={
        "supportsAllDrives": "true", "fields": "id,name,mimeType,capabilities(canAddChildren)",
    }, timeout=30)
    if response.status_code != 200:
        raise SetupError("无法读取目标目录：请检查 Drive API、授权账号和目录访问权限。")
    info = response.json()
    if info.get("id") != target or info.get("mimeType") != "application/vnd.google-apps.folder":
        raise SetupError("目标不是预期的 Drive 文件夹。")
    if not info.get("capabilities", {}).get("canAddChildren", False):
        raise SetupError("授权账号没有在该目录添加文件的权限。")
    return {"passed": True, "folder_id": target, "can_add_children": True,
            "file_upload_tested": False, "production_publish_enabled": False}


def storage_quota(session) -> dict:
    """Google-account quota includes Gmail/Photos; absent limits stay unknown."""
    response = session.get("https://www.googleapis.com/drive/v3/about",
                           params={"fields": "storageQuota"}, timeout=30)
    if response.status_code != 200:
        return {"available": False}
    try:
        quota = response.json()["storageQuota"]
        sizes = {k: int(quota[k]) if k in quota else None
                 for k in ("limit", "usage", "usageInDrive", "usageInDriveTrash")}
        if any(v is not None and v < 0 for v in sizes.values()):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        return {"available": False}
    remaining = max(0, sizes["limit"] - sizes["usage"]) if sizes["limit"] is not None and sizes["usage"] is not None else None
    return {"available": True, "limit_bytes": sizes["limit"], "used_bytes": sizes["usage"],
            "remaining_bytes": remaining, "drive_used_bytes": sizes["usageInDrive"],
            "drive_trash_bytes": sizes["usageInDriveTrash"],
            "scope": "Google account storage; shared with other Google services"}


def refresh_and_verify(credentials: Credentials, target: str) -> dict:
    try:
        credentials.refresh(Request())
    except Exception:
        raise SetupError("OAuth 刷新失败：请检查凭据有效期、应用发布状态，必要时重新授权。") from None
    with AuthorizedSession(credentials) as session:
        report = verify_folder(session, target)
        report["storage_quota"] = storage_quota(session)
        return report


def save_github_secret(repo: str, info: dict) -> None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise SetupError("--repo 应为 owner/repository。")
    # Credentials travel through stdin, never argv, stdout, files or exceptions.
    result = subprocess.run(["gh", "secret", "set", SECRET_NAME, "--repo", repo],
                            input=json.dumps(info), text=True, capture_output=True, check=False)
    if result.returncode:
        raise SetupError("GitHub Secret 写入失败。请以仓库管理员身份重新 gh auth login 后授权。")


def authorize(client_path: Path, repo: str, target: str) -> dict:
    client_path = client_path.expanduser().resolve()
    if client_path.is_relative_to(ROOT):
        raise SetupError("OAuth 客户端 JSON 必须保存在仓库之外，例如 Downloads 文件夹。")
    try:
        info = json.loads(client_path.read_text(encoding="utf-8"))
        installed = info["installed"]
        if (installed.get("auth_uri") != "https://accounts.google.com/o/oauth2/auth"
                or installed.get("token_uri") != TOKEN_URI):
            raise ValueError
    except (OSError, TypeError, KeyError, ValueError):
        raise SetupError("请提供 Google Cloud 下载的桌面应用 OAuth 客户端 JSON。") from None
    if subprocess.run(["gh", "auth", "status"], capture_output=True, check=False).returncode:
        raise SetupError("请先运行 gh auth login，登录有权管理此仓库 Secrets 的 GitHub 账号。")
    flow = InstalledAppFlow.from_client_config({"installed": installed}, scopes=SCOPES,
                                               autogenerate_code_verifier=True)
    granted = flow.run_local_server(
        host="127.0.0.1", port=0, open_browser=True, timeout_seconds=300,
        authorization_prompt_message="请在本机浏览器完成 Google 授权：{url}",
        success_message="授权回调已收到，请返回终端完成验证。可以关闭此页面。",
        access_type="offline", prompt="consent",
    )
    if not granted.refresh_token:
        raise SetupError("未获得离线刷新凭据，请重新授权并允许应用访问 Drive。")
    secret = {"client_id": granted.client_id, "client_secret": granted.client_secret,
              "refresh_token": granted.refresh_token, "token_uri": TOKEN_URI}
    # Test a fresh access token, not merely the short-lived browser access token.
    result = refresh_and_verify(parse_credentials(json.dumps(secret)), target)
    save_github_secret(repo, secret)
    return {**result, "github_secret_saved": SECRET_NAME}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "config/config.yaml")
    commands = parser.add_subparsers(dest="command", required=True)
    auth = commands.add_parser("authorize", help="Run on your computer; browser consent then GitHub Secret")
    auth.add_argument("--client-secrets", type=Path, required=True)
    auth.add_argument("--repo", default="magangwwd/ashare-research-infra")
    commands.add_parser("verify", help="Read OAuth credentials from environment; no Drive writes")
    args = parser.parse_args(argv)
    try:
        target = folder_id(args.config)
        if args.command == "authorize":
            report = authorize(args.client_secrets, args.repo, target)
        else:
            secret = os.environ.get(SECRET_NAME)
            if not secret:
                raise SetupError("环境变量 GDRIVE_OAUTH_JSON 未配置。")
            report = refresh_and_verify(parse_credentials(secret), target)
            disk = shutil.disk_usage(ROOT)
            report["runtime_disk"] = {"total_bytes": disk.total, "free_bytes": disk.free}
        print("SUCCESS " + json.dumps(report, ensure_ascii=False))
        return 0
    except SetupError as error:
        print("FAILED " + str(error))
    except Exception:
        # OAuth/HTTP/subprocess exceptions can contain credentials; never echo them.
        print("FAILED 授权或连接检查未完成。请检查依赖、网络和客户端配置后重试。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
