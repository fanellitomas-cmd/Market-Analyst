#!/bin/sh
# Arranca la API (sólo accesible dentro del contenedor) y el sitio en el puerto público.
# Si cualquiera de los dos se cae, el contenedor termina y Render lo reinicia.
set -e
cd /app
uvicorn app.main:app --host 127.0.0.1 --port 8000 &
API=$!
cd /web
HOSTNAME=0.0.0.0 node server.js &
WEB=$!
trap 'kill $API $WEB 2>/dev/null' TERM INT
while kill -0 $API 2>/dev/null && kill -0 $WEB 2>/dev/null; do
  sleep 5
done
kill $API $WEB 2>/dev/null || true
exit 1
