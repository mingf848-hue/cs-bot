# 1. 使用轻量级 Python 3.11 系统（下载快，无需编译）
FROM python:3.11-slim

# 2. 设置工作目录
WORKDIR /app

# 3. 设置环境变量，让日志立即显示
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 4. 安装 sing-box，用于把 Trojan 节点转成本地 SOCKS5
ARG SING_BOX_VERSION=1.13.13
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl tar \
    && arch="$(dpkg --print-architecture)" \
    && case "$arch" in amd64) sb_arch="amd64" ;; arm64) sb_arch="arm64" ;; *) echo "unsupported arch: $arch" >&2; exit 1 ;; esac \
    && curl -fsSL -o /tmp/sing-box.tar.gz "https://github.com/SagerNet/sing-box/releases/download/v${SING_BOX_VERSION}/sing-box-${SING_BOX_VERSION}-linux-${sb_arch}.tar.gz" \
    && tar -xzf /tmp/sing-box.tar.gz -C /tmp \
    && mv "/tmp/sing-box-${SING_BOX_VERSION}-linux-${sb_arch}/sing-box" /usr/local/bin/sing-box \
    && chmod +x /usr/local/bin/sing-box \
    && rm -rf /var/lib/apt/lists/* /tmp/sing-box*

# 5. 复制依赖文件并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 6. 复制所有代码文件到容器里
COPY . .

# 7. 告诉 Zeabur 这是一个 Web 服务
EXPOSE 10000

# 8. 启动命令 (关键！)
# 如果你的 Python 脚本名不是 tg_bot_v27_triangle_fix.py，请修改下面这一行
CMD ["python", "main.py"]
