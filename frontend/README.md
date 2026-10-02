# MeowConnectPlatform 前端

Vue 3 浏览器版 + Element Plus，HTML / CSS / JavaScript，无构建步骤。由现有 FastAPI 静态路由提供，通过同源接口连接业务服务。

## 文件组织

- `index.html`：公开服务页面、工作空间、表单与弹层。
- `app.js`：Vue Composition API，认证、知识库、文件处理、流式聊天、工具、历史会话、API Key 与管理员接口。
- `theme.css`：日间 / 夜间色值、字体、代码配色、Element Plus 主题。
- `styles.css`：布局、页面排版、品牌动画与响应式规则。
- `assets/fonts`：Anthropic Sans / Serif 本地字体；来源及使用说明见目录中的 README。
- `vendor`：固定版本的浏览器发行文件及许可，无运行时 CDN 请求。

前端无需 `npm install`。按根目录 README 启动现有后端后，打开服务根地址。修改后刷新网页。

## 接口与接入

使用现有 `/auth/*`、`/knowledges/*`、`/knowledge-settings`、`/chat/stream`、`/sessions/*`、`/tools/debug/*`、`/account/api-keys/*` 与 `/admin/*`，未改变后端契约。

知识库 MCP 地址为 `/mcp/knowledge`。首页和详情读取 `/platform/services/knowledge` 的部署状态及公共 HTTPS 地址，配置示例使用返回地址；当前部署未启用外部入口时明确提示。Codex 与 Claude Code 示例均从 `MCP_API_KEY` 环境变量读取个人 Key，不把真实 Key 写入示例。

站内对话继续支持流式内容、检索步骤与来源、停止回答、会话恢复、多知识库范围。Markdown 经 DOMPurify 清理后显示。完整 API Key / 邀请码只展示创建响应中的一次性内容，离开页面、退出登录或关闭后清除。

## 检查

在项目根目录运行：

```powershell
node --check frontend/app.js
node tests/frontend_knowledge_smoke.js
uv run python -m unittest tests.test_frontend_routes tests.test_backend_contracts tests.test_m3_platform
```
