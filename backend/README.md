# 后端目录与依赖

后端按职责分层，并在业务层按能力组织模块。`backend/app.py` 只负责装配应用、注册路由和管理启动/关闭流程；对外 HTTP 路径、MCP 工具、数据库表和数据目录保持不变。

```text
backend/
├── app.py                         # ASGI 入口与应用生命周期
├── api/
│   ├── router.py                  # HTTP 路由汇总
│   ├── dependencies.py            # 请求级数据库会话、JWT 身份和权限
│   ├── middleware.py              # CORS、请求日志、缓存响应头
│   ├── frontend.py                # 前端页面和静态资源
│   ├── routers/                   # 认证、知识库、聊天、会话、管理等接口
│   └── schemas/                   # 按能力拆分的 HTTP 请求/响应模型
├── services/
│   ├── authentication.py          # 用户凭据校验
│   ├── github_oauth.py            # GitHub 身份验证、状态与一次性登录票据
│   ├── knowledge/
│   │   ├── access.py              # 知识库创建、归属与检索可见范围
│   │   ├── uploads.py             # 格式校验、配额、去重、上传与替换
│   │   ├── processing.py          # 索引清理、文档入库、诊断保存、任务恢复
│   │   ├── lifecycle.py           # 重新处理与删除
│   │   └── tools.py               # HTTP 调试与 MCP 共用的知识库查询用例
│   ├── chat/                      # Agent、天气工具、会话存储与 LangChain 补丁
│   └── platform/                  # 个人 API Key、调用审计、频率限制
├── documents/                     # 格式加载、文本清洗、PDF 提取/OCR、分块
├── rag/                           # 检索图、查询扩展、混合检索、重排、父块合并
├── infrastructure/
│   ├── database/
│   │   ├── session.py             # 数据库引擎、SessionLocal、Base、版本检查
│   │   └── models/                # identity/chat/knowledge/operations/legacy ORM
│   ├── cache.py                   # Redis JSON 缓存
│   ├── embedding.py               # 稠密向量与持久化 BM25 统计
│   ├── vector_store/              # Milvus 客户端与写入器
│   ├── resources.py               # 全进程共用的加载器、向量模型与客户端
│   └── storage.py                 # 当前和旧版原文件目录
├── integrations/mcp/              # MCP 协议适配、认证、工具注册与结果模型
├── workers/                       # 后台任务调度、恢复入口和审计清理
├── cli/                           # 管理员初始化、旧文档导入、离线重建
├── core/                          # 环境配置、密码/JWT 原语、日志上下文
└── legacy/                        # 旧版全局文档接口、父块存储和模型；未注册到应用
```

## 依赖规则

- HTTP 和 MCP 适配层调用业务用例；后台任务和命令行也复用业务用例。新增业务逻辑放入 `services/`，避免复制到两个协议入口。
- `core/` 只依赖自身；`documents/` 只依赖文档模块和配置；`infrastructure/` 不导入接口层、业务层或 Agent。共享资源装配可以使用文档加载器。
- 业务层不导入 HTTP 路由、MCP 适配或后台调度。密码哈希和签发令牌放在 `core/security.py`，HTTP 的 `Depends`、Bearer 身份解析放在 `api/dependencies.py`。
- 文档索引与 RAG 检索通过 `infrastructure.resources` 使用同一份 embedding、Milvus 客户端和写入器，保证 BM25 增删统计一致。不要在路由或业务函数中另建全局实例。
- 文档访问都先检查知识库归属；检索只读取 `ready` 文档。重处理、替换、删除仍沿用已有状态机和事务顺序。
- `models/__init__.py` 汇总所有 ORM 映射，Alembic 与应用共用同一个 `Base.metadata`。拆分模型不新增迁移，也不修改历史迁移的建表行为。
- 项目内导入使用新路径；`legacy/` 不参与正常私有知识库检索，其全局文档路由不注册到应用。

GitHub 登录路由位于 `api/routers/github_auth.py`，请求和响应模型位于 `api/schemas/auth.py`，身份查询及一次性凭据由 `services/github_oauth.py` 处理。它与用户名密码登录共用 `api/dependencies.py` 的数据库依赖和 `core/security.py` 的 JWT 签发。用户的 `github_id` 及唯一索引保留在 `infrastructure/database/models/identity.py`，应用数据库版本为 `0009_github_oauth`；本次目录整理不新增迁移。

## 运行与验证

在项目根目录执行：

```bash
uv run uvicorn backend.app:app --host 0.0.0.0 --port 8000 --reload
uv run python -m unittest discover -s tests -p 'test_*.py'
node tests/frontend_platform_smoke.js
node tests/frontend_knowledge_smoke.js
node tests/frontend_github_smoke.js
uv run python tests/m3_local_integration.py
```

已有命令 `python -m backend.admin_bootstrap`、`python -m backend.legacy_import`、`python -m backend.reindex_documents` 保留为兼容入口，实现归入 `backend/cli/`。`python -m backend.workers.process_documents` 保持可用。

运行数据仍位于项目根目录的 `data/knowledge_documents/`、`data/documents/` 和配置指定的 BM25 状态文件。路径统一基于 `core.config.BASE_DIR`，不会随源文件层级改变。此次重构无须移动数据或重建索引。
