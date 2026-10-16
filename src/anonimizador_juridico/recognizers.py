from __future__ import annotations

import re

from presidio_analyzer import Pattern, PatternRecognizer, RecognizerResult

from .validators import (
    CNPJ_PATTERN,
    is_valid_cnpj,
    is_valid_cnj_number,
    is_valid_cpf,
    is_valid_pis,
    map_ocr_digits,
    normalize_cnpj,
    only_digits,
)

# Presidio PatternRecognizer usa DOTALL|MULTILINE|IGNORECASE.
# `\s` ja casa quebra de linha. Para "mesma linha", usar `[^\n]`.

# º (U+00BA) e lixo OCR de "nº" nao sao sempre \W.
_ORD = r"(?:n[^\s\d]{0,3})?"
_SEP = r"[.,\-\s]"

_CPF_LABEL = rf"(?:C\.?\s*P\.?\s*F\.?(?:\s*/\s*MF)?|CPF\s*/\s*MF)\s*{_ORD}\s*[:;]?\s*"
_CNPJ_LABEL = rf"(?:C\.?\s*N\.?\s*P\.?\s*J\.?(?:\s*/\s*MF)?|CNPJ\s*/\s*MF)\s*{_ORD}\s*[:;]?\s*"
_CEP_LABEL = r"(?:C\.?\s*E\.?\s*P\.?)\s*[:\-]?\s*"
_RG_NUMBER = r"\d{1,4}(?:[.\s]\d{3}){1,2}(?:-[0-9Xx])?(?!\d)"
_RG_LABEL = (
    r"(?:c[eéê]dula\s+de\s+)?identidade(?:\s+civil)?\s*"
    r"(?:r\.?\s{0,3}g\.?\s*)?|"
    r"r\.?\s{0,3}g\.?"
)
_PHONE_LABEL = r"(?:Telefone|WhatsApp|Fone|Tel\.?|Cel\.?)"
_PHONE_LABEL_PREFIX = re.compile(
    rf"(?i)^{_PHONE_LABEL}(?![A-Za-z])[^\n\d(]*"
)
_PIS_LABEL = r"(?:PIS(?:\s*/\s*PASEP)?|PASEP|NIT)"
# Vao permitido entre os grupos do numero CNJ: no maximo UMA quebra de linha (e espacos ao redor),
# como sai de um PDF que partiu o numero no fim da linha ("5048714-\n11.2025.4.04.7200").
_CNJ_GAP = r"[ \t]*\n?[ \t]*"
_CNJ_FORMATADO = (
    rf"\d{{7}}-{_CNJ_GAP}\d{{2}}\.{_CNJ_GAP}\d{{4}}\.{_CNJ_GAP}\d\.{_CNJ_GAP}\d{{2}}\.{_CNJ_GAP}\d{{4}}"
)
# NIRE: 11 digitos, com no maximo um espaco ou ponto entre dois digitos ("42 2 0123456 7").
_NIRE_VALOR = r"\d(?:[ .\-]?\d){10}"
# Parametro de chave de assinatura/consulta numa URL (chave1=, codigo=, key=, hash=, token=...).
_URL_CHAVE_PARAM = re.compile(
    r"(?i)[?&;][A-Za-z_]*(?:chave|key|codigo|token|hash|verific|assinatura|signature|senha)"
    r"[A-Za-z0-9_]*="
)
_GLUED_EMAIL = re.compile(
    r"[A-Za-z0-9._%+\-]+@(?:[A-Za-z0-9-]+\.)+[a-z]{2,24}(?=[A-ZÁÀÂÃÉÊÍÓÔÕÚÇ])"
)
_GLUED_TLD_TAIL = re.compile(r"^([a-z]{2,24})([A-ZÁÀÂÃÉÊÍÓÔÕÚÇ].+)$")
_OCR_CPF_CHAR = r"[\dOoIlSBgq]"
_UF_ALT = (
    "AC|AL|AP|AM|BA|CE|DF|ES|GO|MA|MT|MS|MG|PA|PB|PR|PE|PI|"
    "RJ|RN|RS|RO|RR|SC|SP|SE|TO"
)


def _trim_glued_email_tld(value: str) -> str | None:
    at = value.rfind("@")
    if at < 0:
        return None
    parts = value[at + 1 :].split(".")
    if not parts:
        return None
    glued = _GLUED_TLD_TAIL.match(parts[-1])
    if glued is None:
        return None
    parts[-1] = glued.group(1)
    return value[: at + 1] + ".".join(parts)


def _strip_cnpj_prefix_letters(compact: str) -> str:
    stripped = compact
    while stripped.startswith("CNPJ") or stripped.startswith("MF"):
        stripped = stripped[4:] if stripped.startswith("CNPJ") else stripped[2:]
    return stripped


def _labeled_cpf_ok(pattern_text: str) -> bool:
    digits = only_digits(pattern_text)
    return is_valid_cpf(digits) or 9 <= len(digits) <= 11


