# ADR 0003: An HTTP transport and a container, without authentication

**Status:** accepted · **Date:** 2026-11-02

## Context

Over stdio the client launches the server, so every developer needs Python 3.14, uv and
the warehouse drivers locally. Running it as a service (one container, several clients)
needs a network transport, and a network transport is a new way in.

## Decision

- Add MCP Streamable HTTP alongside stdio, **stateless with JSON responses**: no session
  state to lose on restart, and any replica can answer.
- **No authentication in the server.** Authentication belongs to the deployment (an
  identity-aware proxy or gateway), not to a read-only tool server. Until then the service
  is local-only: Compose publishes on `127.0.0.1`, and the docs say so.
- **DNS-rebinding protection is always on**, including when the process binds `0.0.0.0`
  inside a container. The MCP SDK enables it only for loopback binds, so the allow-list is
  set explicitly: localhost plus names passed with `--allowed-host`. A web page in the
  user's browser therefore cannot reach the service through a rebound domain.
- **The container is a fourth read-only layer.** Non-root user, read-only root filesystem,
  all capabilities dropped, `no-new-privileges`. Credentials arrive as secret files, which
  the settings read from `/run/secrets`, so they never appear in `docker inspect`.
- `/healthz` is liveness only. Probing the warehouse every 30 seconds would spend credits
  and keep Snowflake warehouses awake.
- The image targets Python 3.14, where every backend has wheels.

## Consequences

- Exposing the service beyond one machine requires a proxy that authenticates; the
  `--allowed-host` flag exists so that proxy's hostname can pass the Host check.
- Browser sign-in to Snowflake is impossible in a container, so the Snowflake profile
  requires key-pair authentication.
- CI verifies the security properties (foreign Host refused, non-root, read-only), not
  only that the image builds.
