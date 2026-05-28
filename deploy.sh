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
    echo "sudo journalctl -u supermew -n 50"
fi