def _labeled_cnpj_ok(pattern_text: str) -> bool:
    compact = _strip_cnpj_prefix_letters(normalize_cnpj(pattern_text))
    digits = only_digits(pattern_text)
    return is_valid_cnpj(compact) or 12 <= len(digits) <= 14 or 12 <= len(compact) <= 14


class ValueGroupPatternRecognizer(PatternRecognizer):
    """Recorta o span ao grupo nomeado `valor`: o rótulo ("CEP:", "Agência:", "Matrícula nº")
    fica fora do token e o valor inteiro fica dentro (fila 3.x, E11).

    O padrão é reaplicado na posição do achado, no texto inteiro (não num recorte): o que ele
    exige antes e depois do achado (``(?<!\\d)``, ``(?=...SSP)``) continua valendo.
    """

    def analyze(self, text, entities, nlp_artifacts=None, regex_flags=None):
        results = super().analyze(text, entities, nlp_artifacts, regex_flags)
        by_name = {pattern.name: pattern for pattern in self.patterns}
        flags = regex_flags
        if flags is None:
            flags = getattr(
                self,
                "global_regex_flags",
                re.IGNORECASE | re.DOTALL | re.MULTILINE,
            )
        trimmed = []
        for result in results:
            explanation = result.analysis_explanation
            name = explanation.pattern_name if explanation is not None else ""
            pattern = by_name.get(name)
            if pattern is None or "(?P<valor>" not in pattern.regex:
                trimmed.append(result)
                continue
            match = re.compile(pattern.regex, flags).match(text, result.start)
            if match is None or match.group("valor") is None:
                trimmed.append(result)
                continue
            result.start, result.end = match.span("valor")
            trimmed.append(result)
        return trimmed


class BrCpfRecognizer(PatternRecognizer):
    def __init__(self) -> None:
        super().__init__(
            supported_entity="BR_CPF",
            supported_language="pt",
            name="BrCpfRecognizer",
            patterns=[
                Pattern(
                    name="cpf_formatado_ou_compacto",
                    regex=r"(?<!\d)\d{3}[.,\s]?\d{3}[.,\s]?\d{3}[.\-\s]?\d{2}(?!\d)",
                    score=0.75,
                )
            ],
            context=["cpf", "cadastro de pessoa fisica"],
        )

    def validate_result(self, pattern_text: str) -> bool:
        return is_valid_cpf(pattern_text)


class LabeledCpfFallbackRecognizer(ValueGroupPatternRecognizer):
    """CPF com rotulo, tolerante a OCR. Sem rotulo nao entra (P10). O rotulo fica fora do token.

    Aceita o DV na notacao antiga ("C.P.F. 123456789/01"): sem ele o token parava antes
    da barra e o DV sobrava colado ("[CPF_n]/01").
    """

    def __init__(self) -> None:
        super().__init__(
            supported_entity="BR_CPF",
            supported_language="pt",
            name="LabeledCpfFallbackRecognizer",
            patterns=[
                Pattern(
                    name="cpf_rotulado_ocr",
                    regex=(
                        rf"\b{_CPF_LABEL}"
                        rf"(?P<valor>(?:\d{_SEP}*){{8,13}}\d(?:/\d{{2}})?)(?!\d)"
                    ),
                    score=0.55,
                )
            ],
        )

    def invalidate_result(self, pattern_text: str) -> bool | None:
        return None if _labeled_cpf_ok(pattern_text) else True


class BrCpfOcrRecognizer(PatternRecognizer):
    """CPF pontuado com letra de OCR (O/0, l/1). Sem 11 crus."""

    def __init__(self) -> None:
        super().__init__(
            supported_entity="BR_CPF",
            supported_language="pt",
            name="BrCpfOcrRecognizer",
            patterns=[
                Pattern(
                    name="cpf_formatado_letra_ocr",
                    regex=(
                        rf"(?<!\d){_OCR_CPF_CHAR}{{3}}[.,\s]"
                        rf"{_OCR_CPF_CHAR}{{3}}[.,\s]"
                        rf"{_OCR_CPF_CHAR}{{3}}[.\-\s]"
                        rf"{_OCR_CPF_CHAR}{{2}}(?!\d)"
                    ),
                    score=0.7,
                )
            ],
            context=["cpf", "cadastro de pessoa fisica"],
        )

    def validate_result(self, pattern_text: str) -> bool:
        mapped = map_ocr_digits(pattern_text)
        return mapped != only_digits(pattern_text) and is_valid_cpf(mapped)


class BrCnpjRecognizer(PatternRecognizer):
    def __init__(self) -> None:
        super().__init__(
            supported_entity="BR_CNPJ",
            supported_language="pt",
            name="BrCnpjRecognizer",
            patterns=[
                Pattern(
                    name="cnpj_numerico_ou_alfanumerico",
                    regex=CNPJ_PATTERN,
                    score=0.75,
                )
            ],
            context=["cnpj", "cadastro nacional da pessoa juridica"],
        )

    def validate_result(self, pattern_text: str) -> bool:
        return is_valid_cnpj(pattern_text)


