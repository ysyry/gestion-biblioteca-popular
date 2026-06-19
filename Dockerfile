# Imagen para deploy en Railway (o cualquier host con Docker).
FROM python:3.12-slim

WORKDIR /app

# Dependencias primero (mejor cacheo)
COPY backend/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Código del backend: app/, static/, sql/, scripts/
COPY backend/ ./

# Carpeta de datos (config de envíos automáticos).
# En Railway: montar un VOLUMEN en /data y setear APP_DATA_DIR=/data para que
# la configuración persista entre redeploys (hasta que migremos a Postgres).
RUN mkdir -p /app/data
ENV APP_DATA_DIR=/app/data

# Railway inyecta $PORT; uvicorn debe escuchar en 0.0.0.0:$PORT.
ENV PORT=8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
