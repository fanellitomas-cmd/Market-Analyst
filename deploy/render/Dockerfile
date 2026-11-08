# Imagen única para Render (plan Free): la API (FastAPI) y el sitio (Next.js) en un solo
# servicio web. El agente no corre acá (el servicio gratuito se duerme): lo corre GitHub
# Actions (.github/workflows/agente.yml). Se construye desde la raíz del repositorio.

# ---------- Sitio (Next.js) ----------
FROM node:22-bookworm-slim AS web
WORKDIR /web
ENV NEXT_TELEMETRY_DISABLED=1 BACKEND_URL=http://127.0.0.1:8000
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# ---------- API + sitio ----------
FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 NEXT_TELEMETRY_DISABLED=1 NODE_ENV=production
# Node para servir el sitio (mismo Debian que la etapa de compilación).
COPY --from=web /usr/local/bin/node /usr/local/bin/node

WORKDIR /app
COPY backend/pyproject.toml ./
COPY backend/app ./app
RUN pip install --no-cache-dir .
COPY backend/scripts ./scripts

COPY --from=web /web/.next/standalone /web
COPY --from=web /web/.next/static /web/.next/static
COPY --from=web /web/public /web/public
COPY deploy/render/start.sh /start.sh

RUN useradd --create-home appuser && chmod +x /start.sh
USER appuser

# Render indica el puerto público en $PORT (10000 por defecto); la API queda interna.
ENV PORT=10000
EXPOSE 10000
CMD ["/start.sh"]
