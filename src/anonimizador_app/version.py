"""Versao do que esta rodando, para conferir na janela e em cada lote qual implementacao gerou o texto.

- ``APP_VERSION``: versao do app (a mesma do ``pyproject.toml``).
- extrator: ``FAST_EXTRACTOR_VERSION`` (muda quando a leitura do PDF muda; invalida o cache).
- ``DETECTION_VERSION``: cadeia de deteccao (IDs, lista de alvos, capa, GLiNER, fidelidade; 2.1 = + propagacao do lote;
  2.2 = + limites do token, fila 3.x E1: CNPJ alfanumerico com digito, telefone dentro de codigo,
  endereco que para na celula e nao corta numero; 2.3 = + tipo por entidade, E7: rua com nome
  de pessoa vira endereco, instituicao financeira do SISBAJUD fica em claro; 2.4 = + rotulo
  fora do token, E11: "Agencia:", "Debcad:", "Matricula n", "CEP:" e o papel da parte ("executada X")
  ficam no texto e o valor inteiro vai no token; CNPJ com a filial partida; CEP de 8 digitos em tabela;
  2.5 = + identidade do lote, E13: hifen de quebra de linha ("ME-TAIS"), pontuacao de OCR, forma curta
  do nome com varias capturas, prenome e ramo de atividade sem juntar por uma letra, "Filho"/"Junior"
  separados, marca de empresa usada sozinha com token proprio;
  2.6 = + endurecimento antes do L4, B3: a marca de uma palavra nunca e nome de municipio ou de estado
  (lista do IBGE) nem ocorrencia em contexto de foro ou de endereco;
  2.7 = + matricula por cartorio: o token distingue o cartorio de registro de imoveis mais proximo, e o
  nome do cartorio/oficio/tabelionato fica em claro;
  2.8 = + grupo de empresas ligadas: o token de empresa que o ELENCO liga por vinculo declarado (mudanca de
  nome, mesmo CNPJ nas capas) leva o sufixo ·Gn no texto, ex. [EMPRESA_3·G1]; os tokens seguem separados);
  2.8.1 = H3: o par de grafia de rua do ``address.py`` (alterado no H2 sem troca de versao) e a identidade do
  motor de NER (backend, modelo e revisao) na chave do cache; as "alteracoes sucessivas" de nome so
  corroboram vinculo explicito (nao criam grupo sozinhas).
  2.8.2 = H4: com o motor LLM, os nomes do piso local passam pelo juizo do lote do motor local
  (procurador e juiz, banco do SISBAJUD e rua com nome de pessoa) antes de entrarem no elenco.
  2.8.3 = H5: pipeline unico; com o motor LLM, o elenco do lote do motor local tambem acrescenta os IDs ja
  vistos no lote e a marca de uma palavra (com o token da empresa), e a revisao residual avisa como no local.
  2.8.4 = H9-a: o agente publico do juizo do lote (cargo colado ao nome em alguma ocorrencia) sai da troca
  tambem quando a deteccao direta o achou, com ou sem cargo perto; antes so a propagacao era barrada.
  2.8.5 = H9-e: reconhecedores novos (RG de SC "8/R.1.964.808", NIRE, CNH, celular com o 9 separado, processo CNJ
  partido em duas linhas, URL com chave de assinatura/consulta) e a segunda higiene das mencoes do LLM.
  2.9.0 = H9-f: correcoes da higiene (nome com "e" no meio, cidade como pessoa, nome fantasia), URL com chave acima
  de processo e CPF, "manter" indevido nas entradas do LLM, propagacao de variante aceita no elenco (marca curta,
  prenome, sobrenome em "familia X", OCR), nome de arquivo pseudonimizado, imagem omitida no docx, endereco
  recortado antes da cidade.
  2.9.1 = H9-g: token estendido por adjacencia sobre a palavra de nome colada ("[PESSOA_2] Domenico"), recusas da
  propagacao contadas por motivo, sobrenome de municipio com pista de pessoa, marca curta ligada a razao social,
  dicionario em uso comum, veiculo so com modelo ou numero, "socio" fora da prova de nome particular, cargos de
  magistrado, cache de extracao do docx com versao propria.
  2.9.2 = H9-h: fusao de identidade no elenco, "manter negado" com o veredito pos-juizo do motor local, sobrenome e
  adjacencia sem homonimo, passada final de formas conhecidas (ID do mapa), e-mail partido entre celulas, "familia
  De X", cargo de socio e de cartorio, veiculo em todas as origens, RG "8/C.1.234.567", registro da JUCESC, rua
  numerada.
  2.10.0 = H9-i lote A: telefone, e-mail e CEP tolerantes, CDA e processo administrativo trocados, matricula sempre em
  claro, cadastro rural/registro da Junta/IP/CPF mascarado/OAB trocados, PIX (UUID e EndToEndId), sufixos e rotulos soltos,
  lista protegida em arquivo de dados com padroes (orgao fracionado, sigla com UF, cooperativa, legislacao) e trava da
  lista do caso, logradouro com nome proprio, qualidade processual fora do token.
  2.11.0 = H9-i lote B: lista do caso como chave unica tambem com LLM (grafias adicionais, frases da empresa, mencao do
  LLM e do motor local apontadas para a entrada da lista, que vence a lista protegida e a higiene), processos, CDAs,
  contatos e veiculos da aba Caso trocados sem rotulo, raiz de CNPJ, zero a esquerda e NIRE listados, nome completo num
  token so, julgador de ementa em claro, nova tentativa do trecho vazio e o LLM retirando nome so do detector de nomes
  (desligado por padrao, contado na AUDITORIA).
  2.12.0 = H9-j (teste ponta a ponta com IA externa): caracteres invisiveis do PDF viram espaco; CDA com espacos,
  processo com DV errado depois de "Execucao Fiscal", processo antigo da JF, matricula de registro civil, MPF, URL
  com id, chassi/placa/RENAVAM em pares de tabela e em lista, conta em comprovante; papel processual nunca e nome;
  nome de pessoa para na virgula e na palavra minuscula; juizo do lote tambem na verificacao; servidor do eproc;
  contato de orgao publico em claro; mesmo CNPJ = mesma empresa; reenvio do trecho vazio desligado.
  2.12.1 = H9-j rodada 2: fusao por CNPJ sobre o texto mascarado; banco do extrato com cabecalho de tabela; papel com
  artigo; palavra minuscula e comentario HTML fora de token; CDA partida, placa com zero, conta repetida; trecho vazio
  reenviado em dois pedacos.
  2.13.0 = H9-k: guarda final, identificadores em tabela, CTPS, CNPJ de protocolo (ver GOVERNANCA).
  2.14.0 = H9-n: a grafia cadastrada em "Outras grafias" casa sozinha (palavra unica e prenome/sobrenome comum;
  municipio/UF, denylist e dicionario seguem com apoio); ELENCO com "Pode ser a mesma pessoa" tambem no modo
  local (e "Nenhum caso detectado" quando vazia), com o mesmo CPF ao lado.
  2.15.0 = H9-o: particula/sobrenome de um listado cede ao nome mais longo de OUTRA pessoa (NER); prenome cadastrado
  como grafia nao troca nome de magistrado/relator nem dentro de termo protegido; data de nascimento por extenso e
  com "DN" vira [NASCIMENTO_n] e data nunca vira endereco (caixa Contatos e importador).
  2.16.0 = v0.4.10: pai e filho (Filho/Junior/Neto/Sobrinho) nunca no mesmo token (lista, mencao, juncao do sufixo,
  elenco); mae e filha (nome de 3+ palavras que abre outro) separadas; pedaco listado volta se o nome longo perde;
  prenome listado no comeco do nome inteiro; mesmo CNPJ une a entrada da lista; validacao do que o LLM devolve
  (data, codigo eproc, linha de folha, "familia X", bordas, empresa como pessoa) e regra do julgador nas entradas
  do LLM; prenome da lista dentro do nome de outra pessoa ou em minuscula fica de fora.
  2.17.0 = v0.4.11: nenhum agente publico vira token (cargo colado ao nome, por ocorrencia; o nome inteiro no lote so
  sem prova de particular em nenhuma ocorrencia); empresa unida por CNPJ (tambem a forma de uma palavra), por declaracao
  ("doravante", "nome fantasia", "atual/antiga denominacao") e pela forma curta de UMA razao social; a forma curta de
  radical comum sai da entrada; "holding X" sem a palavra; sobrenome de familia com [SOBRENOME_n]; guarda de tipo
  ENDERECO (cartorio, protocolo, titulo de peca, empresa, sem marcador; "A. R. Souza" nao e "Rua"); pessoa com e sem
  nome do meio unida (sem desfazer pai/filho, mae/filha, casada); fim de razao social depois da quebra de pagina.
  2.18.0 = v0.4.12: nome da lista casado com tolerancia a erro de OCR (so nas folhas de OCR; prenome so com uma
  troca entre letras confundiveis; grafia perto de 2+ nomes nao casa); O/o->0, l/I->1, S->5 em NIRE, RG, CPF,
  CNPJ e matricula (CPF/CNPJ com DV); cartorarios e nome do cartorio em claro; "Min. X", "empresa executada" e
  o contato do orgao publico no cabecalho em claro; agente publico tambem no motor local; AVISOS.md.
  2.19.0 = v0.4.13: a mesma denominacao usada por duas empresas (CNPJ diferentes) separada por empresa (CNPJ colado,
  ponte "na epoca denominada", qualificacao; outro nome da empresa ligado por expressao de denominacao; mesma folha;
  senao token proprio "sem contexto") e o nome colado a dois CNPJ nao une por CNPJ; forma parcial do LLM (sobrenome,
  prenome, prenome + 2o nome, razao social sem sufixo ou abreviada) ligada a UMA entrada do elenco; assinante do
  carimbo da Junta Comercial (secretario-geral, com erro de OCR, ate 3 linhas abaixo) em claro.
  2.20.0 = v0.4.14: sobrenome de 2+ pessoas da lista casado sozinho nao vai mais para a pessoa da mencao completa mais
  proxima (decide pelos pedacos da lista colados; senao [SOBRENOME_n]); o nome inteiro de outra pessoa partido numa
  quebra de linha vence o pedaco da lista; pedacos colados da mesma pessoa viram um token so.
  Ainda 2.20.0: matricula de imovel trocada de novo (gatilho "matricula"), conta com "numero", RG de SC/PR com hifen,
  NIRE e registro pela Junta Comercial e pelo carimbo, passada final de formas conhecidas tambem no motor local.
  2.21.0 = v0.4.15: matricula de imovel de novo em claro (decisao do Operador); com LLM, o nome canonico do LLM so
  liga a grafia que e o mesmo nome da empresa, a forma afirmada de duas empresas fica separada, raiz de CNPJ
  diferente veta a fusao e o grupo ·Gn, e o nome da pessoa do elenco vence o [SOBRENOME_n] da lista. No motor:
  prenome solto da pessoa do lote ([PRENOME_n] se 2+), sigla da empresa da lista e razao social depois de
  "denominacao", complemento e bairro no token do endereco, advogado da lista do eproc e antes da OAB, usuario do
  sistema e autor de doutrina em claro, prenome em nome de lugar fica.
  2.22.0 = v0.4.16: nome da operacao do Nexus fora do exportavel; denominacao de 2+ empresas so separada com ancora
  (CNPJ na frase, razao social completa), senao um token declarado no ELENCO; CPF ao lado de dois nomes nao liga;
  prenome da mesma pessoa em varios tokens; ramo + marca da lista leva o token da empresa.
- motor: ``local`` (padrao do app) ou ``LLM: <canal>`` (3.0 com LLM; o canal decide o tratamento do texto
  no provedor). Aparece na linha de versao, no LEIA-ME e na AUDITORIA de cada lote.
- codigo: impressao digital dos ``.py`` carregados (muda com qualquer edicao, mesmo sem commit).
- commit: o commit do git em que a pasta esta, lido direto de ``.git`` (sem chamar programa externo).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .fast_extract import FAST_EXTRACTOR_VERSION

ENGINE_LOCAL = "local"
APP_VERSION = "0.4.17"
DETECTION_VERSION = "2.22.0"

_SRC = Path(__file__).resolve().parents[1]
_ROOT = _SRC.parent


@dataclass(frozen=True, slots=True)
class VersionInfo:
    app: str
    extractor: str
    detection: str
    code: str
    commit: str = ""
    branch: str = ""

    def short(self) -> str:
        return f"v{self.app} · código {self.code}"

    def line(self, engine: str = ENGINE_LOCAL) -> str:
        """``engine``: ``local`` (ou vazio) ou o id do canal do LLM ("gemini-pago")."""

        parts = [
            f"v{self.app}",
            f"extração {self.extractor}",
            f"detecção {self.detection}",
            engine_label(engine),
            f"código {self.code}",
        ]
        if self.commit:
            parts.append(f"commit {self.commit}" + (f" ({self.branch})" if self.branch else ""))
        return " · ".join(parts)


def engine_label(engine: str = ENGINE_LOCAL) -> str:
    """"motor local" ou "motor LLM: gemini-pago"."""

    channel = (engine or "").strip()
    return "motor local" if channel in ("", ENGINE_LOCAL) else f"motor LLM: {channel}"


@lru_cache(maxsize=1)
def version_info() -> VersionInfo:
    commit, branch = git_head()
    return VersionInfo(
        app=APP_VERSION,
        extractor=FAST_EXTRACTOR_VERSION,
        detection=DETECTION_VERSION,
        code=code_fingerprint(),
        commit=commit,
        branch=branch,
    )


def code_fingerprint(root: Path = _SRC) -> str:
    """SHA-256 dos .py do app (quebra de linha normalizada: CRLF e LF dao o mesmo valor)."""

    hasher = hashlib.sha256()
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        hasher.update(path.relative_to(root).as_posix().encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(path.read_bytes().replace(b"\r\n", b"\n"))
        hasher.update(b"\0")
    return hasher.hexdigest()[:7]


def git_head(root: Path = _ROOT) -> tuple[str, str]:
    """(commit curto, branch) da pasta; vazio se nao for um checkout git."""

    try:
        git_dir = root / ".git"
        if git_dir.is_file():
            pointer = git_dir.read_text(encoding="utf-8").strip()
            if not pointer.startswith("gitdir:"):
                return "", ""
            git_dir = (root / pointer[len("gitdir:"):].strip()).resolve()
        common = git_dir
        commondir = git_dir / "commondir"
        if commondir.is_file():
            common = (git_dir / commondir.read_text(encoding="utf-8").strip()).resolve()
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head[:7], ""
        ref = head[len("ref:"):].strip()
        branch = ref[len("refs/heads/"):] if ref.startswith("refs/heads/") else ref
        for base in (git_dir, common):
            ref_file = base / ref
            if ref_file.is_file():
                return ref_file.read_text(encoding="utf-8").strip()[:7], branch
        packed = common / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                if line.endswith(" " + ref):
                    return line.split(" ", 1)[0][:7], branch
        return "", branch
    except OSError:
        return "", ""
