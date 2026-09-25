from __future__ import annotations

import unittest

from anonimizador_juridico.validators import (
    is_valid_cpf,
    is_valid_pis,
    is_valid_titulo_eleitor,
    map_ocr_digits,
)


def pis_with_dv(body10: str) -> str:
    weights = (3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
    total = sum(int(digit) * weight for digit, weight in zip(body10, weights))
    remainder = total % 11
    check = 0 if remainder < 2 else 11 - remainder
    return body10 + str(check)


def titulo_with_dv(sequence8: str, uf: str) -> str:
    first_sum = sum(int(digit) * weight for digit, weight in zip(sequence8, range(2, 10)))
    first = 0 if first_sum % 11 == 10 else first_sum % 11
    second_sum = sum(int(digit) * weight for digit, weight in zip(uf + str(first), (7, 8, 9)))
    second = 0 if second_sum % 11 == 10 else second_sum % 11
    return f"{sequence8}{uf}{first}{second}"


class ValidatorTests(unittest.TestCase):
    def test_map_ocr_digits_letter_substitutions(self) -> None:
        self.assertEqual(map_ocr_digits("529.982.247-2S"), "52998224725")
        self.assertEqual(map_ocr_digits("52998224725"), "52998224725")
        self.assertTrue(is_valid_cpf(map_ocr_digits("529.982.247-2S")))

    def test_pis_dv(self) -> None:
        valid = pis_with_dv("1204567890")
        self.assertEqual(len(valid), 11)
        self.assertTrue(is_valid_pis(valid))
        self.assertTrue(is_valid_pis(f"{valid[:3]}.{valid[3:8]}.{valid[8:10]}-{valid[10]}"))
        self.assertFalse(is_valid_pis("11111111111"))
        self.assertFalse(is_valid_pis(valid[:10] + ("0" if valid[10] != "0" else "1")))

    def test_titulo_eleitor_dv(self) -> None:
        valid = titulo_with_dv("11486452", "06")
        self.assertEqual(len(valid), 12)
        self.assertTrue(is_valid_titulo_eleitor(valid))
        self.assertFalse(is_valid_titulo_eleitor("111111111111"))
        self.assertFalse(is_valid_titulo_eleitor("11486452067"))
        self.assertFalse(is_valid_titulo_eleitor(valid[:11] + ("0" if valid[11] != "0" else "1")))


if __name__ == "__main__":
    unittest.main()
