from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TagInfo:
    entity_type: str
    token: str
    description: str
    covered: bool


STRUCTURED_TAGS: tuple[TagInfo, ...] = (
    TagInfo("BR_CPF", "[CPF_n]", "CPF com dígitos verificadores", True),
    TagInfo("BR_CNPJ", "[CNPJ_n]", "CNPJ numérico ou alfanumérico", True),
    TagInfo("BR_PROCESSO", "[PROCESSO_n]", "Número CNJ (módulo 97)", True),
    TagInfo("EMAIL_ADDRESS", "[EMAIL_n]", "Endereço de e-mail", True),
    TagInfo("BR_PHONE", "[TELEFONE_n]", "Telefone brasileiro (desligue se atrapalhar a peça)", True),
    TagInfo("BR_OAB", "[OAB_n]", "Inscrição na OAB", True),
    TagInfo("BR_RG", "[RG_n]", "RG quando vier com o rótulo", True),
    TagInfo("BR_MATRICULA_IMOVEL", "[MATRICULA_IMOVEL_n]", "Matrícula de imóvel com rótulo", True),
    TagInfo("BR_CEP", "[CEP_n]", "CEP quando vier com o rótulo", True),
    TagInfo("BR_PIX", "[PIX_n]", "Chave Pix quando vier com o rótulo", True),
    TagInfo("BR_CONTA_BANCARIA", "[CONTA_n]", "Agência ou conta quando vier com o rótulo", True),
    TagInfo("BR_RENAVAM", "[RENAVAM_n]", "RENAVAM com rótulo ou cabeçalho de coluna", True),
    TagInfo("BR_PLACA", "[PLACA_n]", "Placa com rótulo ou cabeçalho de coluna", True),
    TagInfo("BR_CHASSI", "[CHASSI_n]", "Chassi/VIN com cabeçalho de coluna ou campo rotulado", True),
    TagInfo("BR_NASCIMENTO", "[NASCIMENTO_n]", "Data de nascimento com rótulo de formulário", True),
    TagInfo("BR_TITULO_ELEITOR", "[TITULO_n]", "Título de eleitor com rótulo de formulário", True),
    TagInfo("BR_PIS", "[PIS_n]", "PIS/PASEP/NIT com rótulo e dígito verificador", True),
    TagInfo("BR_EPROC", "[EPROC_n]", "Código eproc do procurador na capa (PR077705, P3412557)", True),
)

CONTEXTUAL_TAGS: tuple[TagInfo, ...] = (
    TagInfo("PERSON", "[PESSOA_n]", "Nomes completos — capa eproc e motor local (GLiNER)", True),
    TagInfo("ORGANIZATION", "[EMPRESA_n]", "Empresas privadas — capa/CDA e motor local (GLiNER)", True),
    TagInfo("LOCATION", "[ENDERECO_n]", "Logradouro com via e número — capa/CDA e motor local (GLiNER)", True),
)

FIDELITY_PRINCIPLE = (
    "A peça precisa continuar analisável. Datas, valores, eventos, teses, órgãos "
    "e termos jurídicos permanecem. Na dúvida, o trecho original fica: um dado "
    "que escape pontualmente é menos grave do que descaracterizar o documento."
)

NER_LOCAL_RECOGNIZER = "NerLocal"
GEMINI_CONTEXTUAL_RECOGNIZER = "GeminiContextual"

EXPORT_BLOCKED_REASON = (
    "Sem o motor local (GLiNER), nomes, empresas e endereços não "
    "são detectados. Ligue o motor local no painel do lote."
)

GEMINI_POLICY = (
    "O Gemini não roda nesta tela. Depois do lote, o operador pode abrir o PDF "
    "original e o Markdown de saidas/exportaveis num destino externo — de "
    "preferência por conta institucional (Vertex AI ou Workspace), nunca em conta "
    "gratuita ou pessoal. O mapa privado não viaja."
)
