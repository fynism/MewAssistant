# SuperMew

SuperMew（MeowConnectPlatform）是一个私有知识库与 MCP 服务平台。上传文档后，可以在网页中进行知识库问答，也可以通过 MCP 将检索能力接入外部 AI 客户端。用户、知识库、文件与会话按账号隔离。

## 功能

- **知识库管理**：创建多个知识库，上传、替换、删除和重新处理文档，查看处理状态与提取质量。
- **文档解析**：支持 PDF、Word（`.doc` / `.docx`）、Excel（`.xls` / `.xlsx`）和 TXT；PDF 支持逐页原生文字提取与本地 OCR。
- **混合检索**：本地 BGE-M3 稠密向量与 BM25 稀疏向量召回，经 RRF 融合，可选远程重排，再按阈值合并父级上下文。
- **站内问答**：LangChain Agent 与 LangGraph RAG 流程，支持流式回答、停止生成、检索步骤与来源、多知识库范围、历史会话及摘要记忆。
- **远程 MCP**：提供 `listKnowledges` 和 `retrieve`，通过可撤销的个人 API Key 访问私有知识库。
- **账号与管理**：邀请码注册、可选 GitHub 登录、管理员邀请码管理、调用审计、上传配额和 Redis 限流。

## 技术栈与数据流

| 层 | 实现 |
| --- | --- |
| 前端 | Vue 3 浏览器版、Element Plus，本地静态资源，无需 npm 构建 |
| API 与 Agent | FastAPI、LangChain、LangGraph、官方 Python MCP SDK |
| 文档处理 | PDFium、RapidOCR（ONNX CPU）、三级滑动窗口分块 |
| 检索 | Hugging Face Embedding、Milvus、BM25、可选重排 API |
| 持久化 | PostgreSQL、SQLAlchemy、Alembic、Redis、本地文件 |
| 部署 | Docker Compose、Nginx / HTTPS |

```text
上传文件 → 格式校验与去重 → 后台解析 / OCR / 清洗 → 三级分块
                                             ├─ 叶子块 → 稠密 / 稀疏向量 → Milvus
                                             └─ 父级块与处理报告 → PostgreSQL

网页问答 → Agent → RAG 检索与相关性判断 / 查询扩展 → 生成流式回答
MCP 调用 → API Key 鉴权 → 指定知识库检索 → 返回原始片段与来源
```

网页会话使用 JWT，MCP 使用个人 API Key。OCR 和 Embedding 在本地执行；配置的聊天模型、相关性判断模型和远程重排服务会接收对应的查询或文本片段。

## 本地启动

需要 Python **3.12+**（仓库使用 3.12）、`uv` 和 Docker Compose。在仓库根目录执行以下命令。

### 1. 安装依赖并配置环境

```powershell
uv sync --frozen
Copy-Item .env.example .env
```

Bash 下将复制命令改为 `cp .env.example .env`。已有 `.env` 时直接编辑，保留现有配置。

直接运行 Python 应用时，Word 导入和预览需要安装 LibreOffice Writer。Windows 可使用默认安装路径，或将 LibreOffice 的 `program` 目录加入 `PATH`。Linux 需要 `soffice` 命令和文档所用字体，例如 `libreoffice-writer`、`fonts-liberation` 和 `fonts-noto-cjk`。Docker 镜像已包含这些依赖。Word 入库与预览共用转换后的 PDF。已有 Word 可以直接预览，重新处理后旧索引才会更新为逐页引用。

本地处理旧版 `.xls` 时，安装 Node.js 22 或更高版本，并在仓库根目录运行 `npm ci --prefix backend/documents/sheetjs`。Docker 镜像已包含 Node.js 和 SheetJS。Excel 预览按工作表分行列显示，使用文件保存的公式缓存值。重新处理已有 Excel 后，检索引用可跳转到工作表和单元格范围。

Excel 预览每次显示最多 40 行、20 列，超过 256 字符的单元格在预览中截断。单个工作簿最多索引 8 万个有值单元格及 200 万字符，展开后超过 64 MiB 的 `.xlsx` 无法读取。可下载原件查看完整内容。

