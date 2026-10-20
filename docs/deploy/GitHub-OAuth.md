# GitHub OAuth 登录

访客点击“使用 GitHub 登录”，授权后首次自动创建普通用户，之后直接登录，无需邀请码。原有用户名密码登录及邀请码注册继续可用。仅用于站内登录，知识库 MCP 仍使用个人 API Key。

## 创建 OAuth App

在 [GitHub 的 OAuth Apps 设置](https://github.com/settings/developers)中选择 **New OAuth App**：

- Application name：例如 `MeowConnectPlatform`。
- Homepage URL：你实际访问的网站根地址，例如 `https://fyism.cn`。
- Authorization callback URL：`https://fyism.cn/auth/github/callback`。

创建后取得 Client ID，并点击 **Generate a new client secret**。若页面提供 callback URL 通配匹配选项，保持关闭，使用精确回调地址。无需开启 Device Flow，也不需要填写个人访问令牌 PAT。

本地测试建议另建一个 OAuth App，Homepage URL 使用 `http://127.0.0.1:8000`，callback URL 使用 `http://127.0.0.1:8000/auth/github/callback`。本地浏览器也必须使用 `127.0.0.1`，不要在 `localhost` 与 `127.0.0.1` 间切换。测试环境和正式环境使用独立的 OAuth App 和 Client Secret。

## 配置和启动

把以下项目填写到本环境私有 `.env`（Docker 部署填写实际使用的私有环境文件）：

```dotenv
GITHUB_OAUTH_ENABLED=true
GITHUB_CLIENT_ID=你的ClientID
GITHUB_CLIENT_SECRET=你的ClientSecret
GITHUB_REDIRECT_URI=https://fyism.cn/auth/github/callback
```

将示例域名替换为你的网站域名。Client Secret 只写在服务端私有配置中，不提交 Git，也不填写到前端。应用启用时会校验凭据完整性、回调路径，以及 HTTPS（仅本机回环地址允许 HTTP）。`GITHUB_REDIRECT_URI` 必须和 GitHub App 中的 callback URL 完全一致，且与实际访问网站同源，包括端口。

先备份数据库，使用项目原有发布流程更新代码或构建镜像。在启动新版本前运行迁移：

```bash
uv run alembic upgrade head
```

本次迁移 `0009_github_oauth` 为 `users` 增加可空且唯一的 `github_id`；保留已有账号、角色、密码和数据。Docker 的 `deploy.sh` 已包含备份及迁移步骤，不需重复执行另一套发布操作。

确认 PostgreSQL、Redis 运行后，按现有方式重启应用。Redis 需要支持 `GETDEL`（6.2+，仓库使用 Redis 7），用于过期和一次性消费登录状态。默认不开启；`GITHUB_OAUTH_ENABLED=false` 时页面不显示 GitHub 入口，原有登录继续工作。关闭入口不会删除已有 GitHub 用户；这些用户在重新启用后可继续登录。

服务端必须能通过 HTTPS 访问：

```text
github.com/login/oauth/access_token
api.github.com/user
```

访客浏览器也必须能打开 `github.com` 完成授权。国内服务器的单次连通性测试通过不代表之后始终可用；网络异常会提示重试，不会创建未验证身份的登录会话。

## 实际验收

1. 打开 `https://你的域名/auth/providers`，应返回 `{"github":true}`。
2. 从网站登录弹窗点击“使用 GitHub 登录”，GitHub 授权页无需仓库或私人邮箱权限。
3. 首次授权后应回到发起登录的页面，并显示普通用户身份；无邀请码也能注册。进入个人管理创建 API Key、创建自己的知识库，确认原有权限正常。
4. 退出后再用同一 GitHub 账号登录，应回到同一本地账号及其数据；GitHub 修改用户名不会创建新账号。
5. 取消授权，应回到本站并出现可重试提示；旧账号仍可使用原密码登录。

当前版本按 GitHub 唯一用户 ID 识别账号，不根据用户名或邮箱合并老账号。首次注册使用 `gh_<GitHub用户名>_<ID>` 作为站内用户名；若重名则附随机后缀。GitHub 用户默认只能通过 GitHub 登录，没有本地密码；已有账号绑定 GitHub、设置密码、修改站内用户名不在本次范围内。

## 实现和测试

- OAuth `state` 有效期 10 分钟，绑定发起浏览器，Redis 原子一次性消费；使用 PKCE S256。
- 授权成功后签发 60 秒一次性登录票据，通过 HttpOnly Cookie 和同源 POST 换取原有站内 JWT。JWT 与 GitHub access token 都不会写进回跳 URL；GitHub access token 不落库、不返回前端。
- 生产 Cookie 使用 Secure、SameSite=Lax 和 `/auth/github` 路径；Redis 故障时拒绝登录，不降级为无验证注册。
- 用户始终以 `user` 角色注册；GitHub 身份不匹配本站同名账号。请求日志只记录路径，不记录授权参数。反向代理和 Uvicorn 的访问日志可能包含 OAuth 回调查询串，生产日志应对该路径省略查询参数。

```bash
uv run python -m unittest tests.test_github_oauth tests.test_github_oauth_migration tests.test_backend_contracts
node --check frontend/app.js
node tests/frontend_github_smoke.js
node tests/frontend_platform_smoke.js
```

自动测试使用隔离 SQLite、可过期 Redis 替身与 GitHub HTTP 替身，验证注册登录、账号隔离、CSRF、PKCE、状态/票据过期及重放、异常、迁移和页面恢复。真实 GitHub 授权必须在配置实际 OAuth App 后按上面的实际验收完成。

参考：[GitHub OAuth Web Flow](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps)。
