#!/bin/sh
# 清理：9月2日旧数据库备份 + 悬空镜像
set -u
DIR=/home/ztshia/docker/reader/data

echo "=== 删除前：确认当前库可用 ==="
docker exec upstairs-rss python -c "
import sqlite3
db=sqlite3.connect('/data/rss.db')
print('integrity:', db.execute('pragma integrity_check').fetchone()[0])
print('feeds=%d items=%d' % (
  db.execute('select count(*) from feeds').fetchone()[0],
  db.execute('select count(*) from items').fetchone()[0]))
"

echo "=== 删除 9月2日旧备份（537M）==="
if [ -f "$DIR/rss.db.bak_20260902" ]; then
  rm -f "$DIR/rss.db.bak_20260902" && echo "已删除 rss.db.bak_20260902"
fi

echo "=== 删除悬空镜像 dd139663669e（2.93GB）==="
docker rmi dd139663669e 2>&1 | tail -3

echo "=== 删除后 ==="
echo "--- data 目录 ---"
ls -lh "$DIR"
echo "--- 剩余悬空镜像 ---"
docker images -f dangling=true --format '{{.ID}} {{.Size}}' | head -3
echo "(空=已清干净)"
echo "--- 磁盘 ---"
df -h /home | tail -1
echo "--- 容器状态 ---"
docker ps --filter name=upstairs-rss --format '{{.Names}} | {{.Status}}'
