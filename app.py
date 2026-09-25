from pathlib import Path
import multiprocessing
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))

from anonimizador_app.compat import patch_python314

patch_python314()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    from anonimizador_app.desktop import bootstrap_instance

    bootstrap_instance(ROOT)
    from anonimizador_app.ui import run_app

    run_app()
