import os

# Evita o problema do TorchInductor no Windows
os.environ["TORCHINDUCTOR_DISABLE"] = "1"
os.environ["TORCH_COMPILE_DISABLE"] = "1"

import torch

torch._dynamo.disable()

if hasattr(torch, "compile"):
    torch.compile = lambda model, *args, **kwargs: model

from pathlib import Path
from docling.document_converter import DocumentConverter


arquivo_pdf = Path("entrada/teste.pdf")
pasta_saida = Path("saida")

pasta_saida.mkdir(exist_ok=True)

if not arquivo_pdf.exists():
    raise FileNotFoundError(
        f"PDF não encontrado: {arquivo_pdf.resolve()}"
    )

print("Iniciando Docling...")
print(f"Arquivo: {arquivo_pdf.resolve()}")

converter = DocumentConverter()
resultado = converter.convert(arquivo_pdf)

print("Conversão concluída.")

# Markdown
markdown = resultado.document.export_to_markdown()

arquivo_md = pasta_saida / "teste.md"

arquivo_md.write_text(
    markdown,
    encoding="utf-8"
)

print(f"Markdown criado: {arquivo_md.resolve()}")

# JSON estrutural
json_documento = resultado.document.model_dump_json(indent=2)

arquivo_json = pasta_saida / "teste.json"

arquivo_json.write_text(
    json_documento,
    encoding="utf-8"
)

print(f"JSON criado: {arquivo_json.resolve()}")

print("Finalizado.")