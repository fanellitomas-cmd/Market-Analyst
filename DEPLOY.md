# Instalar Market Analyst (PC o servidor gratuito)

El sitio se compone de tres servicios que levanta Docker Compose:

| Servicio | Qué hace |
|---|---|
| `api` | La API en Python que consulta las fuentes de datos |
| `worker` | El agente 24/7: corre las tareas programadas y manda los avisos por WhatsApp |
| `web` | El sitio (Next.js), en el puerto 3000 de la máquina |

La base de datos (SQLite) queda en la carpeta `data/`. Para hacer un backup, copiá esa carpeta.

---

## 1. Configuración (igual para PC y servidor)

```bash
git clone https://github.com/fanellitomas-cmd/market-analyst.git
cd market-analyst
cp backend/.env.example backend/.env
```

Completá `backend/.env`:

1. **Contraseña del sitio.** Generala con Python (sólo hace falta `pip install bcrypt`):
   ```bash
   python backend/scripts/hash_password.py
   ```
   Pegá en `.env` las dos líneas que imprime (`APP_PASSWORD_HASH` y `JWT_SECRET`).
2. **SEC_USER_AGENT**: tu nombre y email, por ejemplo `"Juan Perez juan@ejemplo.com"`. La SEC
   lo exige para la sección Empresas.
3. **WhatsApp** (opcional, ver la sección 4): `WHATSAPP_PHONE` y `CALLMEBOT_APIKEY`.

## 2. Probarlo en tu PC

Instalá [Docker Desktop](https://www.docker.com/products/docker-desktop/) y, en la carpeta del
proyecto:

```bash
docker compose up -d --build
```

Abrí http://localhost:3000. En **Sistema** vas a ver si el agente está activo, sus tareas y el
botón para mandar un WhatsApp de prueba.

Mientras la PC esté prendida, el agente trabaja. Si se apaga o suspende, las tareas que se
perdieron se ejecutan una vez al volver (si pasaron menos de 15 minutos de la hora programada;
si no, esperan a la próxima).

## 3. Servidor gratuito 24/7 (Oracle Cloud "Always Free")

Oracle ofrece una máquina virtual ARM gratuita sin vencimiento, con recursos de sobra para este
sitio.

1. Creá una cuenta en https://www.oracle.com/cloud/free/. Pide una tarjeta para verificar la
   identidad; no cobra mientras uses sólo recursos marcados como "Always Free".
2. Creá una instancia: **Compute → Instances → Create**, imagen **Ubuntu**, forma
   **VM.Standard.A1.Flex** (Ampere, ARM) con 1 OCPU y 6 GB de RAM. Descargá la clave SSH.
3. Conectate por SSH e instalá Docker:
   ```bash
   curl -fsSL https://get.docker.com | sudo sh
   sudo usermod -aG docker $USER && newgrp docker
   ```
4. Seguí la sección 1 (clonar y completar `.env`) y levantá todo:
   ```bash
   docker compose up -d --build
   ```
5. Los servicios se reinician solos si la máquina se reinicia (`restart: unless-stopped`).

> Oracle puede recuperar instancias "Always Free" que considera inactivas (uso de CPU muy bajo
> durante una semana). Si te pasa, pasar la cuenta a "Pay As You Go" lo evita y sigue sin costo
> mientras te mantengas dentro de los recursos gratuitos.

### Entrar desde el celular, sin exponer el sitio a internet: Tailscale

El sitio sólo escucha en la propia máquina (`127.0.0.1:3000`). Para verlo desde tu celular o tu
PC, usá [Tailscale](https://tailscale.com) (gratis para uso personal), que arma una red privada
entre tus dispositivos:

1. En el servidor:
   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up
   sudo tailscale serve --bg 3000
   ```
   El último comando publica el sitio con HTTPS en `https://<nombre-de-la-máquina>.<tu-red>.ts.net`,
   visible sólo para tus dispositivos.
2. Instalá la app de Tailscale en el celular e iniciá sesión con la misma cuenta.
3. Como ahora el sitio va por HTTPS, poné `COOKIE_SECURE=true` en `backend/.env` y reiniciá:
   `docker compose up -d`.

## 4. WhatsApp con CallMeBot (gratis)

CallMeBot manda mensajes de WhatsApp a tu propio número sin costo.

1. Seguí los pasos de https://www.callmebot.com/blog/free-api-whatsapp-messages/: agregar su
   número a tus contactos y mandarle el mensaje de activación que indica la página.
2. Te responde con tu `apikey`.
3. En `backend/.env`:
   ```
   WHATSAPP_PHONE=+5491112345678
   CALLMEBOT_APIKEY=123456
   ```
4. `docker compose up -d` y, en **Sistema**, tocá **Enviar mensaje de prueba**.

Es un servicio gratuito de un tercero: puede demorar algunos minutos en entregar o tener cortes.
Cada intento queda registrado en **Sistema → Últimos avisos**, con el error si falló. Si algún día
necesitás algo más robusto, la API oficial de WhatsApp (Meta) se puede sumar como otro canal, pero
cobra los mensajes que inicia el sistema.

## 5. Actualizar

```bash
git pull
docker compose up -d --build
```

## Sin Docker (desarrollo)

Ver el README: backend con `uvicorn app.main:app --reload`, agente con `python -m app.worker` y
frontend con `npm run dev`.
