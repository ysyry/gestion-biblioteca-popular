import sys
from pathlib import Path

# Permite importar el paquete `app` al correr pytest desde backend/.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
