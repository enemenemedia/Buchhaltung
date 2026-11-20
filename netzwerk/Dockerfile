# Buchhaltung für KMU – Netzwerkversion (HTTPS-Server für das lokale Netz)
# Bauen (im Hauptordner des Repositorys):  docker build -f netzwerk/Dockerfile -t buchhaltung-kmu:1.4.0 .
FROM python:3.12-slim

ARG VERSION=1.4.0
LABEL org.opencontainers.image.title="Buchhaltung für KMU (Netzwerkversion)" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.source="https://github.com/enemenemedia/Buchhaltung"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BUCHHALTUNG_NETZWERK=1 \
    BUCHHALTUNG_DATEN=/daten \
    BUCHHALTUNG_PORT=8443 \
    TZ=Europe/Berlin \
    PUID=1000 \
    PGID=1000

# tzdata: Zeitstempel im Änderungsprotokoll in deutscher Zeit
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /programm
COPY requirements-netzwerk.txt .
RUN pip install --no-cache-dir -r requirements-netzwerk.txt
COPY app ./app
COPY netzwerk/entrypoint.sh /usr/local/bin/buchhaltung-start
RUN chmod 755 /usr/local/bin/buchhaltung-start && mkdir -p /daten \
 && python -m compileall -q app

VOLUME ["/daten"]
EXPOSE 8443

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
  CMD python -c "import os,ssl,urllib.request as u; c=ssl.create_default_context(); c.check_hostname=False; c.verify_mode=ssl.CERT_NONE; u.urlopen('https://127.0.0.1:%s/system/kennung' % os.environ.get('BUCHHALTUNG_PORT','8443'), context=c, timeout=8)" || exit 1

ENTRYPOINT ["/usr/local/bin/buchhaltung-start"]
