# 构建镜像并在单台 ECS 部署

在开发机或 CI 构建应用镜像并推送 Docker Hub；ECS 只拉镜像并运行 Compose，不安装 Python，也不构建镜像。Compose 包含 FastAPI 应用、PostgreSQL、Redis、Milvus、etcd 和 MinIO。前端由 FastAPI 提供静态文件。Attu 只在 `debug` profile 启动。LLM 和可选的 rerank 仍通过外部 API 调用。

## 在开发机或 CI 构建并推送

以下示例适用于 `linux/amd64` 的 ECS；若实例是 ARM 架构，将平台改为 `linux/arm64`。每次发布使用新的、不可复用的版本标签，并将同一标签写入 ECS 的 `.env`：

```bash
docker login
docker buildx build --platform linux/amd64 \
  -t YOUR_DOCKERHUB_USERNAME/supermew:v0.1.0-cpu \
  --push .
```

应用镜像包含代码和 CPU 版 PyTorch，不包含 `.env`、上传文件、数据库数据或下载后的 BGE 模型。模型在首次启动时下载到 ECS 的 `volumes/huggingface`；后续重建容器或更新应用镜像会复用该目录，无须重复下载。Docker 官方支持用 `buildx --push` 将指定平台的构建结果直接推送到镜像仓库。

PostgreSQL、Redis、Milvus 和 Attu 使用 Docker Hub 镜像，etcd 镜像来自 Quay。`minio/minio` 原仓库已无法拉取，Compose 暂用 JumpServer 发布的同版本 MinIO 镜像，并固定 digest。部署生产环境前，请确认你接受该第三方镜像来源；也可以自行构建、推送 MinIO 镜像，通过 `.env` 中的 `MINIO_IMAGE` 覆盖。若要求**所有镜像都存放在你自己的 Docker Hub 命名空间**，可以将第三方镜像逐个 `docker pull`、`docker tag`、`docker push` 后，通过 `.env` 中的 `POSTGRES_IMAGE` 等变量覆盖来源。镜像版本和后续安全更新将由你负责。示例（etcd）：

```bash
docker pull quay.io/coreos/etcd:v3.5.18
docker tag quay.io/coreos/etcd:v3.5.18 YOUR_DOCKERHUB_USERNAME/etcd:v3.5.18
docker push YOUR_DOCKERHUB_USERNAME/etcd:v3.5.18
```

## 服务器准备

- 安装 Docker Engine 和 Docker Compose 插件。建议至少 4 核、8 GB 内存，并留出模型缓存、数据库和向量数据所需磁盘空间。小规格 ECS 请先实测内存与上传时的 CPU 占用。
- 域名和 HTTPS 可由 ECS 上的 Nginx 反向代理提供。安全组仅开放 80/443；应用、数据库等端口都只在本机或 Compose 网络可达。

## ECS 首次启动

```bash
git clone <仓库地址> /opt/supermew
cd /opt/supermew
cp .env.docker.example .env
chmod 600 .env
# 编辑 .env：填入已推送的 SUPERMEW_APP_IMAGE、各服务凭证和模型 API 凭证
# 私有 Docker Hub 仓库需在 ECS 上先执行 docker login
docker compose config --quiet
docker compose pull
docker compose up -d --no-build
docker compose ps
docker compose logs -f app
curl -f http://127.0.0.1:8000/docs
```

`.env` 中的 `DATABASE_URL`、`REDIS_URL`、`MILVUS_HOST` 必须分别指向 Compose 服务名 `postgres`、`redis`、`standalone`，不能使用 `127.0.0.1`。PostgreSQL 和 Redis 密码要与对应 URL 一致；如果密码含 URL 特殊字符，要在 URL 中编码。不要直接把旧版宿主机部署的 `.env` 拷贝过来而不检查这些地址。

已有 Milvus 数据的部署升级时，MinIO 凭证应与原服务一致；如果同时修改 MinIO 凭证，确保 `MINIO_ROOT_USER` 和 `MINIO_ROOT_PASSWORD` 与 Milvus 端同步，并先做好备份。

第一次启动时，`app` 下载 `BAAI/bge-m3` 到 `volumes/huggingface`，会比后续重启慢。请保留该目录；删除缓存、更换模型或服务器时可能需要重新下载。依赖锁文件使用 CPU 版 PyTorch；服务器不需要 GPU、CUDA 驱动或 CUDA Toolkit。`SUPERMEW_APP_IMAGE` 应填写确实已推送的镜像及标签；更新代码后需先构建推送新标签，再修改 ECS 的 `.env` 并重新拉取。

数据在 `volumes/postgres`、`volumes/redis`、`volumes/etcd`、`volumes/minio`、`volumes/milvus`、`volumes/huggingface` 和 `data`。升级镜像时保留这些目录。备份时请同时覆盖数据库、Milvus 依赖数据以及 `data` 下的上传文件和 BM25 状态。

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

发布新镜像后，先将 ECS `.env` 的 `SUPERMEW_APP_IMAGE` 改成新标签。`deploy.sh` 会从 `main` 快进拉取 Compose 配置、拉取镜像并等待应用就绪，**不会在 ECS 构建**。如果不是从 `main` 部署，请手动更新配置后执行 `docker compose pull && docker compose up -d --no-build`。

Attu 默认不运行。需要临时排查 Milvus 时用 `docker compose --profile debug up -d attu`，并通过 SSH 隧道访问本机的 `8080` 端口。不要把 Attu、MinIO、Milvus、PostgreSQL 或 Redis 端口放到 ECS 公网安全组。
