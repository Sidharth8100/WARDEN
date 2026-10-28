#!/bin/sh
# Credentials are supplied by the human-owned runtime environment, never generated.
set -eu
: "${PGHOST:?set the primary service name}"
: "${PGUSER:?set a dedicated replication role}"
: "${PGPASSWORD:?human must provision the replication password}"
: "${PGDATA:=/var/lib/postgresql/data}"
if [ ! -s "$PGDATA/PG_VERSION" ]; then
    # pg_basebackup refuses a nonempty directory: partial backups are never erased.
    pg_basebackup -D "$PGDATA" -R --wal-method=stream --checkpoint=fast
fi
if [ ! -f "$PGDATA/standby.signal" ]; then
    echo 'Refusing to start a replica without standby.signal' >&2
    exit 1
fi
exec postgres -D "$PGDATA" -c hot_standby=on
