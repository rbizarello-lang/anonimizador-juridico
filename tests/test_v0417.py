"""v0.4.17: o GLiNER vai para a placa de video quando o torch enxerga CUDA (so tempo; a deteccao nao muda)."""

from __future__ import annotations

import os
import unittest
from unittest import mock

from anonimizador_app import local_ner
from anonimizador_app.version import APP_VERSION


class _FakeModel:
    def __init__(self, fail: bool = False) -> None:
        self.device = "cpu"
        self.fail = fail

    def to(self, device: str) -> "_FakeModel":
        if self.fail:
            raise RuntimeError("memoria de video cheia")
        self.device = device
        return self


class GlinerDeviceTests(unittest.TestCase):
    def test_versao(self) -> None:
        self.assertEqual(APP_VERSION, "0.4.17")

    def test_cpu_forcado_pela_variavel(self) -> None:
        with mock.patch.dict(os.environ, {local_ner.NER_DEVICE_ENV: "cpu"}):
            self.assertEqual(local_ner._to_target_device(_FakeModel()).device, "cpu")

    def test_placa_quando_cuda_existe(self) -> None:
        import torch

        with mock.patch.dict(os.environ, {local_ner.NER_DEVICE_ENV: ""}), \
                mock.patch.object(torch.cuda, "is_available", return_value=True):
            self.assertEqual(local_ner._to_target_device(_FakeModel()).device, "cuda")

    def test_sem_cuda_fica_no_processador(self) -> None:
        import torch

        with mock.patch.dict(os.environ, {local_ner.NER_DEVICE_ENV: ""}), \
                mock.patch.object(torch.cuda, "is_available", return_value=False):
            self.assertEqual(local_ner._to_target_device(_FakeModel()).device, "cpu")

    def test_falha_na_placa_volta_ao_processador(self) -> None:
        import torch

        with mock.patch.dict(os.environ, {local_ner.NER_DEVICE_ENV: ""}), \
                mock.patch.object(torch.cuda, "is_available", return_value=True):
            self.assertEqual(local_ner._to_target_device(_FakeModel(fail=True)).device, "cpu")


if __name__ == "__main__":
    unittest.main()