class LabeledCnpjFallbackRecognizer(ValueGroupPatternRecognizer):
    def __init__(self) -> None:
        super().__init__(
            supported_entity="BR_CNPJ",
            supported_language="pt",
            name="LabeledCnpjFallbackRecognizer",
            patterns=[
                Pattern(
                    name="cnpj_rotulado_ocr",
                    regex=(
                        rf"\b{_CNPJ_LABEL}(?P<valor>"
                        rf"[0-9A-Z]{{2}}{_SEP}*[0-9A-Z]{{3}}{_SEP}*[0-9A-Z]{{3}}"
                        rf"[/\s]+[0-9A-Z]{{4}}(?:[-\s]*\d{{1,2}})?)(?![0-9A-Z])"
                    ),
                    score=0.55,
                )
            ],
        )

    def invalidate_result(self, pattern_text: str) -> bool | None:
        return None if _labeled_cnpj_ok(pattern_text) else True


class BrProcessNumberRecognizer(PatternRecognizer):
    def __init__(self) -> None:
        super().__init__(
            supported_entity="BR_PROCESSO",
            supported_language="pt",
            name="BrProcessNumberRecognizer",
            patterns=[
                Pattern(
                    name="numero_cnj_formatado_ou_compacto",
                    regex=rf"(?<!\d)(?:{_CNJ_FORMATADO}|\d{{20}})(?!\d)",
                    score=0.85,
                )
            ],
            context=["processo", "autos", "numero unico"],
        )

    def validate_result(self, pattern_text: str) -> bool:
        return is_valid_cnj_number(pattern_text)


class BrRgRecognizer(ValueGroupPatternRecognizer):
    def __init__(self) -> None:
        super().__init__(
            supported_entity="BR_RG",
            supported_language="pt",
            name="BrRgRecognizer",
            patterns=[
                Pattern(
                    name="rg_com_rotulo",
                    regex=rf"\b(?:{_RG_LABEL})\s*{_ORD}\s*[:;]?\s*(?P<valor>{_RG_NUMBER})",
                    score=0.8,
                ),
                Pattern(
                    name="rc_ocr_com_ssp",
                    regex=(
                        rf"\bRC\.?\s+{_ORD}\s*(?P<valor>{_RG_NUMBER})"
                        rf"(?=[\s.\-–,;:]{{0,8}}.{{0,30}}(?:SSP|SESP))"
                    ),
                    score=0.75,
                ),
                Pattern(
                    # RG de Santa Catarina: "8/R.1.964.808" e a variante "8/C.1.234.567" (digito, barra, R ou C, grupos
                    # pontuados).
                    name="rg_sc_barra_r",
                    regex=r"(?<![\w/.])(?P<valor>\d{1,2}/[RC]\.\d{1,3}(?:\.\d{3}){1,2})(?!\d)",
                    score=0.8,
                ),
            ],
        )


class BrNireRecognizer(ValueGroupPatternRecognizer):
    """NIRE (registro na Junta Comercial): 11 digitos depois do rotulo "NIRE". Sem DV padronizado."""

    def __init__(self) -> None:
        super().__init__(
            supported_entity="BR_NIRE",
            supported_language="pt",
            name="BrNireRecognizer",
            patterns=[
                Pattern(
                    name="nire_rotulado",
                    regex=(
                        rf"\bNIRE\s*(?:N[.\s]*[Oº°]?\s*)?[:;]?\s*(?<!\d)(?P<valor>{_NIRE_VALOR})(?!\d)"
                    ),
                    score=0.85,
                )
            ],
        )


_JUNTA_REGISTRO = (
    r"(?<!\d)(?P<valor>\d{3}\.\d{3}\.\d{3}-\d{2})(?![\d-])"
)
# Os mesmos 11 digitos sem pontuacao (so depois de JUCESC / Junta Comercial); CPF valido nao entra.
_JUNTA_REGISTRO_PURO = r"(?<!\d)(?P<valor>\d{11})(?![\d]|-\d)"


