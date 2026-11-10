#!/bin/sh
set -eu

python /app/scripts/deploy.py bootstrap
exec "$@"
