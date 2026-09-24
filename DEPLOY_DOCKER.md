# 单台 ECS 全容器部署

现有 Compose 包含 FastAPI 应用、PostgreSQL、Redis、Milvus、etcd 和 MinIO。前端由 FastAPI 提供静态文件，不需要另建前端容器。Attu 只在 `debug` profile 启动。LLM 和可选的 rerank 仍通过外部 API 调用。

## 服务器准备

- 安装 Docker Engine 和 Docker Compose 插件。建议至少 4 核、8 GB 内存，并留出模型缓存、数据库和向量数据所需磁盘空间。小规格 ECS 请先实测内存与上传时的 CPU 占用。
- 域名和 HTTPS 可由 ECS 上的 Nginx 反向代理提供。安全组仅开放 80/443；应用、数据库等端口都只在本机或 Compose 网络可达。

## 首次启动

```bash
git clone <仓库地址> /opt/supermew
cd /opt/supermew
cp .env.docker.example .env
chmod 600 .env
# 编辑 .env：替换数据库、Redis、MinIO、JWT、管理员邀请码和模型 API 凭证
docker compose config --quiet
docker compose up -d --build
docker compose ps
docker compose logs -f app
curl -f http://127.0.0.1:8000/docs
```

`.env` 中的 `DATABASE_URL`、`REDIS_URL`、`MILVUS_HOST` 必须分别指向 Compose 服务名 `postgres`、`redis`、`standalone`，不能使用 `127.0.0.1`。PostgreSQL 和 Redis 密码要与对应 URL 一致；如果密码含 URL 特殊字符，要在 URL 中编码。不要直接把旧版宿主机部署的 `.env` 拷贝过来而不检查这些地址。

已有 Milvus 数据的部署升级时，MinIO 凭证应与原服务一致；如果同时修改 MinIO 凭证，确保 `MINIO_ROOT_USER` 和 `MINIO_ROOT_PASSWORD` 与 Milvus 端同步，并先做好备份。

第一次启动时，`app` 下载 `BAAI/bge-m3` 到 `volumes/huggingface`，会比后续重启慢。依赖锁文件使用 CPU 版 PyTorch；服务器不需要 GPU、CUDA 驱动或 CUDA Toolkit。

数据在 `volumes/postgres`、`volumes/redis`、`volumes/etcd`、`volumes/minio`、`volumes/milvus`、`volumes/huggingface` 和 `data`。升级或重建镜像时保留这些目录。备份时请同时覆盖数据库、Milvus 依赖数据以及 `data` 下的上传文件和 BM25 状态。

## HTTPS 入口

应用只映射到 ECS 的 `127.0.0.1:8000`。Nginx 代理到 `http://127.0.0.1:8000` 即可；聊天使用流式响应，代理需要关闭响应缓冲并设置足够长的读取超时。例如在已配置 TLS 的 `server` 块中：

```nginx
location / {
    proxy_pass http://127.0.0.1:8000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_buffering off;
    proxy_read_timeout 300s;
}
```

## 更新与排查

```bash
cd /opt/supermew
bash deploy.sh
docker compose ps
docker compose logs --tail=100 app
```

`deploy.sh` 会从 `main` 快进拉取、重建镜像并等待应用就绪。如果不是从 `main` 部署，请手动更新代码后执行 `docker compose up -d --build`。

Attu 默认不运行。需要临时排查 Milvus 时用 `docker compose --profile debug up -d attu`，并通过 SSH 隧道访问本机的 `8080` 端口。不要把 Attu、MinIO、Milvus、PostgreSQL 或 Redis 端口放到 ECS 公网安全组。