`.env.example` 中部分值为空，需要填写后才能启动；空值不会触发代码中的默认值。最小配置如下，替换模型凭证与 JWT 密钥：

```dotenv
ARK_API_KEY=your-model-api-key
MODEL=your-chat-model
GRADE_MODEL=your-grading-model
BASE_URL=https://your-openai-compatible-api.example/v1

EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_DEVICE=cpu
DENSE_EMBEDDING_DIM=1024

MILVUS_HOST=127.0.0.1
MILVUS_PORT=19530
MILVUS_COLLECTION=embeddings_collection
MILVUS_M1_COLLECTION=embeddings_collection_m1
BM25_M1_STATE_PATH=data/bm25_state_m1.json

DATABASE_URL=postgresql+psycopg2://postgres:postgres@127.0.0.1:5432/langchain_app
REDIS_URL=redis://127.0.0.1:6379/0
REDIS_KEY_PREFIX=supermew
JWT_SECRET_KEY=replace-with-a-long-random-secret

GITHUB_OAUTH_ENABLED=false
MCP_EXTERNAL_ENABLED=false
```

`ARK_API_KEY` 是项目沿用的配置名，用于 OpenAI 兼容模型 API。`GRADE_MODEL` 用于 RAG 相关性判断，可与 `MODEL` 填写相同的可用模型；需支持结构化输出。生成随机密钥可运行：

```powershell
uv run python -c "import secrets; print(secrets.token_hex(32))"
```

可选能力及完整参数见 [.env.example](.env.example)：

| 能力 | 配置 |
| --- | --- |
| 远程重排 | 同时设置 `RERANK_MODEL`、`RERANK_BINDING_HOST`、`RERANK_API_KEY`；未配置或调用失败时降级 |
| GitHub 登录 | `GITHUB_OAUTH_ENABLED`、`GITHUB_CLIENT_ID`、`GITHUB_CLIENT_SECRET`、`GITHUB_REDIRECT_URI`，见 [接入说明](docs/deploy/GitHub-OAuth.md) |
| PDF OCR | `PDF_OCR_MODE=auto`，可改为 `always` 或 `off` |
| 父块合并 | `AUTO_MERGE_ENABLED`、`AUTO_MERGE_THRESHOLD` |
| 天气工具 | `AMAP_WEATHER_API`、`AMAP_API_KEY` |

首次运行需要下载 Embedding 模型，本地首次 OCR 也需要加载模型。依赖锁文件使用 CPU 版 PyTorch；下载完成前不要开启 `HF_HUB_OFFLINE=1`。

### 2. 启动数据库、缓存与向量服务

```powershell
docker compose up -d postgres redis etcd minio standalone
docker compose ps
```

以上仅启动依赖，应用在下一步由本机 Python 启动。默认 PostgreSQL 用户与密码均为 `postgres`，数据库为 `langchain_app`；Redis 默认无密码。若设置 `POSTGRES_PASSWORD` 或 `REDIS_PASSWORD`，同步修改连接 URL；URL 中的特殊字符需要编码。已有数据卷继续使用原凭证。

### 3. 迁移数据库并创建管理员

```powershell
uv run alembic upgrade head
uv run python -m backend.admin_bootstrap --username admin
```

管理员密码在终端交互输入，至少 12 个字符。该命令只用于首次初始化，已有管理员时不会创建第二个或覆盖账户。

### 4. 启动应用

```powershell
uv run uvicorn backend.app:app --host 127.0.0.1 --port 8000 --workers 1
```

