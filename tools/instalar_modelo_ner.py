"""Instalacao do modelo de NER (GLiNER): o UNICO passo do app que usa a rede do Hub, e so quando voce manda.

O app roda sempre offline (``HF_HUB_OFFLINE=1`` por padrao, ver ``local_ner.py``) e carrega o modelo na
revisao fixada. Em maquina nova o modelo ainda nao esta no cache local; baixe-o aqui, uma vez:

    python tools/instalar_modelo_ner.py            # so mostra o que baixaria
    python tools/instalar_modelo_ner.py --baixar   # baixa (pede rede)
    python tools/instalar_modelo_ner.py --baixar --extra gliner-large lener   # detectores da comparacao (H7)

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
# Detectores extras de tools/comparar_motores.py (H7). Sem revisao fixada: sao candidatos em avaliacao, nao o
# motor do app; a revisao que for aprovada entra em local_ner.py como a do GLiNER atual.
EXTRAS = {
    "gliner-large": "urchade/gliner_large-v2.1",
    "lener": "pierreguillou/ner-bert-base-cased-pt-lenerbr",
}


def main(argv: list[str]) -> int:
    extras = []
    if "--extra" in argv:
        extras = [item for item in argv[argv.index("--extra") + 1 :] if not item.startswith("--")]
        unknown = [item for item in extras if item not in EXTRAS]
        if unknown:
            print("Extra desconhecido: " + ", ".join(unknown) + ". Opcoes: " + ", ".join(EXTRAS))
            return 2
    print(f"GLiNER:      {GLINER_MODEL} @ {GLINER_REVISION}")
    print(f"tokenizador: {GLINER_TOKENIZER_MODEL} @ {GLINER_TOKENIZER_REVISION} ({', '.join(_TOKENIZER_FILES)})")
    for name in extras:
        print(f"extra:       {name} = {EXTRAS[name]} (sem revisao fixada; candidato em avaliacao)")
    if "--baixar" not in argv:
        print("Nada foi baixado. Rode de novo com --baixar para baixar (pede rede).")
        return 0
    from huggingface_hub import snapshot_download

    snapshot_download(GLINER_MODEL, revision=GLINER_REVISION)
    snapshot_download(
        GLINER_TOKENIZER_MODEL, revision=GLINER_TOKENIZER_REVISION, allow_patterns=_TOKENIZER_FILES
    )
    for name in extras:
        snapshot_download(EXTRAS[name])
    if "lener" in extras:
        print("O detector 'lener' precisa de: pip install transformers")
    print("Modelo(s) instalado(s) no cache local. O app continua offline.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
