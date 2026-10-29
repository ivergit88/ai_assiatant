# Один Dockerfile на все Python-сервисы: зависимости выбираются аргументом GROUP.
#   docker build -f infra/Dockerfile --build-arg GROUP=mcp .
FROM python:3.12-slim

# Российский корневой сертификат (Минцифры): без него bot не достучится до MAX API
# по HTTPS — "certificate verify failed: unable to get local issuer certificate".
# Другим сервисам не мешает, поэтому ставим один раз для всех, а не отдельным Dockerfile.
# Официальный способ получения — https://developers.sber.ru/docs/ru/gigachat/certificates.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl \
    && curl -fsSL -o /usr/local/share/ca-certificates/russian_trusted_root_ca.crt \
        https://gu-st.ru/content/lending/russian_trusted_root_ca_pem.crt \
    && update-ca-certificates \
    && apt-get purge -y --auto-remove curl \
    && rm -rf /var/lib/apt/lists/*

# httpx игнорирует системный store сертификатов и использует свой (certifi),
# если не указать явно эту переменную — тогда он возьмёт системный бандл,
# в который мы только что добавили российский корневой сертификат.
ENV SSL_CERT_FILE=/etc/ssl/certs/ca-certificates.crt

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
