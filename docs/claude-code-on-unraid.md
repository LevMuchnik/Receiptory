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

- **`Dockerfile.claude`** — a lightweight `node:20` image with the Claude Code CLI,
  `git`, `ripgrep`, and the Docker *client* (to drive the host daemon).
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

Claude edits the live files under `/workspace` (= the repo). To see changes in the
running app, rebuild and restart the application container — Claude can do this
itself via the mounted Docker socket:

```bash
docker compose up -d --build      # uses ./docker-compose.yml + ./Dockerfile
```

This reuses the app's real image build (backend + frontend), so the dev container
doesn't need Python/uv/npm. For tighter loops (running `pytest`, hot reload) you
can instead install `uv` and the frontend deps into the dev container and run the
dev servers per the root `CLAUDE.md`.

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
