#!/bin/sh
# 备份 hosts，写入 Docker Hub 的正确 IPv4，规避 DNS 污染
set -u
PWD_SUDO='chanel'

cp /etc/hosts /etc/hosts.bak_wb 2>/dev/null || true

# 先清掉旧的 wb 标记行
grep -v '# wb-dockerhub' /etc/hosts > /tmp/hosts.new

cat >> /tmp/hosts.new <<'EOF'
# wb-dockerhub - 规避 DNS 污染，固定可用的 IPv4
98.86.185.36    registry-1.docker.io # wb-dockerhub
54.88.93.133    registry-1.docker.io # wb-dockerhub
44.197.81.201   registry-1.docker.io # wb-dockerhub
104.18.43.178   auth.docker.io # wb-dockerhub
104.18.42.178   auth.docker.io # wb-dockerhub
EOF

echo "$PWD_SUDO" | sudo -S cp /tmp/hosts.new /etc/hosts 2>/dev/null
rm -f /tmp/hosts.new

echo "=== 新 hosts（docker 部分）==="
grep -i docker /etc/hosts

echo "=== 验证解析与连通 ==="
for h in registry-1.docker.io auth.docker.io; do
  printf '%s -> ' "$h"
  getent ahostsv4 "$h" | head -1 | awk '{print $1}'
done
curl -sS -m 10 -o /dev/null -w 'registry v2: %{http_code}\n' https://registry-1.docker.io/v2/
curl -sS -m 10 -o /dev/null -w 'auth token: %{http_code}\n' 'https://auth.docker.io/token?service=registry.docker.io&scope=repository:ztshia/upstairs-reader:push'
