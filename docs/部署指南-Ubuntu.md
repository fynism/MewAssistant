# SuperMew Ubuntu 生产环境部署指南

> **适用系统**：Ubuntu 22.04 LTS / 24.04 LTS  
> **前置条件**：本地项目已完成部署就绪改造（`.env.example` 模板完整、`docker-compose.yml` 密码改为环境变量、端口绑定 `127.0.0.1`）。直接 `git clone` 即可部署。

## 部署架构

```
用户浏览器
    │
    ▼
Nginx (80/443, HTTPS 反向代理)
    │
    └── FastAPI (Uvicorn 127.0.0.1:8000)
            │
            ├── PostgreSQL (5432)
            ├── Redis (6379)
            ├── Milvus (19530) → etcd + MinIO
            ├── BGE-M3 本地稠密向量模型 (~2GB 内存)
            ├── DeepSeek API（大模型）
            └── Jina Rerank API（重排序）
```

---

## 一、服务器最低配置

| 资源 | 最低 | 推荐 |
|---|---|---|
| CPU | 2 核 | 4 核 |
| 内存 | 4 GB | 8 GB（BGE-M3 ≈ 2GB + Milvus ≈ 1GB） |
| 硬盘 | 30 GB | 50 GB+（向量数据持续增长） |
| 系统 | Ubuntu 22.04 LTS | Ubuntu 24.04 LTS |

---

## 二、安装基础依赖

### 2.1 更新系统

```bash
sudo apt update && sudo apt upgrade -y
```

### 2.2 安装 Docker

```bash
# 卸载旧版本（如有）
sudo apt remove -y docker docker-engine docker.io containerd runc 2>/dev/null

# 安装依赖
sudo apt install -y ca-certificates curl gnupg lsb-release

# 添加 Docker 官方 GPG 密钥
sudo mkdir -p /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg

# 添加 Docker 仓库
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

# 安装 Docker Engine + Docker Compose 插件
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin

# 启动 Docker
sudo systemctl start docker
sudo systemctl enable docker

# 把当前用户加入 docker 组（执行后需退出重新登录）
sudo usermod -aG docker $USER
```

### 2.3 安装 Python 3.12

Ubuntu 24.04 默认自带 Python 3.12，无需额外操作。Ubuntu 22.04 需要通过 deadsnakes PPA 安装：

```bash
# Ubuntu 22.04 需要此步骤；24.04 跳过
sudo apt install -y software-properties-common
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt update
sudo apt install -y python3.12 python3.12-venv
```

验证：

```bash
python3.12 --version
# 应输出 Python 3.12.x
```

### 2.4 安装 uv（Python 包管理）

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

安装完成后直接可用。新版 uv 装到 `~/.local/bin` 或 `~/.cargo/bin`，均在 `PATH` 中，不需要额外 source。验证：

```bash
uv --version
```

### 2.5 安装 Nginx

```bash
sudo apt install -y nginx

sudo systemctl start nginx
sudo systemctl enable nginx
```

---

## 三、拉取代码 & 配置环境变量

### 3.1 克隆项目

```bash
cd /opt
git clone <你的仓库地址> supermew
cd supermew
```

### 3.2 理解：哪些文件已经配好了，哪些需要你手动创建

| 文件 | 状态 | 你需要做什么 |
|---|---|---|
| `docker-compose.yml` | **已就绪** | 无需修改。密码通过 `${POSTGRES_PASSWORD:-postgres}` 和 `${REDIS_PASSWORD:-}` 控制 |
| `.env.example` | **已就绪** | 完整模板，覆盖全部环境变量 |
| `.env` | **不存在**（gitignore 忽略） | 手动创建，填入真实密钥 |
| `data/`、`volumes/` | **不存在**（gitignore 忽略） | 运行时会自动生成 |

### 3.3 生成安全密钥

```bash
# JWT 随机密钥
openssl rand -hex 32

# PostgreSQL 密码
openssl rand -base64 16
# 将输出记为 PG_PASSWORD

# Redis 密码
openssl rand -base64 16
# 将输出记为 REDIS_PASSWORD
```

### 3.4 创建 `.env` 文件

```bash
cp .env.example .env
vim .env
```

