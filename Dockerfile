FROM node:20-bookworm-slim AS web-build

WORKDIR /app/apps/morpheus-studio
COPY apps/morpheus-studio/package.json apps/morpheus-studio/package-lock.json ./
RUN npm ci
COPY apps/morpheus-studio/ ./
RUN npm run build:web

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SCREENWIRE_WEB_DIST_DIR=/app/apps/morpheus-studio/dist \
    SCREENWIRE_PROJECTS_ROOT=/data/projects \
    SCREENWIRE_LOG_DIR=/data/logs \
    SCREENWIRE_SERVICE_ROLE=web

RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
COPY --from=web-build /app/apps/morpheus-studio/dist /app/apps/morpheus-studio/dist

RUN chmod +x /app/bin/start-service.sh

EXPOSE 8000

CMD ["/app/bin/start-service.sh"]