class BrJuntaRegistroRecognizer(ValueGroupPatternRecognizer):
    """Registro da Junta Comercial com 11 digitos escritos como CPF ("123.456.789-01"), depois de "JUCESC", "Junta
    Comercial" ou "registro nº". E o NIRE (a Junta o numera com 11 digitos), so que sem o rotulo "NIRE": entra na
    mesma categoria ``BR_NIRE`` (token ``[NIRE_n]``), com os mesmos digitos como chave, para a mesma inscricao ter um
    token so em qualquer formato. O rotulo fica fora do token. Depois de "JUCESC" ou "Junta Comercial" (ate 40 letras
    de vao, sem quebra de linha nem digito) vale o formato; depois de "registro nº" sozinho so vale se o numero NAO
    fecha como CPF (digito verificador errado): se fecha, e CPF e segue o reconhecedor de CPF."""

    _SOLO = "registro_n_formato_cpf"
    _PURO = "jucesc_registro_11_digitos"

    def __init__(self) -> None:
        super().__init__(
            supported_entity="BR_NIRE",
            supported_language="pt",
            name="BrJuntaRegistroRecognizer",
            patterns=[
                Pattern(
                    name="jucesc_registro_formato_cpf",
                    regex=(
                        r"\b(?:JUCESC|Junta\s+Comercial)(?![A-Za-z])"
                        r"(?:(?!cpf|cnpj|c\.\s*p\.\s*f|\brg\b|identidade)[^\n\d]){0,40}?"
                        r"(?:registro\s*)?(?:n[.\s]*[oº°]?\s*)?[:;]?\s*" + _JUNTA_REGISTRO
                    ),
                    score=0.85,
                ),
                Pattern(
                    name=self._PURO,
                    regex=(
                        r"\b(?:JUCESC|Junta\s+Comercial)(?![A-Za-z])"
                        r"(?:(?!cpf|cnpj|c\.\s*p\.\s*f|\brg\b|identidade)[^\n\d]){0,40}?"
                        r"(?:registro\s*)?(?:n[.\s]*[oº°]?\s*)?[:;]?\s*" + _JUNTA_REGISTRO_PURO
                    ),
                    score=0.85,
                ),
                Pattern(
                    name=self._SOLO,
                    regex=r"\bregistro\s+n[.\s]*[oº°]?\s*[:;]?\s*" + _JUNTA_REGISTRO,
                    score=0.8,
                ),
            ],
        )

    def analyze(self, text, entities, nlp_artifacts=None, regex_flags=None):
        results = super().analyze(text, entities, nlp_artifacts, regex_flags)
        kept = []
        for result in results:
            explanation = result.analysis_explanation
            name = explanation.pattern_name if explanation is not None else ""
            if name in (self._SOLO, self._PURO) and is_valid_cpf(text[result.start : result.end]):
                continue
            kept.append(result)
        return kept


class BrCnhRecognizer(ValueGroupPatternRecognizer):
    """CNH: 11 digitos depois de "CNH" ou "Carteira Nacional de Habilitacao". Sem DV conferido."""

    def __init__(self) -> None:
        super().__init__(
            supported_entity="BR_CNH",
            supported_language="pt",
            name="BrCnhRecognizer",
            patterns=[
                Pattern(
                    name="cnh_rotulada",
                    regex=(
                        r"\b(?:CNH|Carteira\s+Nacional\s+de\s+Habilita[cç][aã]o(?:\s*\(CNH\))?)"
                        r"\s*(?:N[.\s]*[Oº°]?\s*)?[:;]?\s*(?:de\s+registro\s*)?"
                        r"(?<!\d)(?P<valor>\d{11})(?![\d.,]?\d)"
                    ),
                    score=0.85,
                )
            ],
        )


class BrUrlChaveRecognizer(PatternRecognizer):
    """URL de assinatura/consulta com chave na query (chave1=, codigo=, hash=...). A URL inteira vira
    um token; URL institucional sem parametro de chave ("https://www.trf4.jus.br") fica em claro."""

    def __init__(self) -> None:
        super().__init__(
            supported_entity="BR_URL",
            supported_language="pt",
            name="BrUrlChaveRecognizer",
            patterns=[
                Pattern(
                    name="url_com_parametro_de_chave",
                    regex=(
                        r"(?<![\w/@.-])(?:https?://|www\.)[^\s<>\"'\[\]()]*\?[^\s<>\"'\[\]()]*"
                        r"[^\s<>\"'\[\]().,;:!]"
                    ),
                    score=0.85,
                )
            ],
        )

    def validate_result(self, pattern_text: str) -> bool:
        return _URL_CHAVE_PARAM.search(pattern_text) is not None


class BrPisRecognizer(ValueGroupPatternRecognizer):
    """PIS/PASEP/NIT só com rótulo e dígito verificador (P10)."""

    def __init__(self) -> None:
        super().__init__(
            supported_entity="BR_PIS",
            supported_language="pt",
            name="BrPisRecognizer",
            patterns=[
                Pattern(
                    name="pis_rotulado",
                    regex=(
                        rf"\b{_PIS_LABEL}\s*{_ORD}\s*[:;]?\s*"
                        rf"(?P<valor>\d{{3}}\.?\d{{5}}\.?\d{{2}}-?\d)(?!\d)"
                    ),
                    score=0.85,
                )
            ],
            context=["pis", "pasep", "nit"],
        )

    def validate_result(self, pattern_text: str) -> bool:
        return is_valid_pis(pattern_text)


