# GitHub Actions 的 Google Drive 授权

本步骤按用户请求提前准备 Runner 授权。只验证 OAuth 刷新和目标目录权限，不上传数据，不启用 Drive Production；Phase 2 的远程验证与正式发布仍待 Phase 1 稳定后实现。

目标目录：[Ashare_Quant](https://drive.google.com/drive/folders/1rkC99Ymgp8mjvAcJNRPJMlOGLdI9_bue)，ID 已写入 config.yaml 的 storage.drive.folder_id。storage.drive.enabled 仍为 false。ChatGPT 的 Drive 连接不能导出给 GitHub Actions 使用。

## 1. 创建自己的 Google OAuth 客户端

用拥有目标目录权限的 Google 账号打开 [Google Cloud Console](https://console.cloud.google.com/)。

1. 选择或创建项目，在 **APIs & Services → Library** 启用 **Google Drive API**。
2. 打开 **Google Auth Platform**，完成 Branding 中的应用名、支持邮箱和联系邮箱。
3. Audience 选择 External（个人账号）；测试阶段添加自己的 Google 邮箱为 Test user。Workspace 内部应用可按组织规则选择 Internal。
4. 本工具会请求 `https://www.googleapis.com/auth/drive`，用于未来上传和正式版本切换；它具有较广的 Drive 权限，不是仅限目标文件夹的授权。现有用户创建的文件夹不能仅凭 `drive.file` 自动获得访问权；当前方案没有实现 Picker 选择授权。
5. 在 Clients 创建 **Desktop app / 桌面应用** OAuth 客户端，下载 JSON。放在电脑 Downloads 等仓库之外的目录，不提交 Git。
6. External 应用在 Testing 状态、使用 Drive scope 时，refresh token 通常 7 天过期。长期自动任务应先按 Google 要求发布为 **In production**，再运行下面的授权。是否需要验证以 Google Console 对账号和应用用途的要求为准，不声称发布应用一定免验证。

参考：[OAuth 刷新凭据过期规则](https://developers.google.com/identity/protocols/oauth2#expiration)、[Desktop OAuth](https://developers.google.com/identity/protocols/oauth2/native-app)、[Drive scopes](https://developers.google.com/workspace/drive/api/guides/api-specific-auth)。

## 2. 在自己的电脑完成授权并保存 Secret

本机需要 Python 3.12、Git 和 [GitHub CLI](https://cli.github.com/)。不要在远程 Runner 或本聊天环境运行浏览器授权，回调地址是本机 127.0.0.1。

打开终端，首次获取代码：

```sh
git clone https://github.com/magangwwd/ashare-research-infra.git
cd ashare-research-infra
```

已有本地仓库则先 `git pull --ff-only`，不用重复 clone。建议使用虚拟环境；在该环境安装并登录自己的 GitHub 账号：

```sh
python -m pip install -r requirements.txt
gh auth login
```

登录账号必须有管理该仓库 Actions Secrets 的权限。将下面路径替换成刚下载的 JSON 实际路径：

```sh
python -m data_pipeline.drive_auth authorize --client-secrets "/完整路径/client_secret_你的客户端.json"
```

浏览器选择拥有 Ashare_Quant 目录权限的 Google 账号并完成授权。程序在内存中取得 refresh token，重新刷新并检查目录 canAddChildren，然后经 stdin 调用 `gh secret set` 写入 **GDRIVE_OAUTH_JSON**。不会显示凭据或生成 token.json，也不上传任何 Drive 文件。授权失败不保存新的 Secret；成功会覆盖该同名 Secret。

正确成功输出含 `SUCCESS`、`github_secret_saved: GDRIVE_OAUTH_JSON`、`can_add_children: true`；这只表示授权与目录权限已验证，不表示真实上传/正式发布已通过。

若出现 Secret 写入失败，先检查 gh 登录账号及仓库管理权限，再重新运行授权；不要将 JSON、refresh token 或任何 Secret 发到聊天。

## 3. 在 GitHub Runner 验证

打开仓库 **Actions → Task 01 data infrastructure → Run workflow**：

- Branch 选 main。
- 勾选 **Only verify Drive OAuth; no downloads or Drive uploads**。
- 点击 Run workflow。

本次仅运行 `drive-auth` job，默认数据 job 跳过。Runner 从 GDRIVE_OAUTH_JSON 刷新访问 token，并只读检查目标目录及添加文件权限；任何缺失凭据、刷新失败或目录权限失败都会返回 FAILED。勾选只验证时的绿色结果不是 Critical 数据总验收证据。

默认不勾选，schedule 和手动运行仍按原数据流程执行；不会因为准备了 Drive Secret 而自动上传。后续 Phase 2 必须实现 staging、上传完整性验证、失败保留旧版本和 Current Pointer，见 [发布合同](drive-phase2.md)。
