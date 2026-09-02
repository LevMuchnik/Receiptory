# Developing Receiptory in place with Claude Code (UNRAID / NAS)

This repo is deployed on an UNRAID NAS under `/mnt/user/appdata/Receiptory` and
runs as a Docker container. This guide sets up **Claude Code as its own small
container** so you can edit and fix the app in place without installing anything
on the UNRAID host.

## Why not install Claude Code on the UNRAID host

UNRAID boots its OS into RAM from the USB flash drive; the root filesystem is
rebuilt on every reboot. Anything you `npm install -g` on the host (Node, Claude
Code) disappears on restart and clutters the appliance. The UNRAID-native pattern
is: run everything in a container, keep all state in `/mnt/user/appdata`. We do
the same for Claude Code.

## What's here

- **`Dockerfile.claude`** — a lightweight `node:24` image with the Claude Code CLI,
  `git`, `ripgrep`, and the Docker *client* + Compose v2 plugin (client-only, no
  daemon; drives the host daemon via the mounted socket).
- **`docker-compose.claude.yml`** — runs that image as a `claude-dev` container,
  bind-mounting the repo and persisting Claude's auth.

## Setup

From the repo directory on the NAS (`/mnt/user/appdata/Receiptory`):

```bash
docker compose -f docker-compose.claude.yml up -d --build
```

Start an interactive session (or use the container's `>_` console in the UNRAID
Docker tab):

```bash
docker exec -it claude-dev claude
```

On first run, authenticate once:

- **Subscription:** run `/login` inside Claude and paste the code from the printed
  URL (works fine on a headless NAS), **or**
- **API key:** uncomment `ANTHROPIC_API_KEY` in `docker-compose.claude.yml`.

Auth is stored under `./.claude-dev-home` (mounted as `$HOME`), so you log in once
and it survives container and host restarts. That directory is git-ignored.

## The dev loop

Claude edits the live repo files in place. To see changes in the running app,
rebuild and restart the application container — Claude can do this itself via the
mounted Docker socket:

```bash
docker compose up -d --build      # uses ./docker-compose.yml + ./Dockerfile
```

This works because the repo is mounted at the **same absolute path** inside the
sidecar as it has on the host (`RECEIPTORY_DIR`, defaulting to
`/mnt/user/appdata/Receiptory` in `docker-compose.claude.yml`). The Docker CLI
runs in the sidecar, but the host daemon resolves the app's bind mounts (e.g.
`./data`) against the host filesystem — matching paths keeps `./data` pointing at
the real `/mnt/user/appdata/Receiptory/data` instead of a stray empty directory.
If your repo lives elsewhere, set `RECEIPTORY_DIR` (in the environment or a `.env`
file next to the compose file) to its absolute host path.

This reuses the app's real image build (backend + frontend), so the dev container
doesn't need Python/uv/npm. For tighter loops (running `pytest`, hot reload) you
can instead install `uv` and the frontend deps into the dev container and run the
dev servers per the root `CLAUDE.md`.

## Updating Claude Code and Node

Both the Claude Code CLI and Node live **inside the `claude-dev` image**, not on the
host. Because the container's `/usr/local` is an image layer (not a mounted volume),
anything you `npm install -g` or unpack there by hand survives container *restarts*
but is **wiped whenever the sidecar is recreated from its image** (`--build`,
`docker rm`, or an UNRAID container update). So the durable fix always lives in
`Dockerfile.claude`, and applying it means rebuilding the sidecar.

### Updating Claude Code

The CLI is installed by this line in `Dockerfile.claude`:

```dockerfile
RUN npm install -g @anthropic-ai/claude-code bun
```

`npm install -g` already pulls the **latest** published version at build time, so the
durable way to update Claude Code is simply to rebuild the sidecar (this re-runs the
`npm install -g` layer against the current npm registry):

```bash
# from the repo dir on the NAS
docker compose -f docker-compose.claude.yml build --no-cache claude-dev
docker compose -f docker-compose.claude.yml up -d
```

`--no-cache` forces the `npm install -g` layer to re-run; without it Docker may reuse
a cached layer and keep the old version. To pin a specific version instead of latest,
change the line to `@anthropic-ai/claude-code@<version>` before rebuilding.

For a **quick, non-durable** bump inside the running container (lost on next rebuild):

```bash
docker exec claude-dev npm install -g @anthropic-ai/claude-code@latest
```

### Updating Node

Node's version is pinned by the **base image tag** on the `FROM` line of `Dockerfile.claude`:

```dockerfile
FROM node:24-bookworm
```

To move to a new major (e.g. Node 26 LTS when it lands), change the tag and rebuild
the sidecar with the same two commands as above. Keep it at or above the minimum the
Claude Code CLI requires (currently `node >=22`); `node:<major>-bookworm` always
resolves to the latest patch of that line at build time.

> **Heads-up: the rebuild recreates the container you're working in.** If you run the
> rebuild *from inside* a `claude-dev` session, it will terminate that session when the
> container is replaced. Run it from the UNRAID host (or the container's `>_` console),
> then `docker exec -it claude-dev claude` back in. Verify afterward with
> `docker exec claude-dev sh -c 'node --version && claude --version'`.

## Notes & cautions

- **Docker socket = host root.** Mounting `/var/run/docker.sock` gives the dev
  container root-equivalent control of every container on the host. Fine for a
  trusted homelab LAN; comment it out in `docker-compose.claude.yml` if you'd
  rather rebuild the app manually.
- **Work on a branch.** You're editing the live deployed copy. Use
  `git switch -c fix/...`, commit, and push to GitHub before rebuilding so a bad
  edit can't strand the running app.
- **Secrets.** `.env` (your LLM API key) is git-ignored — keep it that way.
- **Permissions.** Files Claude writes may be root-owned. If the app runs as
  `nobody:users`, run `chown -R 99:100 .` afterward, or give the dev container a
  matching UID/GID.
- **Back up `appdata`** (CA Backup plugin) before large changes.

## Tear down

```bash
docker compose -f docker-compose.claude.yml down
```
