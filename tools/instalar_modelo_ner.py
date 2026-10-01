"""Instalacao do modelo de NER (GLiNER): o UNICO passo do app que usa a rede do Hub, e so quando voce manda.

O app roda sempre offline (``HF_HUB_OFFLINE=1`` por padrao, ver ``local_ner.py``) e carrega o modelo na
revisao fixada. Em maquina nova o modelo ainda nao esta no cache local; baixe-o aqui, uma vez:

    python tools/instalar_modelo_ner.py            # so mostra o que baixaria
    python tools/instalar_modelo_ner.py --baixar   # baixa (pede rede)

Baixa: o GLiNER na revisao fixada e o tokenizador mdeberta (so os arquivos de tokenizacao, na revisao
fixada). Nenhum texto de caso e enviado: sao pedidos de download de arquivos publicos do Hub. Sem
``--baixar`` o app cai no spaCy PT (se instalado) e avisa no log.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Ligar o Hub ANTES de importar o local_ner (que o desliga por padrao): aqui a rede e o objetivo.
os.environ["HF_HUB_OFFLINE"] = "0"
os.environ["TRANSFORMERS_OFFLINE"] = "0"

from anonimizador_app.local_ner import (  # noqa: E402
    GLINER_MODEL,
    GLINER_REVISION,
    GLINER_TOKENIZER_MODEL,
    GLINER_TOKENIZER_REVISION,
)

_TOKENIZER_FILES = ["config.json", "spm.model", "tokenizer_config.json"]


def main(argv: list[str]) -> int:
    print(f"GLiNER:      {GLINER_MODEL} @ {GLINER_REVISION}")
    print(f"tokenizador: {GLINER_TOKENIZER_MODEL} @ {GLINER_TOKENIZER_REVISION} ({', '.join(_TOKENIZER_FILES)})")
    if "--baixar" not in argv:
        print("Nada foi baixado. Rode de novo com --baixar para baixar (pede rede).")
        return 0
    from huggingface_hub import snapshot_download

    snapshot_download(GLINER_MODEL, revision=GLINER_REVISION)
    snapshot_download(
        GLINER_TOKENIZER_MODEL, revision=GLINER_TOKENIZER_REVISION, allow_patterns=_TOKENIZER_FILES
    )
    print("Modelo instalado no cache local. O app continua offline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
