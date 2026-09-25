"""Contorno do TorchInductor no Windows, antes de importar o Docling."""

from __future__ import annotations

import os

_APPLIED = False


def apply_torch_windows_workaround() -> None:
    global _APPLIED
    if _APPLIED:
        return

    os.environ.setdefault("TORCHINDUCTOR_DISABLE", "1")
    os.environ.setdefault("TORCH_COMPILE_DISABLE", "1")

    import torch

    torch._dynamo.disable()
    if hasattr(torch, "compile"):
        torch.compile = lambda model, *args, **kwargs: model  # type: ignore[method-assign]

    _APPLIED = True
