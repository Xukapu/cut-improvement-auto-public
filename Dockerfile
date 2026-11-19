FROM node:22-bookworm-slim AS frontend
WORKDIR /build/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/index.html frontend/tsconfig*.json frontend/vite.config.ts ./
COPY frontend/src ./src
COPY frontend/public ./public
RUN npm run build

FROM python:3.13-slim-bookworm
ENV PYTHONUNBUFFERED=1 \
    PYTHONPATH=/srv/sto/backend \
    CUT_FRONTEND_DIST=/srv/sto/frontend/dist
WORKDIR /srv/sto

RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 cut \
    && useradd --uid 10001 --gid cut --no-create-home cut \
    && install -d -o cut -g cut data data/dispute_photos

COPY pyproject.toml alembic.ini ./
COPY backend/app ./backend/app
RUN python -m pip install --no-cache-dir .
COPY scripts/bootstrap_owner.py ./scripts/bootstrap_owner.py
COPY --from=frontend /build/frontend/dist ./frontend/dist
USER cut
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
