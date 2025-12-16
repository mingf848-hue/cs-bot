# 1. 使用轻量级 Python 3.11 系统（下载快，无需编译）
FROM python:3.11-slim

# 2. 设置工作目录
WORKDIR /app

# 3. 设置环境变量，让日志立即显示
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 4. 复制依赖文件并安装
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. 复制所有代码文件到容器里
COPY . .

# 6. 告诉 Zeabur 这是一个 Web 服务
EXPOSE 10000

# 7. 启动命令 (关键！)
# 如果你的 Python 脚本名不是 tg_bot_v27_triangle_fix.py，请修改下面这一行
CMD ["python", "tg_bot_v27_triangle_fix.py"]
