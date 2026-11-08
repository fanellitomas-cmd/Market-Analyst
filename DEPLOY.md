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

## Opción gratuita sin tarjeta: Render + Neon + GitHub Actions

Mientras no haya un servidor propio, el sitio se reparte en tres servicios gratuitos que no
piden tarjeta:

| Parte | Dónde | Qué tener en cuenta |
|---|---|---|
| Sitio y API | **Render** (plan Free) | Se duerme tras 15 minutos sin visitas; al entrar tarda 30–60 s en despertar |
| Agente y avisos por WhatsApp | **GitHub Actions** (`.github/workflows/agente.yml`) | Corre cada 15 min; GitHub puede demorar los disparos unos minutos |
| Base de datos | **Neon** (Postgres gratis) | Guarda tesis, corridas del agente y avisos; la comparten Render y GitHub |

Las alertas no dependen de que el sitio esté despierto: las manda GitHub Actions.

### 1. Base de datos en Neon

1. Creá una cuenta en https://neon.tech (podés entrar con tu cuenta de GitHub).
2. Creá un proyecto (región: **AWS São Paulo** si aparece; si no, cualquiera).
3. En el panel, **Connect** → copiá la dirección que empieza con `postgresql://...`
   (termina en `?sslmode=require`). Ésa es tu `DATABASE_URL`: tratala como una contraseña.

Las tablas se crean solas la primera vez que el sitio o el agente se conectan.

### 2. Agente en GitHub Actions

En el repositorio de GitHub: **Settings → Secrets and variables → Actions → New repository
secret**, y cargá:

| Secreto | Valor |
|---|---|
| `DATABASE_URL` | la dirección de Neon |
| `SEC_USER_AGENT` | tu nombre y email, por ejemplo `Juan Perez juan@ejemplo.com` |
| `WHATSAPP_PHONE` | opcional, ver la sección 4 (por ejemplo `+5491112345678`) |
| `CALLMEBOT_APIKEY` | opcional, ver la sección 4 |

Después, en la pestaña **Actions**: si GitHub pregunta, habilitá los workflows; abrí
**Agente → Run workflow** para probarlo. Tiene que terminar en verde y decir "nada pendiente"
o las tareas que ejecutó.

> El workflow sólo corre desde la rama principal (`main`). Si el repositorio pasa 60 días sin
> cambios, GitHub pausa las tareas programadas y avisa por email: se reactivan desde la pestaña
> Actions con un clic.
>
> Si el repositorio es público, cualquiera puede ver los registros de las corridas (no los
> secretos: GitHub los oculta, y el agente no registra el número ni la clave de WhatsApp).

### 3. Sitio en Render

1. Creá una cuenta en https://render.com (con tu cuenta de GitHub) y dale acceso al
   repositorio.
2. **New → Blueprint** → elegí el repositorio. Render lee `render.yaml` y te pide:
   - `DATABASE_URL`: la dirección de Neon.
   - `APP_USERNAME`: el usuario para entrar al sitio.
   - `APP_PASSWORD`: la contraseña (al menos 10 caracteres). Acá va la contraseña en sí: el
     sitio la convierte en hash al arrancar. (Si preferís, podés cargar `APP_PASSWORD_HASH`
     generado con `scripts/hash_password.py` en su lugar.)
   - `SEC_USER_AGENT`, `WHATSAPP_PHONE`, `CALLMEBOT_APIKEY`: los mismos valores que en GitHub
     (los de WhatsApp pueden quedar vacíos).

   `JWT_SECRET` lo genera Render solo.
3. **Apply**. La primera compilación tarda unos minutos. Al terminar, el sitio queda en
   `https://market-analyst-xxxx.onrender.com`.
4. Entrá, iniciá sesión y abrí **Sistema**: el agente figura activo si GitHub Actions corrió en
   la última hora.

Cada vez que se actualiza `main`, Render vuelve a compilar y publicar el sitio solo.

> A diferencia de la instalación con Tailscale, este sitio queda publicado en internet: lo
> protege el login. Usá una contraseña larga y única. Tras 5 intentos fallidos el login se
> bloquea 15 minutos.

---

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