将以下模板中标记 `← 替换` 的 5 处占位符替换为真实值：

```env
# ==================== LLM 大模型 ====================
ARK_API_KEY=sk-xxxxxxxx          # ← 替换
MODEL=deepseek-v4-pro
GRADE_MODEL=deepseek-v4-flash
FAST_MODEL=deepseek-v4-flash
BASE_URL=https://api.deepseek.com

# ==================== 本地稠密向量 ====================
EMBEDDING_MODEL=BAAI/bge-m3
EMBEDDING_DEVICE=cpu
DENSE_EMBEDDING_DIM=1024
# 注意：不要设置 HF_HUB_OFFLINE=1，否则首次部署无法下载模型

# ==================== Rerank 重排序 ====================
RERANK_MODEL=jina-reranker-v3
RERANK_BINDING_HOST=https://api.jina.ai/v1/rerank
RERANK_API_KEY=jina_xxxxxxxx     # ← 替换

# ==================== Milvus 向量数据库 ====================
MILVUS_HOST=127.0.0.1
MILVUS_PORT=19530
MILVUS_COLLECTION=embeddings_collection

# ==================== PostgreSQL ====================
# 密码必须与 POSTGRES_PASSWORD 一致
DATABASE_URL=postgresql+psycopg2://postgres:<PG_PASSWORD>@127.0.0.1:5432/langchain_app

# ==================== Redis ====================
# 密码必须与 REDIS_PASSWORD 一致
REDIS_URL=redis://:<REDIS_PASSWORD>@127.0.0.1:6379/0
REDIS_KEY_PREFIX=supermew
REDIS_CACHE_TTL_SECONDS=300

# ==================== JWT 鉴权 ====================
JWT_SECRET_KEY=<JWT_SECRET>      # ← 替换为 openssl rand -hex 32 输出
JWT_ALGORITHM=HS256
JWT_EXPIRE_MINUTES=1440

# ==================== 密码哈希 ====================
PASSWORD_PBKDF2_ROUNDS=310000

# ==================== 管理员邀请码 ====================
ADMIN_INVITE_CODE=supermew-admin-2026

# ==================== Auto-Merging ====================
AUTO_MERGE_ENABLED=true
AUTO_MERGE_THRESHOLD=2
LEAF_RETRIEVE_LEVEL=3

# ==================== 高德天气 API（可选）====================
AMAP_WEATHER_API=https://restapi.amap.com/v3/weather/weatherInfo
AMAP_API_KEY=你的高德key          # ← 可选
```

```bash
# 设置 .env 权限（仅 root 可读写）
chmod 600 /opt/supermew/.env
```

### 3.5 设置密码并启动 Docker

`docker-compose.yml` 已配置为从环境变量读取密码。Ubuntu 上推荐**把密码写入 `.env`**（docker compose 会自动读取）：

```bash
echo "POSTGRES_PASSWORD=你的PG密码" >> /opt/supermew/.env
echo "REDIS_PASSWORD=你的Redis密码" >> /opt/supermew/.env
```

> **关键对应关系**：`.env` 中 `DATABASE_URL` 的密码 = `POSTGRES_PASSWORD` 的值；`REDIS_URL` 的密码 = `REDIS_PASSWORD` 的值。三处必须一致。

---

## 四、启动 Docker 基础设施

```bash
cd /opt/supermew

# 启动所有依赖服务
docker compose up -d

# 查看运行状态（预期 6 个容器全部 Up/healthy）
docker compose ps
```

容器列表：

| 容器名 | 服务 | 端口（均绑定 127.0.0.1，不暴露公网） |
|---|---|---|
| supermew-postgres | PostgreSQL 15 | 5432 |
| supermew-redis | Redis 7 | 6379 |
| milvus-etcd | etcd（Milvus 元数据） | — |
| milvus-minio | MinIO（Milvus 对象存储） | 9000/9001 |
| milvus-standalone | Milvus 向量数据库 | 19530/9091 |
| milvus-attu | Attu（Milvus 管理面板） | 8080 |

等待 1-2 分钟让 Milvus 完全启动：

```bash
curl http://localhost:9091/healthz
# 返回 "OK" 即正常

# 如启动失败：
docker compose logs standalone
```

---

## 五、安装依赖 & 首次启动应用

