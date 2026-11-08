#!/bin/sh
# TTS 端到端实测
set -u
echo "=== /api/tts/speak（voiceclone，参考音频来自镜像）==="
curl -s -m 90 -X POST http://127.0.0.1:5321/api/tts/speak \
  -H 'Content-Type: application/json' \
  -d '{"text":"更新完成，音色克隆测试。"}' \
  -o /tmp/tts_check.bin \
  -w 'HTTP %{http_code} | type=%{content_type} | size=%{size_download} bytes\n'
echo -n '文件头: '
head -c 4 /tmp/tts_check.bin | od -c | head -1
rm -f /tmp/tts_check.bin
