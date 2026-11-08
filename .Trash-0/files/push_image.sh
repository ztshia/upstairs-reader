#!/bin/sh
# 清理构建临时目录与 hosts 临时文件
set -u

rm -rf /home/ztshia/build-upstream
echo "build-upstream 已删除:"
ls -d /home/ztshia/build-upstream 2>&1 | head -1

rm -f /tmp/r.json
echo "=== 保留的镜像 ==="
docker images ztshia/upstairs-reader --format '{{.Repository}}:{{.Tag}} {{.ID}} {{.Size}} {{.CreatedSince}}'
echo "=== hosts 备份仍在 ==="
ls -l /etc/hosts.bak_wb 2>&1 | head -1
