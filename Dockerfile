FROM python:3.12-slim

WORKDIR /app

# 安装依赖（利用构建层缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝应用代码
COPY . .

# 创建数据目录与可写目录，并以非 root 用户运行
RUN mkdir -p /data /app/audio_cache /app/static/avatars \
    && useradd --create-home --shell /usr/sbin/nologin appuser \
    && chown -R appuser:appuser /app /data

USER appuser

EXPOSE 5321

CMD ["python", "app.py"]
