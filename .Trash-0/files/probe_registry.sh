#!/bin/sh
# 穷举 registry-1.docker.io 的候选 IPv4，找能完成 TLS 握手的那个
set -u

CANDIDATES="103.252.115.49 74.86.142.55 199.59.148.89 50.19.86.14 98.86.185.36 54.88.93.133 3.218.197.120 98.87.236.114 184.193.170.199 44.197.81.201 52.0.154.85 103.240.182.55"

echo "=== 用 DoH 再取一轮权威地址 ==="
for s in 223.5.5.5 119.29.29.29 8.8.4.4; do
  printf 'dig @%s: ' "$s"
  timeout 6 dig +short A registry-1.docker.io @"$s" 2>/dev/null | tr '\n' ' '
  echo
done

echo "=== 逐个测 TLS ==="
for ip in $CANDIDATES; do
  printf '%-18s ' "$ip"
  timeout 10 curl -sS -m 8 --resolve registry-1.docker.io:443:"$ip" \
    -o /dev/null -w 'http=%{http_code} tls=%{time_appconnect}s total=%{time_total}s\n' \
    https://registry-1.docker.io/v2/ 2>&1 | tail -1
done