class LocalEmailRecognizer(PatternRecognizer):
    """E-mail local; TLD colado em maiúscula (OCR) entra por regex case-sensitive."""

    def __init__(self) -> None:
        super().__init__(
            supported_entity="EMAIL_ADDRESS",
            supported_language="pt",
            name="LocalEmailRecognizer",
            patterns=[
                Pattern(
                    name="email_local_sem_cache_tld",
                    regex=r"\b[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,63}\b",
                    score=0.9,
                )
            ],
        )

    def analyze(self, text, entities, nlp_artifacts=None, regex_flags=None):
        results = super().analyze(text, entities, nlp_artifacts, regex_flags)
        for result in results:
            trimmed = _trim_glued_email_tld(text[result.start : result.end])
            if trimmed is not None:
                result.end = result.start + len(trimmed)
        for match in _GLUED_EMAIL.finditer(text):
            if any(item.start < match.end() and match.start() < item.end for item in results):
                continue
            results.append(
                RecognizerResult(
                    entity_type="EMAIL_ADDRESS",
                    start=match.start(),
                    end=match.end(),
                    score=0.85,
                )
            )
        results.extend(self._split_pairs(text, results))
        return results

    @staticmethod
    def _split_pairs(text: str, taken: list) -> list:
        """E-mail partido entre celulas (H9-h): a parte local e o dominio viram dois achados com a MESMA chave
        (o endereco inteiro), ou seja, dois tokens iguais; a barra da tabela e a quebra de linha ficam de fora."""

        pairs: list[RecognizerResult] = []
        for match in _SPLIT_EMAIL.finditer(text):
            if not _SPLIT_GAP_MARK.search(match.group("gap")):
                continue
            if any(item.start < match.end() and match.start() < item.end for item in taken):
                continue
            if sum(char.isalpha() for char in match.group("local")) < 3:
                continue
            canonical = (match.group("local") + match.group("domain")).casefold()
            for group in ("local", "domain"):
                start, end = match.span(group)
                pairs.append(
                    RecognizerResult(
                        entity_type="EMAIL_ADDRESS",
                        start=start,
                        end=end,
                        score=0.85,
                        recognition_metadata={"canonical_key": canonical},
                    )
                )
        return pairs


# E-mail partido entre duas celulas de tabela ou duas linhas ("fulano.silva |" + quebra + "| @provedor.com"): parte
# local, vao so de espaco, barra de tabela e uma quebra de linha, e o dominio comecando em "@". O vao tem de ter
# barra ou quebra: espaco simples entre a palavra e o "@" nao e e-mail partido.
_SPLIT_EMAIL = re.compile(
    r"(?<![A-Za-z0-9._%+\-@])(?P<local>[A-Za-z0-9][A-Za-z0-9._%+\-]{1,63})"
    r"(?P<gap>[ \t|]*(?:\r?\n[ \t|]*)?)"
    r"(?P<domain>@[A-Za-z0-9.\-]+\.[A-Za-z]{2,24})\b"
)
_SPLIT_GAP_MARK = re.compile(r"[|\n]")
_MATRICULA_NUMERO = re.compile(r"\d{1,7}(?:\.\d{1,3})?")


class BrMatriculaImovelRecognizer(ValueGroupPatternRecognizer):
    """Matrícula rotulada, inclusive lista 'matrículas nº X; nº Y'.

    O rótulo ("Matrícula nº") fica fora do token; na lista, cada número é um token (a mesma
    matrícula citada sozinha em outro ponto cai no mesmo token).
    """

    def __init__(self) -> None:
        super().__init__(
            supported_entity="BR_MATRICULA_IMOVEL",
            supported_language="pt",
            name="BrRealEstateRegistrationRecognizer",
            patterns=[
                Pattern(
                    name="matricula_imovel_com_rotulo",
                    regex=(
                        r"\bMATR[ÍI]CULA\s*(?:N[.\s]*[Oº°]?\s*)?"
                        r"(?P<valor>\d{1,7}(?:\.\d{1,3})?)\b"
                    ),
                    score=0.85,
                ),
                Pattern(
                    name="matriculas_lista_rotulada",
                    regex=(
                        r"\bMATR[ÍI]CULAS?\s+"
                        r"(?:N[.\s]*[Oº°]?\s*)?"
                        r"(?P<valor>\d{1,7}(?:\.\d{1,3})?"
                        r"(?:\s*[;:,]\s*(?:N[.\s]*[Oº°]?\s*)?\d{1,7}(?:\.\d{1,3})?)+)"
                    ),
                    score=0.85,
                ),
            ],
        )

    def analyze(self, text, entities, nlp_artifacts=None, regex_flags=None):
        results = super().analyze(text, entities, nlp_artifacts, regex_flags)
        split: list[RecognizerResult] = []
        for result in results:
            value = text[result.start : result.end]
            numbers = list(_MATRICULA_NUMERO.finditer(value))
            if len(numbers) < 2 or not re.search(r"[;:,]", value):
                split.append(result)
                continue
            for number in numbers:
                split.append(
                    RecognizerResult(
                        entity_type=result.entity_type,
                        start=result.start + number.start(),
                        end=result.start + number.end(),
                        score=result.score,
                        analysis_explanation=result.analysis_explanation,
                    )
                )
        return split