```bash
cd /opt/supermew

# 安装 Python 依赖
uv sync

# 前台测试（确认无报错后 Ctrl+C 停掉）
uv run uvicorn backend.app:app --host 127.0.0.1 --port 8000
```

首次启动时 BGE-M3 模型会自动下载到 `~/.cache/huggingface/`，约 2GB。如国内下载慢，先设置镜像：

```bash
export HF_ENDPOINT=https://hf-mirror.com
```

浏览器访问 `http://服务器IP:8000` 确认页面正常，然后 `Ctrl+C`。

---

## 六、Ubuntu 防火墙配置（ufw）

```bash
# 启用 ufw
sudo ufw default deny incoming
sudo ufw default allow outgoing

# 只开放必要端口
sudo ufw allow 22/tcp      # SSH
sudo ufw allow 80/tcp      # HTTP
sudo ufw allow 443/tcp     # HTTPS

# 启动防火墙
sudo ufw enable

# 确认规则
sudo ufw status verbose
```

> Ubuntu 没有 SELinux（使用 AppArmor），不会出现 CentOS 常见的 SELinux 阻断 Nginx 连接问题。

---

## 七、配置 systemd 服务

让应用开机自启、崩溃自动重启。

```bash
sudo vim /etc/systemd/system/supermew.service
```

```ini
[Unit]
Description=SuperMew RAG Chatbot
After=network.target docker.service
Requires=docker.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/supermew
Environment=PATH=/root/.local/bin:/root/.cargo/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin
ExecStart=uv run uvicorn backend.app:app --host 127.0.0.1 --port 8000 --workers 1
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl start supermew
sudo systemctl enable supermew

# 确认运行
sudo systemctl status supermew

# 实时日志
sudo journalctl -u supermew -f
```

> **为什么 `--workers 1`？** 项目使用全局单例（`embedding_service`、`rag_graph`）+ `asyncio.Queue` 跨线程调度，多 worker 会导致状态不一致。

---

## 八、配置 Nginx 反向代理

### 8.1 创建站点配置

```bash
sudo vim /etc/nginx/sites-available/supermew
```

```nginx
server {
    listen 80;
    server_name 你的域名.com;

    client_max_body_size 50M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE 流式输出：必须关闭缓冲
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```

```bash
# 启用站点
sudo ln -s /etc/nginx/sites-available/supermew /etc/nginx/sites-enabled/

# 移除默认站点（可选，避免冲突）
sudo rm -f /etc/nginx/sites-enabled/default

# 检查语法并重载
sudo nginx -t
sudo systemctl reload nginx
```

### 8.2 SSL 证书（Let's Encrypt）

```bash
# 安装 certbot
sudo apt install -y certbot python3-certbot-nginx

# 申请证书（自动修改 Nginx 配置、添加 HTTPS 重定向）
sudo certbot --nginx -d 你的域名.com

# 测试自动续期
sudo certbot renew --dry-run

# Ubuntu 上 certbot 会自动配置 systemd timer，确认一下
sudo systemctl status certbot.timer
```

---

## 九、验证部署

```bash
# 1. Docker 服务
docker compose -f /opt/supermew/docker-compose.yml ps

# 2. 应用进程
sudo systemctl status supermew

# 3. API 文档
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/docs
# 应返回 200

# 4. Nginx
sudo nginx -t && sudo systemctl status nginx

# 5. 浏览器访问 https://你的域名.com
#    注册 → 登录 → 上传文档 → 提问，全链路测试
```

---

## 十、代码更新与日常运维

### 10.1 一键部署脚本 `deploy.sh`

```bash
vim /opt/supermew/deploy.sh
chmod +x /opt/supermew/deploy.sh
```

```bash
#!/bin/bash
set -e
cd /opt/supermew

echo "=== 拉取最新代码 ==="
git pull origin main

echo "=== 更新依赖 ==="
uv sync

echo "=== 重启 Docker 服务（如有变更）==="
docker compose up -d

echo "=== 等待依赖服务就绪 ==="
sleep 5

echo "=== 重启应用 ==="
sudo systemctl restart supermew

echo "=== 等待应用启动 ==="
sleep 3

echo "=== 验证 ==="
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8000/docs)
if [ "$HTTP_CODE" = "200" ]; then
    echo "部署成功"
else
    echo "部署异常，HTTP 状态码: $HTTP_CODE，请检查日志"
fi
```

