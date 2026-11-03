# Releasing

A version tag publishes everywhere at once: PyPI, the container image on GHCR, and the
[official MCP Registry](https://registry.modelcontextprotocol.io), from which
downstream catalogs such as GitHub's pick servers up.

## One-time setup

1. **PyPI trusted publisher.** On pypi.org, under *Your account → Publishing*, add a
   pending publisher: project `warehouse-context-mcp`, owner `AndreMuraro`, repository
   `warehouse-context-mcp`, workflow `ci.yml`, environment `pypi`. No API token is stored.
2. **GitHub environment.** In the repository settings, create an environment named `pypi`
   (optionally requiring your approval before each release).
3. **MCP Registry.** Nothing to set up: the workflow signs in with GitHub OIDC, which grants
   the `io.github.AndreMuraro/*` namespace. Ownership of the PyPI package is proven by the
   `mcp-name` comment at the top of the README.

## Each release

1. Bump `version` in `pyproject.toml` and both versions in `server.json` (a test fails if
   they disagree), then `uv lock`.
2. Commit, then tag and push: `git tag v0.4.0 && git push origin v0.4.0`.
3. CI runs the full checks, then `pypi` → `mcp-registry`, and `image` in parallel.

PyPI versions are permanent: a published version can be yanked but never replaced.

## Later: list the container image too

New GHCR packages start private. After the first release, make
`ghcr.io/andremuraro/warehouse-context-mcp` public in its package settings, then add an
`oci` entry to `server.json` (the Dockerfile already carries the ownership label).
