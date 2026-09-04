#!/bin/sh
# Start as root, align the app user with PUID/PGID, own /data, drop privileges.
set -e
PUID="${PUID:-1000}"
PGID="${PGID:-1000}"
groupmod -o -g "$PGID" app
usermod -o -u "$PUID" app
if [ "$(stat -c %u:%g /data)" != "$PUID:$PGID" ]; then
    chown -R app:app /data
fi
exec setpriv --reuid=app --regid=app --init-groups "$@"
