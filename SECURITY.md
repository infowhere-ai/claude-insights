# Security Policy

## Supported Versions

| Version | Supported |
|---------|-----------|
| latest  | ✅        |

## Threat Model

claude-insights is designed to run **on localhost only**. It monitors your local Claude Code
sessions and exposes a web dashboard at `http://localhost:PORT`.

**Known sensitive capabilities (by design):**

| Endpoint | Capability | Risk if exposed to network |
|----------|-----------|---------------------------|
| `GET /api/browse` | Lists directories on the host | Filesystem enumeration |
| `DELETE /api/file` | Deletes untracked git files | File deletion |
| `WebSocket /ws/terminal` | PTY shell access | Remote code execution |

**Never expose this tool to an untrusted network** without additional authentication (e.g., a
reverse proxy with auth). The default binding is `127.0.0.1`.

## Origin and CORS Restrictions

The following protections are enforced by default:

- **WebSocket `/ws/terminal` — Origin header check:** the server validates the `Origin` header
  before accepting any WebSocket connection. Only `http(s)://localhost` and
  `http(s)://127.0.0.1` (with any port) are allowed. Connections from `file://` pages,
  remote hosts, or missing/empty origins are rejected with close code 1008.

- **HTTP API — CORS restricted to localhost:** the CORS middleware only allows requests from
  `http(s)://localhost` and `http(s)://127.0.0.1`. `file://` pages (origin `null`) are
  explicitly rejected — the dashboard is always served from localhost via StaticFiles.

- **Docker port binding:** the provided `docker-compose.yml` binds port 19001 to
  `127.0.0.1` only (`127.0.0.1:19001:19001`). To expose the service on a LAN interface,
  change this mapping explicitly and understand the threat model above.

## Reporting a Vulnerability

**DO NOT open a public GitHub issue for security vulnerabilities.**

Report privately via GitHub's
[Private Vulnerability Reporting](https://github.com/infowhere-ai/claude-insights/security/advisories/new).

Include:
- Description of the vulnerability
- Steps to reproduce
- Affected version (`claude-insights --version`)
- Suggested fix (if any)

We acknowledge reports within **48 hours** and aim to release a patch within **90 days**.