class BrBankAccountRecognizer(ValueGroupPatternRecognizer):
    """Agência, conta, Debcad/SIDA e conta SISPAR rotulados: o rótulo fica fora do token e o valor
    inteiro fica dentro ("Banco/Agência: 237/1234-5" -> "Banco/Agência: [CONTA_n]").

    "Agência 1234, Conta 56789-0" vira dois tokens (um por valor); o rótulo "Conta" entre eles
    não entra em nenhum.
    """

    _AGENCIA_E_CONTA = "agencia_com_conta_rotulada"

    def analyze(self, text, entities, nlp_artifacts=None, regex_flags=None):
        results = super().analyze(text, entities, nlp_artifacts, regex_flags)
        pattern = next((p for p in self.patterns if p.name == self._AGENCIA_E_CONTA), None)
        if pattern is None:
            return results
        flags = regex_flags
        if flags is None:
            flags = getattr(
                self, "global_regex_flags", re.IGNORECASE | re.DOTALL | re.MULTILINE
            )
        compiled = re.compile(pattern.regex, flags)
        out: list[RecognizerResult] = []
        for result in results:
            explanation = result.analysis_explanation
            if explanation is None or explanation.pattern_name != self._AGENCIA_E_CONTA:
                out.append(result)
                continue
            match = compiled.match(text, result.start)
            if match is None:
                out.append(result)
                continue
            for group in ("agencia", "conta"):
                start, end = match.span(group)
                out.append(
                    RecognizerResult(
                        entity_type=result.entity_type,
                        start=start,
                        end=end,
                        score=result.score,
                        analysis_explanation=explanation,
                    )
                )
        return out


class BrCepRecognizer(ValueGroupPatternRecognizer):
    def __init__(self) -> None:
        super().__init__(
            supported_entity="BR_CEP",
            supported_language="pt",
            name="BrCepRecognizer",
            patterns=[
                Pattern(
                    name="cep_com_rotulo",
                    regex=rf"\b{_CEP_LABEL}(?P<valor>\d{{2}}\.?\d{{3}}-?\s*\d{{3}}\d?)(?!\d)",
                    score=0.85,
                )
            ],
        )


class BrCepTabelaRecognizer(ValueGroupPatternRecognizer):
    """CEP de 8 dígitos colado, sem rótulo e sem hífen, em tabela de endereços: "BAIRRO 12345678
    CIDADE SC" (a coluna do CEP, a da cidade e a da UF, uma depois da outra).

    Só entra com a cidade em maiúsculas e a UF no fim da linha ou da célula: o mesmo número
    em código, protocolo ou data ("PORTARIA N 12345678 DA DF DA SJSC") não casa. Sem
    IGNORECASE: a UF e a cidade são maiúsculas.
    """

    def __init__(self) -> None:
        super().__init__(
            supported_entity="BR_CEP",
            supported_language="pt",
            name="BrCepTabelaRecognizer",
            patterns=[
                Pattern(
                    name="cep_8_digitos_cidade_uf",
                    regex=(
                        r"(?<![\d.\-/,])(?P<valor>\d{8})(?![\d.\-/,])"
                        r"[ \t]+(?:[A-ZÀ-Ý][A-ZÀ-Ý'.]*[ \t]+){1,4}?"
                        rf"(?:{_UF_ALT})[ \t]*(?=\n|\||$)"
                    ),
                    score=0.8,
                )
            ],
            global_regex_flags=re.MULTILINE | re.DOTALL,
        )


class BrCepUfRecognizer(PatternRecognizer):
    """CEP precedido de sigla de UF (rótulo contextual). Sem IGNORECASE: evita 'to'/'am'."""

    def __init__(self) -> None:
        super().__init__(
            supported_entity="BR_CEP",
            supported_language="pt",
            name="BrCepUfRecognizer",
            patterns=[
                Pattern(
                    name="cep_apos_uf",
                    regex=(
                        rf"(?<![A-Z])(?:{_UF_ALT})\b"
                        r"[,\s\-–]{1,4}"
                        r"\d{2}\.?\d{3}[-\s]*\d{3}(?!\d)"
                    ),
                    score=0.82,
                )
            ],
            global_regex_flags=re.MULTILINE | re.DOTALL,
        )

    def analyze(self, text, entities, nlp_artifacts=None, regex_flags=None):
        results = super().analyze(text, entities, nlp_artifacts, regex_flags)
        body = re.compile(r"\d{2}\.?\d{3}[-\s]*\d{3}(?!\d)")
        for result in results:
            snippet = text[result.start : result.end]
            match = body.search(snippet)
            if match:
                result.start += match.start()
                result.end = result.start + (match.end() - match.start())
        return results