以后更新只需：`cd /opt/supermew && bash deploy.sh`

### 10.2 日常运维命令

```bash
# === 应用管理 ===
sudo systemctl status supermew          # 状态
sudo systemctl restart supermew         # 重启
sudo systemctl stop supermew            # 停止
sudo journalctl -u supermew -f          # 实时日志
sudo journalctl -u supermew -n 100      # 最近 100 行

# === Docker 服务管理 ===
cd /opt/supermew
docker compose ps                       # 容器状态
docker compose restart                  # 重启全部
docker compose restart standalone       # 单独重启 Milvus
docker compose logs -f standalone       # Milvus 日志
docker compose logs -f postgres         # PostgreSQL 日志

# === 数据库备份 ===
docker exec supermew-postgres pg_dump -U postgres langchain_app \
  > backup_$(date +%Y%m%d_%H%M%S).sql

# === 磁盘 ===
du -sh /opt/supermew/volumes/*
df -h
```

---

## 十一、常见问题

### Q1：`uv sync` 报错 "Python 3.12 not found"

```bash
# Ubuntu 22.04 需要指定 Python 路径
which python3.12
# 如果找不到，确认 deadsnakes PPA 已添加（见 2.3 节）
```

### Q2：Milvus 启动失败（etcd 连接超时）

```bash
cd /opt/supermew
docker compose down
docker compose up -d
# 等待 2 分钟让 etcd 就绪
```

### Q3：BGE-M3 模型下载慢 / 失败

```bash
# 在 .env 中设置 HuggingFace 镜像
echo "HF_ENDPOINT=https://hf-mirror.com" >> /opt/supermew/.env
sudo systemctl restart supermew
```

### Q4：`docker compose up -d` 提示权限不足

```bash
# 当前用户不在 docker 组
sudo usermod -aG docker $USER
# 退出重新登录，或执行 newgrp docker
```

### Q5：Nginx 502 Bad Gateway

```bash
# 确认应用在运行
sudo systemctl status supermew

# 确认端口监听
ss -tlnp | grep 8000

# 查看应用日志
sudo journalctl -u supermew -n 50
```

### Q6：内存不足

```bash
free -h

# 关闭非必需的 Attu 管理面板
docker compose stop attu
```

---

## 附录：部署检查清单

### 环境安装
- [ ] Ubuntu 系统已更新
- [ ] Docker + Docker Compose 已安装并启动
- [ ] Python 3.12+ 已安装
- [ ] uv 已安装
- [ ] Nginx 已安装

### 项目配置
- [ ] 代码已 clone 到 `/opt/supermew`
- [ ] `cp .env.example .env` 完成
- [ ] `.env` 中 5 处占位符已替换：`ARK_API_KEY`、`RERANK_API_KEY`、`DATABASE_URL` 密码、`REDIS_URL` 密码、`JWT_SECRET_KEY`
- [ ] `.env` 中**没有** `HF_HUB_OFFLINE=1`
- [ ] `.env` 权限为 600
- [ ] `POSTGRES_PASSWORD` 和 `REDIS_PASSWORD` 已写入 `.env`

### 启动验证
- [ ] `docker compose up -d` 成功（6 容器 running）
- [ ] `curl http://localhost:9091/healthz` 返回 OK
- [ ] `uv sync` 完成
- [ ] `uv run uvicorn ...` 前台测试无报错

### 服务配置
- [ ] systemd 服务已创建：`sudo systemctl status supermew`
- [ ] ufw 仅开放 22/80/443
- [ ] Nginx 配置完成：`sudo nginx -t`
- [ ] SSL 证书已申请：`sudo certbot --nginx -d 你的域名.com`

### 功能验证
- [ ] `https://域名.com` 正常显示登录页
- [ ] 普通用户注册成功
- [ ] 管理员注册成功（邀请码）
- [ ] 管理员上传 PDF，返回分块数
- [ ] 普通用户提问知识库问题，流式逐字回复
- [ ] 点击"停止"按钮，回复中断
- [ ] 历史会话回显正常
