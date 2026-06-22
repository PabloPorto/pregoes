#!/usr/bin/env bash
# Instala os hooks versionados em .git/hooks (rode UMA vez por clone).
set -e
cd "$(git rev-parse --show-toplevel)"
cp scripts/git-hooks/pre-push .git/hooks/pre-push
chmod +x .git/hooks/pre-push
echo "Hook pre-push instalado: a suíte roda antes de cada push."
