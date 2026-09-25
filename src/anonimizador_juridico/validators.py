from __future__ import annotations

import re


_NON_DIGITS = re.compile(r"\D+")
_NON_CNPJ = re.compile(r"[^0-9A-Z]+")
_OCR_TO_DIGIT = str.maketrans(
    {
        "O": "0",
        "o": "0",
        "I": "1",
        "i": "1",
        "l": "1",
        "L": "1",
        "S": "5",
        "s": "5",
        "B": "8",
        "b": "8",
        "G": "9",
        "g": "9",
        "Q": "9",
        "q": "9",
    }
)
_PIS_WEIGHTS = (3, 2, 9, 8, 7, 6, 5, 4, 3, 2)
_TITULO_UF_MAX = 28


def only_digits(value: str) -> str:
    return _NON_DIGITS.sub("", value)


def map_ocr_digits(value: str) -> str:
    """Converte letras de OCR (O/0, l/1, S/5…) e devolve só dígitos."""

    return only_digits(value.translate(_OCR_TO_DIGIT))


def hamming_distance(left: str, right: str) -> int:
    if len(left) != len(right):
        return max(len(left), len(right))
    return sum(a != b for a, b in zip(left, right))


def at_most_one_digit_edit(left: str, right: str) -> bool:
    """Mesmos dígitos, 1 substituição, 1 inserção ou 1 deleção."""

    if left == right:
        return True
    if abs(len(left) - len(right)) > 1:
        return False
    if len(left) == len(right):
        return hamming_distance(left, right) == 1
    shorter, longer = (left, right) if len(left) < len(right) else (right, left)
    index = cursor = extras = 0
    while index < len(shorter) and cursor < len(longer):
        if shorter[index] == longer[cursor]:
            index += 1
            cursor += 1
            continue
        extras += 1
        if extras > 1:
            return False
        cursor += 1
    extras += len(longer) - cursor
    return extras <= 1


def normalize_cnpj(value: str) -> str:
    return _NON_CNPJ.sub("", value.upper())


def is_valid_cpf(value: str) -> bool:
    digits = only_digits(value)
    if len(digits) != 11 or len(set(digits)) == 1:
        return False

    numbers = [int(char) for char in digits]
    first_sum = sum(number * weight for number, weight in zip(numbers[:9], range(10, 1, -1)))
    first = (first_sum * 10) % 11
    first = 0 if first == 10 else first
    if numbers[9] != first:
        return False

    second_sum = sum(number * weight for number, weight in zip(numbers[:10], range(11, 1, -1)))
    second = (second_sum * 10) % 11
    second = 0 if second == 10 else second
    return numbers[10] == second


def _cnpj_character_value(char: str) -> int:
    # Especificacao RFB: valor ASCII menos 48.
    return ord(char) - 48


def _cnpj_check_digit(characters: str, weights: tuple[int, ...]) -> int:
    total = sum(_cnpj_character_value(char) * weight for char, weight in zip(characters, weights))
    remainder = total % 11
    return 0 if remainder in (0, 1) else 11 - remainder


def is_valid_cnpj(value: str) -> bool:
    normalized = normalize_cnpj(value)
    if len(normalized) != 14:
        return False
    body, check_digits = normalized[:12], normalized[12:]
    if not body.isalnum() or not body.isascii() or not check_digits.isdigit():
        return False
    if any(not (char.isdigit() or "A" <= char <= "Z") for char in body):
        return False
    if len(set(normalized)) == 1:
        return False

    first = _cnpj_check_digit(body, (5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2))
    second = _cnpj_check_digit(body + str(first), (6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2))
    return check_digits == f"{first}{second}"


def is_valid_cnj_number(value: str) -> bool:
    digits = only_digits(value)
    if len(digits) != 20:
        return False

    sequential = digits[:7]
    check_digits = digits[7:9]
    remaining_fields = digits[9:]
    rearranged = sequential + remaining_fields + check_digits
    return int(rearranged) % 97 == 1


def is_valid_pis(value: str) -> bool:
    """PIS/PASEP/NIT: 11 dígitos, pesos 3-2-9-8-7-6-5-4-3-2, módulo 11."""

    digits = only_digits(value)
    if len(digits) != 11 or len(set(digits)) == 1:
        return False
    total = sum(int(digit) * weight for digit, weight in zip(digits[:10], _PIS_WEIGHTS))
    remainder = total % 11
    check = 0 if remainder < 2 else 11 - remainder
    return check == int(digits[10])


def is_valid_titulo_eleitor(value: str) -> bool:
    """Título de eleitor TSE: 12 dígitos (8 sequência + UF 01–28 + 2 DV)."""

    digits = only_digits(value)
    if len(digits) != 12:
        return False
    sequence, uf, first_given, second_given = digits[:8], digits[8:10], int(digits[10]), int(digits[11])
    uf_code = int(uf)
    if uf_code < 1 or uf_code > _TITULO_UF_MAX:
        return False
    first_sum = sum(int(digit) * weight for digit, weight in zip(sequence, range(2, 10)))
    first = 0 if first_sum % 11 == 10 else first_sum % 11
    if first != first_given:
        return False
    second_sum = sum(int(digit) * weight for digit, weight in zip(uf + str(first), (7, 8, 9)))
    second = 0 if second_sum % 11 == 10 else second_sum % 11
    return second == second_given
