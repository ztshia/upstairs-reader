#!/bin/sh
# 更新 NUC 上的 upstairs-rss 到新镜像，并去掉会覆盖新代码的两个挂载
set -u
TS=$(date +%Y%m%d_%H%M%S)
DIR=/home/ztshia/docker/reader

echo "=== 备份 ==="
cp "$DIR/docker-compose.yml" "$DIR/docker-compose.yml.bak_$TS"
echo "$DIR/docker-compose.yml.bak_$TS"
ls -l "$DIR/docker-compose.yml.bak_$TS"

echo "=== 备份数据库 ==="
cp "$DIR/data/rss.db" "$DIR/data/rss.db.bak_$TS"
ls -lh "$DIR/data/rss.db.bak_$TS"

echo "=== 改写 compose：移除 app.py / voice_reference.wav 挂载 ==="
cp "$DIR/docker-compose.yml" "$DIR/.compose.tmp"
# 删掉这两行挂载（含行尾注释）
grep -v -E '^\s*-\s*\./app\.py:/app/app\.py' "$DIR/.compose.tmp" \
  | grep -v -E '^\s*-\s*\./voice_reference\.wav:/app/voice_reference\.wav' \
  > "$DIR/docker-compose.yml"
rm -f "$DIR/.compose.tmp"

echo "--- 新 volumes 段 ---"
sed -n '/volumes:/,/environment:/p' "$DIR/docker-compose.yml"

echo "=== 重建容器 ==="
cd "$DIR" && docker compose up -d 2>&1 | tail -10

echo "=== 等待健康检查 ==="
sleep 12
docker ps --filter name=upstairs-rss --format '{{.Names}} | {{.Image}} | {{.Status}}'
echo "--- 镜像 ID ---"
docker inspect upstairs-rss --format '{{.Image}}'
echo "--- 挂载 ---"
docker inspect upstairs-rss --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{println}}{{end}}'
