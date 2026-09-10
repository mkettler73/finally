#!/bin/sh
# FinAlly container entrypoint.
#
# Its only job beyond exec'ing the server is to prove, before uvicorn starts,
# that the SQLite directory is writable by the current (non-root) user. A
# container that boots and then fails on the first write is far harder to
# diagnose than one that refuses to start with an explanation.
set -eu

DB_PATH="${FINALLY_DB_PATH:-/app/db/finally.db}"
DB_DIR="$(dirname "$DB_PATH")"

mkdir -p "$DB_DIR" 2>/dev/null || true

if [ ! -w "$DB_DIR" ]; then
    cat >&2 <<EOF
FinAlly: cannot write to the database directory $DB_DIR.

Running as uid $(id -u), gid $(id -g). The mount is owned by
$(ls -ld "$DB_DIR" 2>/dev/null | awk '{print $3":"$4}').

This normally means a host directory was bind-mounted over /app/db and the
host owns it as a different user. Fixes, in order of preference:

  1. Use the named volume the start scripts use:
         docker run -v finally-data:/app/db ...
  2. Or run the container as the host user that owns the directory:
         docker run --user "\$(id -u):\$(id -g)" -v "\$PWD/db:/app/db" ...
  3. Or make the host directory writable:  chmod 0777 ./db
EOF
    exit 1
fi

# Prove writability rather than trusting the permission bits (read-only mounts
# and some volume drivers pass the -w test and still fail on open).
_probe="$DB_DIR/.finally-write-probe.$$"
if ! : > "$_probe" 2>/dev/null; then
    echo "FinAlly: $DB_DIR reports writable but the write failed — is the mount read-only?" >&2
    exit 1
fi
rm -f "$_probe"

exec "$@"
