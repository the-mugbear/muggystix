#!/bin/sh
# Container entrypoint — self-heal the bind-mounted uploads volume, then drop
# to the unprivileged app user (UID/GID 999) before running the real command.
#
# Why this exists: the app writes to /app/uploads (the admin credential marker
# at first boot, note attachments, and the worker queue/artifact subdirs). That
# path is a host bind mount, and on a fresh deploy the host directory is owned
# by whoever created it (root for a bare `docker compose up`, or the deploy
# user for deploy.sh) — NOT by UID 999. The container can't chown a root-owned
# mount as an unprivileged user, so seed_default_admin()'s marker write used to
# fail and crash-loop the backend, forcing operators to run `chown 999:999
# ./uploads` by hand. Fixing ownership here — as root, at boot, on every deploy
# path — removes that manual step for good.
#
# The application process still runs as 999: this only adds a tiny root-owned
# init step (mkdir/chown/exec) before handing off via gosu. The compose
# `command:` for the worker / report-worker services arrives here as "$@", so
# all three services inherit the same self-heal + privilege drop.
set -e

APP_UID=999
APP_USER=appuser
APP_GROUP=appgroup
UPLOADS=/app/uploads

if [ "$(id -u)" = "0" ]; then
    # Directories the app creates files in. The marker lands in the uploads
    # root; the workers and note-attachment feature use these subdirs.
    mkdir -p \
        "$UPLOADS/ingestion_queue" \
        "$UPLOADS/note_attachments" \
        "$UPLOADS/report_artifacts"

    # Only chown when the root isn't already ours, so steady-state restarts
    # don't pay a recursive chown over a potentially large uploads volume.
    # First boot (or a mount left with wrong ownership) does one recursive
    # pass; it's metadata-only and fast even with many files.
    if [ "$(stat -c '%u' "$UPLOADS" 2>/dev/null || echo -1)" != "$APP_UID" ]; then
        chown -R "$APP_USER:$APP_GROUP" "$UPLOADS" 2>/dev/null \
            || echo "[entrypoint] WARNING: could not chown $UPLOADS to $APP_UID; \
uploads may not be writable (read-only mount?). seed_default_admin will \
fail closed if so." >&2
    fi

    exec gosu "$APP_USER" "$@"
fi

# Already unprivileged (e.g. an explicit `user:` override in compose): the mount
# is assumed pre-provisioned; just run the command as-is.
exec "$@"
