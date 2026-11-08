#!/bin/sh
# 更新后验证：代码版本、数据量、key 脱敏、批注气泡样式、TTS
set -u
B=http://127.0.0.1:5321

echo "=== 1) 页面与静态资源 ==="
curl -s -o /dev/null -w 'home      -> %{http_code}\n' $B/
curl -s -o /dev/null -w 'login     -> %{http_code}\n' $B/login
printf 'css v115  -> '
curl -s "$B/static/index.css?v=115" | grep -c 'width: max-content'
printf 'bubble px -> '
curl -s "$B/static/index.css" | grep -o 'max-width: min(300px, calc(100vw - 40px))' | head -1
printf 'TEMPLATES_AUTO_RELOAD in image: '
docker exec upstairs-rss grep -c 'TEMPLATES_AUTO_RELOAD' /app/app.py
printf 'voice_reference in image: '
docker exec upstairs-rss md5sum /app/voice_reference.wav | cut -d' ' -f1

echo "=== 2) 数据完整性 ==="
curl -s $B/api/stats
echo
docker exec upstairs-rss python -c "
import sqlite3
db=sqlite3.connect('/data/rss.db')
print('feeds=', db.execute('select count(*) from feeds').fetchone()[0])
print('items=', db.execute('select count(*) from items').fetchone()[0])
print('users=', db.execute('select count(*) from users').fetchone()[0])
print('notes=', db.execute(\"select count(*) from user_items where note is not null and note!='' and note!='[]'\").fetchone()[0])
"

echo "=== 3) key 脱敏（未登录）==="
curl -s $B/api/settings/ai | python -c "
import sys,json
d=json.load(sys.stdin)
print('ai_api_key =', repr(d.get('ai_api_key')))
print('tts_api_key=', repr(d.get('tts_api_key')))
"

echo "=== 4) 批注 key 与 TTS 配置仍在 ==="
docker exec upstairs-rss python -c "
import sqlite3
db=sqlite3.connect('/data/rss.db')
cfg=dict(db.execute('select key,value from site_settings').fetchall())
k=cfg.get('tts_api_key') or ''
print('tts_model  =', cfg.get('tts_model'))
print('tts_key len=', len(k), '| 前缀', k[:6] + '...' if k else 'EMPTY')
print('ai_key  len=', len(cfg.get('ai_api_key') or ''))
"

echo "=== 5) 日志 ==="
docker logs --tail 6 upstairs-rss 2>&1
