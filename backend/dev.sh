#!/usr/bin/env bash
# Levanta la app en modo desarrollo con hot reload.
# Uso:  cd ~/Proyectos/biblioteca-app/backend && ./dev.sh
# Abrí luego: http://localhost:8000
set -e
cd "$(dirname "$0")"

# Crear venv si no existe e instalar dependencias
if [ ! -d .venv ]; then
  echo "→ Creando entorno virtual…"
  python3 -m venv .venv
  ./.venv/bin/pip install -q -r requirements.txt
fi

source .venv/bin/activate

echo "→ App en http://localhost:8000  (Ctrl+C para frenar)"
echo "  · Backend: se reinicia solo al guardar archivos .py (--reload)"
echo "  · Frontend (static/index.html): refrescá el navegador para ver cambios"
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
