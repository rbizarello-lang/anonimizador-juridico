"""Marca, modelo e implemento de veiculo: nao sao empresa (H9-f, H9-g, H9-h).

"Fiat Strada 2020", "Honda CG 160", "Amarok", "Librelato" (carroceria) saem de listagens de veiculo (RENAJUD,
penhora, leilao) e o NER tomava por empresa. A H9-g so descartava a mencao do LLM; modelo que veio do motor local ou da
propagacao do elenco continuava como [EMPRESA_n]. Esta regra vale para o TEXTO da mencao, venha de onde vier:

- marca seguida de modelo conhecido ou de palavra com digito (ano, cilindrada, codigo): veiculo;
- marca, modelo ou fabricante de implemento SEM mais nada, so quando ha prova no texto em volta: placa na mesma linha ou
  ano colado ao nome ("Amarok 2014/2015", "Librelato 2012", "FIAT STRADA FREEDOM CD  ABC1D23");
- com sufixo societario ou palavra de comercio (Veiculos, Financeira, Leasing, Participacoes...), a mencao segue empresa.
"""

from __future__ import annotations

import re

from anonimizador_juridico.pseudonymizer import fold_lexical

VEHICLE_BRANDS = frozenset(
    "fiat volkswagen vw chevrolet gm ford toyota honda hyundai renault nissan jeep peugeot citroen mitsubishi "
    "kia bmw mercedes audi volvo yamaha suzuki kawasaki iveco scania chery caoa byd harley".split()
)
VEHICLE_MODELS = frozenset(
    "uno palio siena strada toro mobi argo cronos fiorino doblo gol polo virtus saveiro voyage fox golf jetta "
    "amarok tiguan nivus kombi fusca onix prisma celta corsa classic cobalt cruze tracker spin montana s10 "
    "camaro fiesta ka focus ranger ecosport fusion hilux corolla etios yaris civic fit city cg biz titan fazer "
    "factor lander hornet xre hb20 creta tucson sandero duster kwid logan captur kicks versa march frontier "
    "compass renegade commander clio megane scenic ninja".split()
)
# Fabricante de carroceria e implemento rodoviario (reboque, semirreboque). Nome que tambem e sobrenome comum fica de fora.
IMPLEMENT_BRANDS = frozenset("librelato randon facchini rossetti bertoloni recrusul fruehauf krone".split())
VEHICLE_TRADE = frozenset(
    "veiculos automoveis automotores motos motors motor comercio concessionaria pecas distribuidora revenda "
    "auto locadora locacao transportes transportadora do da de brasil financeira financial financiamentos "
    "services servicos leasing participacoes seguradora seguros banco consorcio administradora latin america "
    "industria industrial implementos rodoviarios company companhia holding".split()
)
_COMPANY_SUFFIX = re.compile(r"(?i)\b(?:ltda|me|epp|eireli|s/?a|s\.a|cia|companhia)\b")
_YEAR = re.compile(r"(?:19|20)\d{2}(?:/(?:19|20)?\d{2})?")
_PLATE = re.compile(r"(?<![A-Za-z0-9])[A-Z]{3}-?\d[A-Z0-9]\d{2}(?![A-Za-z0-9])", re.IGNORECASE)
_YEAR_AFTER = re.compile(rf"^[\s,/|:\-]*(?:ano\s*)?{_YEAR.pattern}(?!\d)")
_YEAR_BEFORE = re.compile(rf"(?<!\d){_YEAR.pattern}[\s,/|:\-]*$")
_VOCAB = VEHICLE_BRANDS | VEHICLE_MODELS | IMPLEMENT_BRANDS


def _words(value: str) -> list[str]:
    return fold_lexical(value).replace("-", " ").replace("/", " ").split()


def is_vehicle_text(value: str) -> bool:
    """Marca + modelo conhecido ou palavra com digito (sem sufixo societario nem palavra de comercio)."""

    if _COMPANY_SUFFIX.search(value):
        return False
    words = _words(value)
    if len(words) < 2 or words[0] not in VEHICLE_BRANDS or len(words) > 6:
        return False
    rest = words[1:]
    if any(word in VEHICLE_TRADE for word in rest):
        return False
    return any(word in VEHICLE_MODELS or any(char.isdigit() for char in word) for word in rest)


def is_vehicle_span(text: str, start: int, end: int) -> bool:
    """O trecho ``text[start:end]`` e marca, modelo ou implemento de veiculo, pelo texto dele ou pelo que o cerca."""

    if not 0 <= start < end <= len(text):
        return False
    value = text[start:end]
    if is_vehicle_text(value):
        return True
    if _COMPANY_SUFFIX.search(value):
        return False
    words = _words(value)
    if not words or len(words) > 5 or words[0] not in _VOCAB:
        return False
    if any(word in VEHICLE_TRADE for word in words[1:]):
        return False
    # Palavra seguinte do mesmo nome ("Librelato Implementos Rodoviarios"): o trecho nao esta inteiro.
    following = re.match(r"[ \t]+([^\W\d_]+)", text[end : end + 40])
    if following is not None and fold_lexical(following.group(1)) in VEHICLE_TRADE - {"do", "da", "de"}:
        return False
    line_start = text.rfind("\n", 0, start) + 1
    line_end = text.find("\n", end)
    line = text[line_start : len(text) if line_end < 0 else line_end]
    if _PLATE.search(line):
        return True
    return bool(_YEAR_AFTER.match(text[end : end + 20]) or _YEAR_BEFORE.search(text[max(0, start - 20) : start]))
