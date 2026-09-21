from __future__ import annotations

import sys
from pathlib import Path

# Los tests importan "src...."/"api...." como si se ejecutaran desde la
# raiz del proyecto (igual que los scripts/test_*.py originales, que
# hacian sys.path.insert(0, ...parent.parent) a mano) -- necesario
# porque pytest no instala el proyecto como paquete.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
