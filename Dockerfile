FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Copy only the dependency manifests so this layer is cached until they change
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev


################################
# PRODUCTION
# Final image used for runtime
################################
FROM python:3.12-slim AS production

# Turns off buffering for easier container logging, and prevents .pyc files being written at runtime
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Creates a non-root user with an explicit UID and adds permission to access the /app folder
# For more info, please refer to https://aka.ms/vscode-docker-python-configure-containers
RUN adduser -u 10000 --disabled-password --gecos "" appuser && chown -R appuser /app

COPY --from=builder --chown=appuser /app/.venv /app/.venv
COPY src .
COPY gunicorn.conf.py .

USER appuser

# Run the application with Gunicorn
CMD ["gunicorn", "main:app", "--config", "gunicorn.conf.py"]