- 网页：[http://127.0.0.1:8000/](http://127.0.0.1:8000/)
- API 文档：[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

也可运行 `uv run python -m backend.app`。前端由后端直接提供，修改静态文件后刷新网页即可。

登录管理员账号后，在邀请码管理页面创建邀请码。普通用户通过邀请码注册；启用 GitHub 登录后，首次授权会自动创建普通用户，无需邀请码。旧配置 `ADMIN_INVITE_CODE` 已停用。

## 使用知识库与 MCP

1. 登录网页，创建知识库并上传文件。
2. 等待文件完成处理；只有 `ready` 文件进入检索范围。质量提示和页面提取结果可在文件列表查看。
3. 在站内选择知识库问答，或先使用工具调试验证检索结果。
4. 需要外部客户端访问时，在个人管理中创建 API Key，立即保存明文；之后只可查看元信息或撤销。

MCP 使用 Streamable HTTP，端点为 `/mcp/knowledge`。发布前配置 HTTPS 反向代理，以及以下变量：

```dotenv
MCP_PUBLIC_BASE_URL=https://your-domain.example
MCP_ALLOWED_HOSTS=your-domain.example,localhost:*,127.0.0.1:*
MCP_EXTERNAL_ENABLED=true
```

在完成 HTTPS 与客户端联调后启用远程入口。Nginx 请求头、流式响应与代理设置见 [配置示例](deploy/nginx-mcp.conf.example)。MCP 认证使用 Bearer API Key。

| 工具 | 用途与主要参数 |
| --- | --- |
| `listKnowledges` | 列出当前账号知识库与可检索状态；支持 `limit`、`cursor` |
| `retrieve` | 传入 `query`、1–20 个不重复的 `knowledgeIds`、`topK`（默认 5，默认上限 20） |

先列出知识库，再显式指定 ID 检索。`retrieve` 返回片段、文件名、来源标识、页码、检索 / 重排分数及处理报告，不生成回答。站内工具调试接口为 `POST /tools/debug/listKnowledges` 和 `POST /tools/debug/retrieve`，使用登录 JWT。

## Langfuse 与 Ragas 单题验证

SuperMew 在服务端将 MCP 检索和网页问答的 trace 发送到 Langfuse。本地脚本调用实际接口，再把分数写回该请求的 trace。准备一份测试文档和一个测试知识库即可验证整条链路。

1. 在 [Langfuse Cloud](https://cloud.langfuse.com) 的 **Project Settings → API Keys** 创建凭证。在后端的本地 `.env` 或部署使用的私有 `staging.env` 中，填写 `LANGFUSE_BASE_URL`、`LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY`，设置 `LANGFUSE_TRACING_ENABLED=true`，再启动含此功能的应用版本。使用非 EU 项目时，`LANGFUSE_BASE_URL` 填项目所属区域的地址。
2. 在本地终端设置同一 Langfuse 项目的 URL 和两把 Key，并沿用后端的 `LANGFUSE_TRACING_ENVIRONMENT`。MeowMCP 模式还需要个人 `MCP_API_KEY`，网页问答模式需要站内登录 JWT `SUPERMEW_BENCHMARK_TOKEN`。首次测试请选用只含测试资料的知识库。MCP 可用 `listKnowledges` 获取知识库 ID。
3. 在项目根目录运行以下任一命令，替换地址及知识库 ID。请求公网 API 时，目标服务需先部署含 Langfuse 集成的版本。

   ```powershell
   uv run --locked python -m benchmarks.rag_eval mcp --base-url https://fyism.cn --query "测试文档里的退款期限是多久？" --knowledge-id YOUR_KNOWLEDGE_ID
   uv run --locked python -m benchmarks.rag_eval chat --base-url http://127.0.0.1:8000 --query "根据测试文档，退款期限是多久？" --knowledge-id YOUR_KNOWLEDGE_ID
   ```

脚本输出服务端 trace ID，向该 trace 写入 `rag_eval_transport_smoke`。这个分数用于验证关联与回写。在 Langfuse 的 **Tracing** 打开对应 trace，检查检索、重排状态和 **Scores**，等待批次入库后刷新。

运行 Ragas 指标时，在 `uv run --locked` 后加 `--group eval`。MCP 模式加 `--relevant-document-id DOCUMENT_ID`，可重复指定，计算基于文档 ID 的 Recall。网页问答模式加 `--faithfulness`，并配置 `RAGAS_JUDGE_MODEL`、`RAGAS_JUDGE_API_KEY` 和可选的 `RAGAS_JUDGE_BASE_URL`。Faithfulness 会把测试问题、召回文本和回答发送给裁判模型。Ragas 只在评测脚本里运行。

Langfuse 默认接收耗时、状态、计数等追踪数据。在只含测试数据的环境中设置 `LANGFUSE_CAPTURE_RETRIEVAL_CONTENT=true` 后，新请求的检索问题和最终片段分别出现在 `mcp.retrieve`（MeowMCP）或 `rag.graph`（网页问答）的 **Input** 和 **Output** 中。Langfuse Secret Key、MeowMCP API Key 和登录 JWT 通过私有运行配置提供。

## Docker 发布

应用镜像由开发机或 CI 构建推送，服务器通过 [deploy.sh](deploy.sh) 拉取指定版本；[docker-compose.yml](docker-compose.yml) 没有应用构建配置。

```bash
docker buildx build --platform linux/amd64 -t YOUR_REGISTRY/supermew:YOUR_RELEASE --push .
```

从 [.env.docker.example](.env.docker.example) 分别创建私有的 `staging.env` 和 `production.env`，填写模型配置、强密码、JWT 密钥及对外域名，并补充 `GRADE_MODEL`。容器内连接地址必须使用服务名 `postgres`、`redis`、`standalone`。

两套环境的 `SUPERMEW_ENV_NAME`、`DOCKER_VOLUME_DIRECTORY`、宿主机端口及 Redis 前缀应分别配置。数据目录必须是以环境名结尾的绝对路径，例如 `/opt/supermew-data/staging`；测试与正式环境不能共享数据目录或端口。

```bash
bash deploy.sh staging /opt/supermew-config/staging.env YOUR_REGISTRY/supermew:YOUR_RELEASE
# 测试验收后，以同一镜像发布正式环境
bash deploy.sh production /opt/supermew-config/production.env YOUR_REGISTRY/supermew:YOUR_RELEASE
```

使用固定、不可复用的版本标签或镜像摘要；脚本拒绝 `:latest` 和 `:dev`。部署会拉取应用镜像、启动依赖、备份 PostgreSQL、停止旧应用、运行迁移，再等待新应用就绪；不会自动升级已有依赖容器。默认数据库备份目录为 `/opt/supermew-backups/<环境名>`，可通过 `SUPERMEW_BACKUP_DIR` 修改。

首次部署后，在对应环境中初始化管理员：

```bash
docker compose -p supermew-staging --env-file /opt/supermew-config/staging.env exec app python -m backend.admin_bootstrap --username admin
```

正式环境将项目名和私有配置路径替换为对应值。应用及依赖的宿主机端口仅绑定 `127.0.0.1`，对外通过 Nginx / HTTPS 提供服务。

## 运行边界与维护

应用目前按**单实例、单 Web worker**运行。进程内扫描器默认每 2 秒读取待处理文件，最多并行处理 2 份；重启后恢复中断任务。多个实例共用一套数据库、向量集合和文件状态时，不能保证处理任务所有权。

| 默认限制 | 配置 |
| --- | --- |
| 单文件 20 MiB，PDF 最多 300 页 | `MAX_UPLOAD_BYTES`、`MAX_PDF_PAGES` |
| 每用户 100 份文件、1 GiB 存储 | `MAX_USER_DOCUMENTS`、`MAX_USER_STORAGE_BYTES` |
| 上传 10 次 / 分钟，站内检索 30 次 / 分钟 | `UPLOAD_RATE_PER_MINUTE`、`RETRIEVAL_RATE_PER_MINUTE` |
| MCP 每用户 60 次 / 分钟、每 Key 30 次 / 分钟 | `MCP_USER_RATE_PER_MINUTE`、`MCP_KEY_RATE_PER_MINUTE` |
| 文档处理并发 2，扫描间隔 2 秒 | `MAX_DOCUMENT_PROCESSING`、`DOCUMENT_SCAN_INTERVAL_SECONDS` |

频率超限返回 429，容量超限返回 413；Redis 不可用时受限操作返回 503。上传返回 202 后异步处理，应根据文件状态判断是否可检索。

OCR 默认以 200 DPI 识别，低质量页最多以 300 DPI 重试一次。识别不可靠的页面留存结果供复核并显示提示；复杂表格、公式与手写内容可能无法完整还原。质量分数不是准确率概率。OCR 和 Embedding 会消耗本机 CPU / 内存，远程模型与重排增加请求延迟和调用费用；调高并发前应以实际文件及上游限额验收。

升级前同时备份 PostgreSQL、上传原文件、BM25 状态和 Milvus 及其依赖数据。`deploy.sh` 的数据库备份不替代完整数据备份。Docker 运行数据位于 `DOCKER_VOLUME_DIRECTORY` 下的 `data/` 和 `volumes/`，模型缓存在 `volumes/huggingface/`。

已有文档不会因解析规则更新而自动重建，可在页面中逐份重新处理。批量重建须先停止应用和文档 worker，并保留备份：

```powershell
uv run python -m backend.reindex_documents --all
# 将旧 BM25 索引升级为保留数字、年份、版本与型号的新分词规则
uv run python -m backend.reindex_documents --all --upgrade-tokenizer
```

批量重建会清理旧索引，不要与在线应用并行执行。排障时使用响应头 `X-Request-ID` 对照日志；管理员可查看近 24 小时调用与队列。调用审计保留 90 天，不记录查询全文、文件正文或完整 Key。

## 测试与检索评测

```powershell
uv run python -m unittest discover -s tests -p 'test_*.py'
node tests/frontend_platform_smoke.js
node tests/frontend_knowledge_smoke.js
node tests/frontend_github_smoke.js
```

前端冒烟检查需要 Node.js。HTTP / MCP 集成验证使用临时数据库；加上 `--milvus` 可在独立测试集合验证上传检索，需要本地 Milvus 可用：

```powershell
uv run python tests/m3_local_integration.py
uv run python tests/m3_local_integration.py --milvus
```

中文检索评测见 [benchmarks/README.md](benchmarks/README.md)，脚本覆盖 TXT 上传、处理、检索和按文档 ID 评分。测试包与原始结果位于 Git 忽略的 `data/` 中，需要自行生成。

2026-10-26 的隔离测试记录如下：

| 自定义测试集 | Recall@5 | Recall@10 | Hit@10 | MRR@10 | nDCG@10 |
| --- | --- | --- | --- | --- | --- |
| 10 题 / 80 份文档 | 76.92% | 89.42% | 100% | 1.0000 | 0.8984 |
| 100 题 / 1,000 份文档 | 72.05% | 82.63% | 99% | 0.9833 | 0.8602 |

以上是 T2Retrieval 子集的检索成绩，不代表完整官方基准或生成答案准确率。两组问题集不同；相同 10 题扩库后的 Recall@10 为 88.17%。100 题结果来自每题间隔 4 秒的完整重测，以避开上游重排限额；平均请求耗时 1.725 秒，不含主动等待，不代表并发吞吐。详细条件与限制见 [HANDOFF.md](HANDOFF.md)。

## 目录

```text
backend/
  app.py              # FastAPI 入口
  api/                # HTTP 路由、请求模型与前端静态路由
  services/           # 认证、知识库、聊天与平台业务
  documents/          # 解析、清洗、OCR 与分块
  rag/                # 召回、重排、查询扩展与父块合并
  infrastructure/     # 数据库、缓存、Embedding、Milvus 与文件存储
  integrations/mcp/   # MCP 协议与认证
  workers/            # 后台扫描、任务恢复与审计清理
  cli/                # 管理员初始化、旧数据导入与索引重建
frontend/             # 无构建步骤的网页客户端
migrations/           # Alembic 数据库迁移
benchmarks/           # T2Retrieval 测试工具
tests/                # 单元、集成与前端冒烟测试
deploy/               # Nginx 配置示例
```

模块边界与开发约定见 [后端说明](backend/README.md)、[前端说明](frontend/README.md) 和 [AGENTS.md](AGENTS.md)。
