#!/bin/sh
# Startet den Server als normaler Benutzer (PUID/PGID), nicht als root.
# Der Datenordner gehört danach diesem Benutzer – passend zu NAS-Freigaben (z. B. Synology: PUID=1026, PGID=100).
set -e
umask 077          # Datenbank, Schlüssel und Belege nur für den Server-Benutzer lesbar
DATEN="${BUCHHALTUNG_DATEN:-/daten}"
mkdir -p "$DATEN"
if [ "$(id -u)" = "0" ]; then
    if [ "$(stat -c %u:%g "$DATEN")" != "${PUID}:${PGID}" ]; then
        chown -R "${PUID}:${PGID}" "$DATEN"
    fi
    exec setpriv --reuid="${PUID}" --regid="${PGID}" --clear-groups \
        env HOME="$DATEN" python -m app.netzwerk.server "$@"
fi
exec python -m app.netzwerk.server "$@"
