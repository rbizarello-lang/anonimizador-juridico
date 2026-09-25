"""Importa os Dados Abertos do CNPJ (Receita Federal), recorte RS/SC/PR.

Baixe os ZIPs oficiais (Empresas*.zip e Estabelecimentos*.zip) para uma pasta
e rode:

    .\\.venv\\Scripts\\python.exe tools\\importar_cnpj_sul.py --fonte CAMINHO\\pasta --destino dados\\cnpj_sul.sqlite

Fonte: https://arquivos.receitafederal.gov.br/dados/cnpj/dados_abertos_cnpj/

O download e anual (nao precisa ser mensal). O app nao baixa sozinho.
Devedora sediada fora do Sul fica de fora; use a lista de alvos.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from anonimizador_app.cnpj_index import default_index_path, import_southern_dump


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Gera o indice local de CNPJ da regiao Sul."
    )
    parser.add_argument(
        "--fonte",
        required=True,
        help="Pasta com os ZIPs ou CSVs da Receita (Empresas e Estabelecimentos).",
    )
    parser.add_argument(
        "--destino",
        default=str(default_index_path(ROOT)),
        help="Arquivo SQLite de saida (padrao: dados/cnpj_sul.sqlite).",
    )
    args = parser.parse_args()
    source = Path(args.fonte)
    destination = Path(args.destino)
    print(f"Lendo {source} ...")
    count = import_southern_dump(source, destination)
    print(f"Indice gravado em {destination} ({count} estabelecimentos do Sul).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
