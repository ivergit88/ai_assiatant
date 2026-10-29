# Один Dockerfile на все Python-сервисы: зависимости выбираются аргументом GROUP.
#   docker build -f infra/Dockerfile --build-arg GROUP=mcp .
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.8 /uv /usr/local/bin/uv

ENV PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

# Сначала только зависимости: этот слой кешируется, пока не меняется uv.lock.
ARG GROUP
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-default-groups --group ${GROUP}

COPY . .

RUN useradd --create-home app
USER app