class BrPhoneRecognizer(PatternRecognizer):
    def __init__(self) -> None:
        super().__init__(
            supported_entity="BR_PHONE",
            supported_language="pt",
            name="BrPhoneRecognizer",
            patterns=[
                Pattern(
                    name="telefone_br",
                    regex=(
                        r"(?<!\d)(?:\+?55[\s.\-]?)?"
                        r"(?:\([1-9]\d\)|[1-9]\d)[\s.\-]?"
                        r"(?:9\d{4}|[2-5]\d{3})[\s.\-]?\d{4}(?!\d)"
                    ),
                    score=0.8,
                ),
                Pattern(
                    # Celular com o nono digito separado por espaco: "47 9 8473-5213".
                    name="telefone_nono_digito_separado",
                    regex=(
                        r"(?<!\d)(?:\+?55[\s.\-]?)?"
                        r"(?:\([1-9]\d\)|[1-9]\d)[\s.\-]?"
                        r"9[ \t]\d{4}[\s.\-]?\d{4}(?!\d)"
                    ),
                    score=0.8,
                ),
                Pattern(
                    name="telefone_ddd_parenteses",
                    regex=(
                        r"(?<!\d)(?:\+?55[\s.\-]?)?"
                        r"\([1-9]\d\)[\s.\-]*"
                        r"(?:\d[\s.\-]*){6,8}\d(?!\d)"
                    ),
                    score=0.8,
                ),
                Pattern(
                    name="telefone_rotulado_7_a_9_locais",
                    regex=(
                        rf"\b{_PHONE_LABEL}(?![A-Za-z])[^\n\d(]{{0,20}}?"
                        r"(?:\+?55[\s.\-]?)?"
                        r"(?:\([1-9]\d\)|[1-9]\d)[\s.\-]?"
                        r"\d{3,5}[\s.\-]?\d{4}(?!\d)"
                    ),
                    score=0.85,
                ),
            ],
            context=["telefone", "fone", "celular", "contato"],
        )

    def analyze(self, text, entities, nlp_artifacts=None, regex_flags=None):
        results = super().analyze(text, entities, nlp_artifacts, regex_flags)
        trimmed = []
        for result in results:
            matched = text[result.start : result.end]
            prefix = _PHONE_LABEL_PREFIX.match(matched)
            if prefix and result.start + prefix.end() < result.end:
                result.start = result.start + prefix.end()
            trimmed.append(result)
        return trimmed


