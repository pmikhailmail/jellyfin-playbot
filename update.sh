#!/bin/sh
set -e

OUTER_DIR=~/docker/jellyfin-playbot
REPO_DIR=$OUTER_DIR/jellyfin-playbot
ENV_BACKUP=/tmp/playbot_env_backup

# 1. Create outer directory if it doesn't exist
mkdir -p "$OUTER_DIR"

# 2. Stop the container if already running
if [ -f "$REPO_DIR/docker-compose.yml" ]; then
  cd "$REPO_DIR"
  docker compose down
fi

# 3. Back up .env if it exists
if [ -f "$REPO_DIR/.env" ]; then
  cp "$REPO_DIR/.env" "$ENV_BACKUP"
fi

# 4. Delete old clone and re-clone from GitHub
cd "$OUTER_DIR"
rm -rf jellyfin-playbot
docker run --rm \
  -v "$(pwd)":/workspace \
  alpine/git clone https://github.com/pmikhailmail/jellyfin-playbot.git /workspace/jellyfin-playbot

# 5. Restore .env (if it was backed up)
if [ -f "$ENV_BACKUP" ]; then
  cp "$ENV_BACKUP" "$REPO_DIR/.env"
else
  echo "WARNING: .env not found. Create $REPO_DIR/.env before starting the container."
fi

# 6. Build and start
cd "$REPO_DIR"
docker compose build --no-cache
docker compose up -d
