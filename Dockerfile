FROM node:22-bookworm-slim AS zd-admin-build

WORKDIR /app/zd-admin
ENV NODE_OPTIONS=--max-old-space-size=4096
RUN corepack enable
COPY zd-admin/package.json zd-admin/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile
COPY zd-admin/ ./
RUN pnpm exec vite build --mode pro

FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=zd-admin-build /app/zd-admin/dist ./zd-admin/dist

EXPOSE 10000

CMD ["python", "main.py"]