def build_structured_recognizers() -> list[PatternRecognizer]:
    """Cria somente reconhecedores que independem de NLP ou acesso externo."""

    return [
        BrCpfRecognizer(),
        BrCpfOcrRecognizer(),
        BrCnpjRecognizer(),
        BrProcessNumberRecognizer(),
        LabeledCpfFallbackRecognizer(),
        LabeledCnpjFallbackRecognizer(),
        ValueGroupPatternRecognizer(
            supported_entity="BR_PROCESSO",
            supported_language="pt",
            name="LabeledProcessFallbackRecognizer",
            patterns=[
                Pattern(
                    name="processo_rotulado_sem_validacao",
                    regex=(
                        r"\b(?:PROCESSO|AUTOS)\s*(?:N[.\s]*[Oº°]?\s*)?:?\s*"
                        rf"(?P<valor>{_CNJ_FORMATADO}|\d{{20}})(?!\d)"
                    ),
                    score=0.55,
                )
            ],
        ),
        LocalEmailRecognizer(),
        BrPhoneRecognizer(),
        ValueGroupPatternRecognizer(
            supported_entity="BR_OAB",
            supported_language="pt",
            name="BrOabRecognizer",
            patterns=[
                Pattern(
                    name="oab_uf_antes",
                    regex=(
                        r"(?i)\bOAB\s*[/\-]?\s*(?P<valor>[A-Z]{2}\s*"
                        r"(?:n[.\s]*[oº°]?\s*)?:?-?\s*"
                        r"\d{1,7}(?:\.\d{3})?(?:-[A-Z])?)\b"
                    ),
                    score=0.9,
                ),
                Pattern(
                    name="oab_uf_depois",
                    regex=(
                        r"(?i)\bOAB\s+(?P<valor>\d{1,7}(?:\.\d{3})?(?:-[A-Z])?"
                        r"\s*[/\-]\s*[A-Z]{2})\b"
                    ),
                    score=0.9,
                ),
                Pattern(
                    name="oab_prefixo_n",
                    regex=(
                        r"(?i)\bN\.?\s*OAB\s*[/\-]?\s*"
                        r"(?P<valor>\d{1,7}(?:\.\d{3})?(?:-[A-Z])?)\b"
                    ),
                    score=0.9,
                ),
            ],
        ),
        BrRgRecognizer(),
        BrNireRecognizer(),
        BrJuntaRegistroRecognizer(),
        BrCnhRecognizer(),
        BrUrlChaveRecognizer(),
        BrPisRecognizer(),
        BrMatriculaImovelRecognizer(),
        BrCepRecognizer(),
        BrCepUfRecognizer(),
        BrCepTabelaRecognizer(),
        ValueGroupPatternRecognizer(
            supported_entity="BR_PIX",
            supported_language="pt",
            name="BrPixRecognizer",
            patterns=[
                Pattern(
                    name="pix_rotulado_evp_uuid",
                    regex=(
                        r"\b(?:CHAVE\s+(?:PIX|ALEAT[ÓO]RIA)|PIX(?:\s+CHAVE)?)"
                        r"\s*(?:N[.\s]*[Oº°]?\s*)?:?\s*"
                        r"(?P<valor>[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}"
                        r"-[0-9A-F]{12}|[0-9A-F]{32})\b"
                    ),
                    score=0.85,
                )
            ],
        ),
        BrBankAccountRecognizer(
            supported_entity="BR_CONTA_BANCARIA",
            supported_language="pt",
            name="BrBankAccountRecognizer",
            patterns=[
                Pattern(
                    name="conta_judicial_ag_op_conta",
                    regex=(
                        r"(?:Ag\.?\s*/\s*Op\.?\s*/\s*Conta|"
                        r"Ag\.?\s+Op\.?\s+Conta|"
                        r"Ag[êe]ncia\s*/\s*Opera[cç][aã]o\s*/\s*Conta|"
                        r"conta\s+judicial)"
                        r"\s*(?:n[^\s\d]{0,3})?\s*:?\s*"
                        r"(?P<valor>\d{3,5}/\d{2,4}/\d{3,6}-?\d)"
                    ),
                    score=0.9,
                ),
                Pattern(
                    name="agencia_e_conta_rotuladas",
                    regex=(
                        r"\bAG[ÊE]NCIA\s+E\s+CONTA\s*(?:N[.\s]*[Oº°]?\s*)?:?\s*"
                        r"(?P<valor>\d{3,5}(?:-\d)?(?:\s*[/\-]\s*|\s+)\d{4,12}(?:-\d)?)"
                    ),
                    score=0.9,
                ),
                Pattern(
                    name="agencia_com_conta_rotulada",
                    regex=(
                        r"\bAG[ÊE]NCIA\s*(?:N[.\s]*[Oº°]?\s*)?:?\s*"
                        r"(?P<agencia>\d{3,5}(?:-\d)?)"
                        r"\s*[,;]?\s*(?:E\s+)?CONTA\s*(?:N[.\s]*[Oº°]?\s*)?:?\s*"
                        r"(?P<conta>\d{5,12}(?:-\d)?|\d{4}-\d)"
                    ),
                    score=0.9,
                ),
                Pattern(
                    name="banco_agencia_rotulada",
                    regex=(
                        r"\b(?:BANCO\s*/\s*)?AG[ÊE]NCIA\s*(?:N[.\s]*[Oº°]?\s*)?:?\s*"
                        r"(?P<valor>\d{3}\s*/\s*\d{3,5}(?:-[0-9X])?)(?![\d/])"
                    ),
                    score=0.9,
                ),
                Pattern(
                    name="agencia_rotulada",
                    regex=(
                        r"\bAG[ÊE]NCIA\s*(?:N[.\s]*[Oº°]?\s*)?:?\s*"
                        r"(?P<valor>\d{3,5}(?:-\d)?)(?!\d)"
                    ),
                    score=0.8,
                ),
                Pattern(
                    name="conta_rotulada",
                    regex=(
                        r"\bCONTA(?:\s+CORRENTE|\s+BANC[ÁA]RIA)?"
                        r"\s*(?:N[.\s]*[Oº°]?\s*)?:?\s*"
                        r"(?P<valor>\d{5,12}(?:-\d)?|\d{4}-\d)(?!\d)"
                    ),
                    score=0.8,
                ),
                Pattern(
                    name="conta_sispar_rotulada",
                    regex=(
                        r"\b(?:CONTA\s+SISPAR|SISPAR|INSCRI[CÇ][AÃ]O\s+SISPAR)\s*:?\s*"
                        r"(?P<valor>[0-9*]{3,20}(?:/[0-9*]{2,8})?)(?![0-9A-Za-z*])"
                    ),
                    score=0.85,
                ),
                Pattern(
                    name="inscricao_debcad_rotulada",
                    regex=(
                        r"\b(?:DEBCAD|SIDA)\s*"
                        r"(?:N[.\s]*[Oº°]?\s*)?:?\s*"
                        r"(?P<valor>\d{6,14})(?!\d)"
                    ),
                    score=0.8,
                ),
            ],
        ),
        ValueGroupPatternRecognizer(
            supported_entity="BR_RENAVAM",
            supported_language="pt",
            name="BrRenavamRecognizer",
            patterns=[
                Pattern(
                    name="renavam_com_rotulo",
                    regex=(
                        r"\bRENAVAM\s*(?:N[.\s]*[Oº°]?\s*)?:?\s*"
                        r"(?P<valor>\d{9,11})(?!\d)"
                    ),
                    score=0.85,
                )
            ],
        ),
        ValueGroupPatternRecognizer(
            supported_entity="BR_PLACA",
            supported_language="pt",
            name="BrVehiclePlateRecognizer",
            patterns=[
                Pattern(
                    name="placa_com_rotulo",
                    regex=(
                        r"\bPLACA\s*(?:N[.\s]*[Oº°]?\s*)?:?\s*"
                        r"(?P<valor>[A-Z]{3}-?(?:\d{4}|\d[A-Z]\d{2}))\b"
                    ),
                    score=0.85,
                )
            ],
        ),
    ]
