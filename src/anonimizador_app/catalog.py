from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TagInfo:
    entity_type: str
    token: str
    description: str
    covered: bool


STRUCTURED_TAGS: tuple[TagInfo, ...] = (
    TagInfo("BR_CPF", "[CPF_n]", "CPF com digitos verificadores", True),
    TagInfo("BR_CNPJ", "[CNPJ_n]", "CNPJ numerico ou alfanumerico", True),
    TagInfo("BR_PROCESSO", "[PROCESSO_n]", "Numero CNJ (Modulo 97)", True),
    TagInfo("EMAIL_ADDRESS", "[EMAIL_n]", "Endereco de e-mail", True),
    TagInfo("BR_PHONE", "[TELEFONE_n]", "Telefone brasileiro (desligue se atrapalhar a peca)", True),
    TagInfo("BR_OAB", "[OAB_n]", "Inscricao na OAB", True),
    TagInfo("BR_RG", "[RG_n]", "RG quando vier com o rotulo", True),
    TagInfo("BR_MATRICULA_IMOVEL", "[MATRICULA_IMOVEL_n]", "Matricula de imovel com rotulo", True),
    TagInfo("BR_CEP", "[CEP_n]", "CEP quando vier com o rotulo", True),
    TagInfo("BR_PIX", "[PIX_n]", "Chave Pix quando vier com o rotulo", True),
    TagInfo("BR_CONTA_BANCARIA", "[CONTA_n]", "Agencia ou conta quando vier com o rotulo", True),
    TagInfo("BR_RENAVAM", "[RENAVAM_n]", "RENAVAM com rotulo ou cabecalho de coluna", True),
    TagInfo("BR_PLACA", "[PLACA_n]", "Placa com rotulo ou cabecalho de coluna", True),
    TagInfo("BR_CHASSI", "[CHASSI_n]", "Chassi/VIN com cabecalho de coluna ou campo rotulado", True),
    TagInfo("BR_NASCIMENTO", "[NASCIMENTO_n]", "Data de nascimento com rotulo de formulario", True),
    TagInfo("BR_TITULO_ELEITOR", "[TITULO_n]", "Titulo de eleitor com rotulo de formulario", True),
    TagInfo("BR_PIS", "[PIS_n]", "PIS/PASEP/NIT com rotulo e digito verificador", True),
    TagInfo("BR_EPROC", "[EPROC_n]", "Codigo eproc do procurador na capa (PR077705, P3412557)", True),
)

CONTEXTUAL_TAGS: tuple[TagInfo, ...] = (
    TagInfo("PERSON", "[PESSOA_n]", "Nomes completos — capa eproc e NerLocal (GLiNER)", True),
    TagInfo("ORGANIZATION", "[EMPRESA_n]", "Empresas privadas — capa/CDA e NerLocal (GLiNER)", True),
    TagInfo("LOCATION", "[ENDERECO_n]", "Logradouro com via e numero — capa/CDA e NerLocal (GLiNER)", True),
)

FIDELITY_PRINCIPLE = (
    "A peca precisa continuar analisavel. Datas, valores, eventos, teses, orgaos "
    "e termos juridicos permanecem. Na duvida, o trecho original fica: um dado "
    "que escape pontualmente e menos grave do que descaracterizar o documento."
)

NER_LOCAL_RECOGNIZER = "NerLocal"
GEMINI_CONTEXTUAL_RECOGNIZER = "GeminiContextual"

EXPORT_BLOCKED_REASON = (
    "Sem o motor local (GLiNER / NerLocal), nomes, empresas e enderecos nao "
    "sao detectados. Ligue o motor local na aba Anonimizar."
)

GEMINI_POLICY = (
    "O Gemini nao roda neste app. Depois do lote, o operador pode abrir o PDF "
    "original e o Markdown em saidas/exportaveis num destino externo. "
    "O mapa privado nao viaja."
)
