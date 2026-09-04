#!/bin/sh
# As root: align the app user with PUID/PGID, own /data, drop privileges.
# Not root (compose `user:` override): nothing to fix, just run.
set -e
if [ "$(id -u)" != "0" ]; then
    exec "$@"
fi
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
groupmod -o -g "$PGID" app
usermod -o -u "$PUID" app
if [ "$(stat -c %u:%g /data)" != "$PUID:$PGID" ]; then
    chown -R app:app /data
fi
exec setpriv --reuid=app --regid=app --init-groups "$@"
