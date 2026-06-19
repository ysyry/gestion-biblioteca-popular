# Deploy en Railway

App FastAPI (proceso siempre vivo) servida con Docker. Railway corre el `Dockerfile`
de la raíz tal cual. Costo: arranca con US$5 de crédito de prueba; luego plan Hobby
~US$5/mes (incluye US$5 de uso, suficiente para esta app chica).

---

## 1. Subir el código a GitHub

Desde `~/Proyectos/biblioteca-app` (ya tiene `git init` y el primer commit hecho):

```bash
# Creá un repo vacío en https://github.com/new  (ej: biblioteca-app, privado)
git remote add origin https://github.com/<TU_USUARIO>/biblioteca-app.git
git branch -M main
git push -u origin main
```

> El `.gitignore` ya excluye `backend/.env` (los secrets NO se suben).

## 2. Crear el proyecto en Railway

1. Entrá a https://railway.com → **New Project** → **Deploy from GitHub repo**.
2. Elegí el repo. Railway detecta el `Dockerfile` y construye solo.
3. Esperá el primer build (va a fallar/levantar a medias hasta cargar las variables; es normal).

## 3. Cargar las variables de entorno

En el servicio → pestaña **Variables** → pegá estas (los **valores** copialos de tu
`backend/.env` local; acá van solo los nombres y notas):

**Koha (datos reales)**
```
KOHA_BASE_URL=http://3169.bepe.ar:8080
KOHA_USER=<usuario de servicio>
KOHA_PASSWORD=<contraseña de servicio>
```

**IDs de reportes** (ya creados en Koha)
```
REPORT_MEMBER_SEARCH_ID=276
REPORT_MEMBER_LOANS_ID=277
REPORT_LOANS_ACTIVE_ID=278
REPORT_LOANS_OVERDUE_ID=279
REPORT_MEMBER_PROFILE_ID=286
REPORT_MEMBER_ACCOUNT_ID=287
REPORT_MEMBER_HISTORY_ID=288
REPORT_LOANS_CONTACT_ID=289
```

**App**
```
APP_SECRET_KEY=<generá uno largo y aleatorio, NO el de dev>
APP_TOKEN_EXPIRE_MINUTES=480
CORS_ORIGINS=https://<tu-dominio>.up.railway.app
```

**Mails (Gmail)**
```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=<gmail de la biblioteca>
SMTP_PASSWORD=<contraseña de aplicación de Gmail>
SMTP_FROM=<el mismo gmail>
SMTP_FROM_NAME=Biblioteca Popular Osvaldo Bayer
SMTP_USE_TLS=true
MAIL_DRY_RUN=true        # dejalo en true hasta probar; luego false para enviar de verdad
```

**Automáticos / programador**
```
APP_DATA_DIR=/data                          # ver paso 4 (volumen)
APP_TZ=America/Argentina/Buenos_Aires
SCHED_HOUR=9                                 # hora del chequeo diario
```

> Tip: generar un APP_SECRET_KEY → `python -c "import secrets; print(secrets.token_urlsafe(48))"`

## 4. Volumen para que la config persista

La config de los automáticos se guarda en un archivo. Sin volumen, se pierde en cada
redeploy. En el servicio → **Settings → Volumes** → **New Volume**, montalo en **`/data`**.
(Ya pusimos `APP_DATA_DIR=/data` en las variables, así escribe ahí.)

## 5. Dominio y prueba

1. **Settings → Networking → Generate Domain** → te da `https://...up.railway.app`.
2. Abrí esa URL, entrá con tus credenciales de Koha.
3. Actualizá `CORS_ORIGINS` con ese dominio si hizo falta.

---

## Más adelante: base de datos (Postgres)

Cuando quieras pasar la config (y sumar historial de campañas, módulo de pagos, etc.) a
una base de datos:

1. En el proyecto Railway → **New → Database → PostgreSQL** (queda en el mismo proyecto).
2. Railway expone `DATABASE_URL` automáticamente al servicio.
3. Migramos el guardado de `app/auto_mail.py` (hoy archivo JSON) a Postgres. Está aislado
   en `load_config()` / `save_config()`, así que es un cambio chico.

> Alternativa gratis y persistente para la DB: **Neon** (https://neon.tech) → copiás su
> `DATABASE_URL` a las variables de Railway. Útil si querés la DB sin sumar costo.

---

## Notas

- El programador corre **dentro del proceso** (Railway no se duerme), chequea 1 vez por
  día a `SCHED_HOUR` y dispara cada job según su "cada X días".
- Los secrets viven solo en las Variables de Railway, nunca en el repo.
- Para enviar mails de verdad: poné `MAIL_DRY_RUN=false` (o usá el botón "Enviar ahora").
