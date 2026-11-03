# syntax=docker/dockerfile:1
#
# warehouse-context-mcp: a small, non-root image with every backend installed and the
# synthetic demo warehouse baked in.
#
#   docker build -t warehouse-context-mcp .
#   docker run --rm -i warehouse-context-mcp                      # stdio, demo warehouse
#   docker compose up                                             # HTTP on 127.0.0.1:8000
#
# Python 3.14: the Snowflake connector has no 3.15 build yet (see README).

ARG PYTHON_VERSION=3.14
ARG UV_VERSION=0.13

FROM ghcr.io/astral-sh/uv:${UV_VERSION} AS uv

# --- build: resolve from uv.lock into a self-contained virtual environment ------------------
FROM python:${PYTHON_VERSION}-slim AS build
COPY --from=uv /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /src

# Dependencies first, so editing the source does not reinstall them.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-dev --all-extras --no-install-project

COPY . .
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --all-extras --no-editable

RUN /opt/venv/bin/python -m warehouse_context_mcp.demo /opt/demo

# --- runtime: no build tools, no uv, no source tree --------------------------------------------
FROM python:${PYTHON_VERSION}-slim AS runtime

LABEL org.opencontainers.image.title="warehouse-context-mcp" \
      org.opencontainers.image.description="Governed, read-only warehouse context for AI agents over MCP" \
      org.opencontainers.image.source="https://github.com/AndreMuraro/warehouse-context-mcp" \
      org.opencontainers.image.licenses="MIT" \
      io.modelcontextprotocol.server.name="io.github.AndreMuraro/warehouse-context-mcp"

RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --create-home --home-dir /home/app app

COPY --from=build --chown=root:root /opt/venv /opt/venv
COPY --from=build --chown=root:root /opt/demo /opt/demo

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Numeric, so orchestrators can verify runAsNonRoot without resolving names.
USER 10001:10001
WORKDIR /home/app
EXPOSE 8000

ENTRYPOINT ["warehouse-context-mcp"]
CMD ["--duckdb", "/opt/demo/demo.duckdb", "--catalog", "/opt/demo/catalog.yaml"]
