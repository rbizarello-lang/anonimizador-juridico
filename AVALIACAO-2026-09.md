# Avaliação do Anonimizador Jurídico e proposta 3.0 (LangExtract + Gemini)

**Data:** 25/09/2026 · **Versão avaliada:** 0.3.0 (commit `09581cd`)
**Escopo pedido:** (1) avaliação geral do app e indicativos de melhoria; (2) avaliação do
LangExtract (github.com/google/langextract) e de como usá-lo aqui; (3) uma solução eficiente e
rápida, mantendo ou reconfigurando o papel do Gemini.

Este documento não substitui o `GOVERNANCA.md`: é insumo para uma decisão ("Veredito 3.0").
Nenhum dado real foi usado: todos os nomes, números e endereços dos testes são fictícios.

---

## 0. Resumo em dois minutos

1. **A maior parte dos vazamentos não vem de "falta de IA": vem das regras do próprio app que
   descartam acertos.** Experimento: alimentei o pipeline atual com um detector *perfeito*
   (que acha todas as entidades). **7 de 8 entidades vazaram mesmo assim.** A causa são seis
   regras de "fidelidade" que, criadas para corrigir falsos positivos de um lote específico,
   hoje descartam nomes comuns brasileiros, pessoas citadas na mesma linha que "PGFN" e
   empresas cujo nome começa com "União", "Fazenda", "Evento" etc. — estas, nem se o usuário
   as puser na lista de alvos (§2).
2. **A perda de estrutura tem causas concretas e corrigíveis:** o módulo de "geografia"
   transforma toda menção a "evento N" no meio de uma frase em título e parte a frase ao meio;
   o Docling reescreve o PDF em Markdown com perdas; o leitor de DOCX joga todas as tabelas
   para o fim do texto (§3).
3. **Velocidade:** havia cinco algoritmos quadráticos (um deles dentro do Presidio). Só as
   regras determinísticas do app atual levaram **11,5 min para 1.000 páginas**, sem contar
   Docling e GLiNER. Corrigi os cinco, com saída idêntica byte a byte verificada: o app atual
   ficou **6 a 7× mais rápido** nessa parte (§4). Docling + GLiNER em CPU continuam não
   escalando para 2–3 mil folhas.
4. **Risco de privacidade no fluxo atual:** a governança 2.0 manda o operador abrir **o PDF
   original** no Gemini, fora do app. Em conta gratuita do Gemini ou no Antigravity com conta
   pessoal, os termos permitem usar o conteúdo para melhorar produtos. Com sigilo fiscal
   (CTN, art. 198), esse é hoje o maior risco — maior que um nome que escape (§6).
5. **LangExtract serve — como motor de *descoberta*, não como anonimizador completo.** Li o
   código-fonte (v1.7.0) e achei três armadilhas sérias para anonimização (uma ocorrência por
   extração; descarte silencioso de trecho com erro; um trecho com erro derruba o documento
   inteiro no modo paralelo). O protótipo contorna as três (§7).
6. **Proposta 3.0 — "o LLM classifica, o código troca":** CPF/CNPJ mascarados nesta máquina
   → Gemini (via LangExtract, de preferência Vertex AI em São Paulo) aponta *quem é quem* e o
   papel de cada um → tabela de papéis decide → propagação determinística a todas as
   ocorrências → troca por token → o Gemini relê o texto **já tokenizado** e acusa o que
   sobrou → página não lida bloqueia a exportação → o usuário revisa o **elenco** (20–100
   linhas), não as 3.000 páginas (§8).
7. **Entregue neste ciclo:** protótipo funcional (`tools/anonimizar_llm.py`), 47 testes novos,
   ferramenta de diagnóstico (`tools/sonda_vazamento.py`) e as correções de desempenho do
   núcleo. Medido: **3.000 páginas em 19 s** de processamento local (+ o tempo do LLM, estimado
   em 3–8 min com 16–32 chamadas paralelas). Custo estimado: **US$ 2 a 13 por processo de
   3.000 páginas**, conforme o modelo (§8.5, §9).

---

## 1. Como a avaliação foi feita

| Frente | O que foi feito |
|---|---|
| Código | Leitura do pipeline, extração, OCR, reconhecedores, 10 motores heurísticos, regras de fidelidade e higiene (~11 mil linhas em `src/`), do `GOVERNANCA.md` (733 linhas) e das regras em `.cursor/rules`. |
| Testes | Suíte atual rodada: 299 testes; 2 falhas só de Windows (ícone e flags de subprocesso), 1 pulado. |
| Sondas | Pipeline real com detector simulado perfeito (isola o pós-processamento); teste do serializador de geografia; perfilamento com PDF sintético de 1.000 e 3.000 páginas. |
| LangExtract | Instalado (v1.7.0) e lido o código-fonte: chunking, alinhamento, passes múltiplos, provedores, tratamento de erros. Rodado com modelo falso, sem rede. |
| Pesquisa | Termos de uso do Gemini API e do Antigravity; preços; residência de dados no Vertex AI. Alguns domínios do Google estavam bloqueados pela rede deste ambiente: os termos foram confirmados por busca e devem ser reconferidos no texto vigente. |
| Limite honesto | **Não houve chamada real ao Gemini** (sem chave neste ambiente). Qualidade real do LLM e tempo por chamada são estimativas; o protótipo está pronto para essa medição no lote real (§10). |

---

## 2. Por que vaza

### 2.1. Experimento decisivo: detector perfeito, pipeline atual

`python tools/sonda_vazamento.py` — o mesmo documento fictício, com o GLiNER substituído por
um detector que acerta tudo. Resultado:

| Entidade (fictícia) | Tipo | Vazou? | Regra que descartou o acerto |
|---|---|---|---|
| FAZENDA SANTA RITA AGROPECUARIA LTDA | empresa (executada) | **sim** | "Fazenda" é marcador de órgão público; janela de precedente |
| PAULO ROBERTO SOUZA | sócio | **sim** | linha contém "PGFN"; nome só de partículas comuns |
| MARIA OLIVEIRA SANTOS | sócia | **sim** | nome só de partículas comuns |
| Rua das Acácias, 120 | endereço | não | — |
| UNIÃO COMERCIAL DE GRÃOS LTDA | empresa (sucessora) | **sim** | começa com "União" (KEEP_TERMS por prefixo) |
| CARLOS PEREIRA LIMA | terceiro | **sim** | nome só de partículas comuns |
| JOSÉ ANTÔNIO FERREIRA | representante legal | **sim** | linha contém "PGFN" |
| Ana Lu Costa | contadora | **sim** | nome só de partículas comuns |

**7 de 8 vazaram com detector perfeito.** No pipeline proposto, com o mesmo "detector
perfeito", **0 de 8** — e União, PGFN, `R$`, datas, SISBAJUD, Tema e artigo intactos.

### 2.2. As regras responsáveis

| Regra (arquivo) | O que faz | Consequência |
|---|---|---|
| `is_common_person_name_span` (`target_list.py`) | Nome encontrado pelo NER composto só de prenomes/sobrenomes frequentes (Maria, Carlos, Silva, Santos, Oliveira…) **não** é tokenizado. | A maioria dos nomes brasileiros cai aqui. Criada para não tokenizar "Silva" solto; atingiu nomes completos. |
| `is_pfn_officer` (`fidelity.py`) | Pessoa na mesma linha que "PGFN", "PFN", "procurador da Fazenda"… (ou até 80 caracteres depois) é tratada como procurador e mantida. | Em petição da PGFN, "A PGFN requer a citação do sócio FULANO" é a frase mais comum que existe. |
| `is_too_generic` + `_PUBLIC_ORG_MARKERS` (`gemini_detector.py`) | Empresa com "fazenda", "união", "câmara", "fórum", "receita", "secretaria", "turma", "vara"… em qualquer posição é descartada. | "Fazenda Santa Rita Agropecuária" é típico do agro no Sul. Também afeta a heurística de capa (a executada nem chega a ser proposta). |
| `is_kept_term` por **prefixo** (`gemini_detector.py`) | Qualquer valor que **comece** por termo protegido ("união ", "evento ", "tribunal ", "comarca "…) é mantido — inclusive vindo da lista de alvos. | "União Comercial…", "Evento Produções…" nunca são tokenizadas, nem se o usuário listar (verificado com as duas na lista de alvos). |
| `is_precedent_party` (`fidelity.py`) | Empresa a ±200 caracteres de "Tema", "Súmula", "Turma", "REsp", "DJe", " x "… é tratada como parte de precedente. | Peças da PGFN citam Tema/Súmula o tempo todo; a executada ao lado da tese some da troca. |
| `_GENERIC_WORDS` (`gemini_detector.py`) | "paulo", "santa", "santo", "grande", "rio"… não contam como palavra de conteúdo (por causa de São Paulo, Porto Alegre). | "PAULO SOUZA" fica com uma palavra de conteúdo e é descartado. |

Outras causas de vazamento, já conhecidas na governança e confirmadas no código:

- **GLiNER `gliner_multi-v2.1`** é genérico e fraco em CAIXA ALTA e em texto de OCR; o limiar
  0,75 foi (corretamente) mantido, então o recall no corpo é baixo (P2, P11).
- **Heurísticas de capa** só olham os primeiros 16 mil / 80 mil caracteres (P1).
- **Lista de alvos** depende de digitação manual; quando acumulou (240 empresas + 59 pessoas),
  comeu CNAE e o art. 133 do CTN (P13) e teve de ser recortada.
- **Aviso residual** fica desligado quando o cache do NER está ligado (`local_ner.py`).
- **Não há medida de recall.** A `AUDITORIA.txt` conta tokens e protege a estrutura, mas não
  consegue ver o nome que *não* foi trocado. O vazamento é invisível ao processo de aceite.

### 2.3. Por que isso aconteceu (sem culpa: é a dinâmica do método)

Cada regra nasceu de um falso positivo real num lote real, com o cuidado de "um fator por
vez". O problema é estrutural: regras sem contexto, acumuladas, afinadas num único PDF
(PARTE_1), avaliadas só pelo que foi trocado a mais — nunca pelo que deixou de ser trocado.
"Fidelidade > recall" virou, na prática, "na dúvida, descarta". A cada ciclo a peça ficou mais
íntegra e o vazamento mais silencioso. O próprio código registra o sobreajuste: constantes como
`BROGGI`, `CRITÉRIUM`, `vidros`, `esquadrias`, `ETDA/LIDA` e o comentário de CNPJ "neste lote".

---

## 3. Por que perde estrutura

| Problema | Evidência | Efeito na análise jurídica |
|---|---|---|
| **Geografia parte frases.** `geography._split_glued_events` transforma **toda** menção a "evento N" precedida de espaço em título `## Evento N`. | "Conforme o evento 12, a executada foi citada" vira `Conforme o` / `## Evento 12` / `, a executada foi citada`. Reproduzido. | Frases quebradas e **títulos de evento falsos** — o LLM de análise passa a "ver" eventos que não existem naquele ponto. |
| **Docling reescreve o PDF** (Markdown com perdas, tabelas achatadas, eventos fundidos — P3/P4). Tempo limite de 15 min; se estoura, cai no PyMuPDF com outro formato. | Governança §5, §11. | Texto diferente do original; lotes do mesmo PDF podem sair em formatos diferentes. |
| **DOCX:** `_read_docx` lê todos os parágrafos e só depois todas as tabelas. | `extractors.py`. | A tabela de débitos aparece longe do parágrafo que a cita. |
| **TXT/MD ganham títulos falsos** ("# DOCUMENTO EXTRAIDO", "## Pagina 1"). | Saída da sonda. | Ruído na peça. |
| **Super-redação por partícula** da lista de alvos (P13). | `[EMPRESA]` 169→482 no mesmo PARTE_1; CNAE e CTN comidos. | Tese desfigurada. Mitigado, ao custo de mais regras de descarte (as do §2). |

---

## 4. Velocidade e escala (P5)

### 4.1. O que foi medido

PDF sintético de texto nativo, 1.325 caracteres por página (o PARTE_1 real tem ~1.264).

| Etapa | Antes | Depois |
|---|---|---|
| App atual, só regras + lista de alvos (43 alvos, com variantes de OCR), 300 páginas, **mesma saída byte a byte** | 73,6 s | **11,7 s** |
| App atual, só regras + lista de alvos (120 alvos), 1.000 páginas | **688,8 s** (11,5 min) | **102,9 s** (mesmas 18.000 trocas) |
| Pipeline novo, parte local completa (extração, IDs, elenco, propagação, troca, verificação), 3.000 páginas | 187,7 s | **18,7 s** |
| Extração PyMuPDF, 3.000 páginas (3,97 milhões de caracteres) | — | **4,4 s** |
| Referência do próprio lote real (governança): Docling+OCR no PARTE_1 (269 págs.) | ~6 min | — |
| Referência: GLiNER no PARTE_1 | ~8 min | — |

"Antes" e "depois" do app atual rodam o mesmo código de regras; muda só o núcleo e a busca
aproximada da lista de alvos (§4.2).

Extrapolando as referências do lote real para 3.000 páginas: **~2,5 horas** no app atual em
CPU, com o Docling estourando o limite de 15 min por PDF.

### 4.2. Os gargalos quadráticos (corrigidos neste ciclo, no núcleo compartilhado)

| Função | Problema | Correção | Garantia |
|---|---|---|---|
| `resolve_overlaps` (`detector.py`) | Cada candidato comparado com todos os escolhidos. | Busca binária: os escolhidos são disjuntos; basta o vizinho. | Idêntico ao algoritmo antigo em 3.000 casos aleatórios + testes existentes. |
| `StructuredDetector.detect` | O `remove_duplicates` **do Presidio** é quadrático no nº de achados; rodava no processo inteiro de uma vez. | Roda por blocos de ~50 mil caracteres, cortados só em âncora de página (nenhum ID atravessa a âncora). | Mesmos spans que no texto inteiro (verificado). |
| `pseudonymize` (`pseudonymizer.py`) | Fatiava o texto inteiro a cada troca. | Junta os pedaços numa passada. | Mesma saída; testes existentes. |
| `_covered` (`id_propagation.py`) | Cada número comparado com todos os spans. | Índice com máximo acumulado + busca binária. | Mesma pergunta, mesma resposta. |
| `_fuzzy_phrase_spans` (`target_list.py`) | Para **cada alvo**, redividia o texto inteiro em palavras e normalizava cada palavra de novo (80 milhões de normalizações de caractere em 150 páginas com 120 alvos). Era o maior custo do app atual. | Palavras normalizadas uma vez por texto; normalização de caractere memorizada. | Saída do app atual idêntica byte a byte (mesmo hash, mesmo mapa) num PDF de 300 páginas com variantes de OCR. |

Essas cinco correções valem para o app atual e não mudam nenhum resultado — só o tempo.

---

## 5. Processo e governança

O `GOVERNANCA.md` é raro em rigor: decisões datadas, critérios de aborto, histórico reabrível.
Mas ele travou o produto numa premissa — "local e sem LLM" — que a própria realidade dos autos
(milhares de folhas, digitalizações, CAIXA ALTA, OCR) desmentiu, como você mesmo concluiu.

Três ajustes de método, independentemente da tecnologia escolhida:

1. **Medir o que vaza, não só o que se troca.** Um gabarito pequeno (30–50 páginas reais
   anotadas: pessoa, empresa, endereço), guardado localmente, com três números por lote:
   recall por tipo, precisão por tipo e preservação (termos protegidos intactos). Sem isso,
   todo ciclo otimiza às cegas a métrica visível.
2. **Parar de acumular regras de descarte sem contexto.** Quem decide se "Souza" é o sócio ou
   o ministro é quem entende a frase. Esse é o trabalho que o LLM faz bem (§8).
3. **Registrar um "Veredito 3.0"** e atualizar `.cursor/rules`: as regras atuais proíbem LLM e
   Gemini no app (`governanca-prevalece`, `fidelidade-corta-recall`, `pii-nao-viaja`). Um
   agente de código (Cursor, Antigravity) que as leia vai recusar a mudança. Minuta no §10.

---

## 6. O paradoxo de privacidade do fluxo atual

A governança 2.0 tirou o Gemini do app para que "a PII não viaje" — e, no mesmo passo, pôs o
operador a abrir **o original** no Gemini, fora do app, para confrontar vazamento e estrutura.
Resultado: o documento mais sensível (sem nenhuma máscara) vai ao Google de qualquer forma,
pelo canal menos controlado.

O que dizem os termos (conferir o texto vigente antes de decidir):

| Canal | Uso do conteúdo |
|---|---|
| Gemini API, nível gratuito | Conteúdo pode ser usado para melhorar produtos, com revisão humana. |
| Gemini API, nível pago | Não usado para melhorar produtos; tratado sob o adendo de processamento de dados. |
| Antigravity, termos padrão | "Interactions" usadas para avaliar e melhorar produtos e tecnologias de ML do Google; **exceção** para acesso via Gemini Enterprise/Google Cloud, Workspace ou chave Gemini Enterprise. |
| Vertex AI (Google Cloud) | Termos do contrato de nuvem; sem uso para treino; região `southamerica-east1` (São Paulo) com residência de dados no endpoint regional. **Conferir** se o modelo escolhido está publicado nesse endpoint: o endpoint `global` não garante residência. |

Com sigilo fiscal (CTN, art. 198) e LGPD em jogo, **o canal importa mais que a ferramenta.**
Recomendação: projeto institucional no Google Cloud (Vertex AI, região São Paulo), ou no
mínimo Gemini API paga; nunca nível gratuito nem conta pessoal. A escolha do canal é decisão
institucional — vale ouvir o encarregado de dados (DPO) e verificar os contratos existentes.

A proposta 3.0 melhora o quadro mesmo nesse cenário: CPF, CNPJ, e-mail, telefone, conta,
placa etc. são mascarados **nesta máquina antes** de qualquer envio; o `mapa.json` e o elenco
nunca saem; e o LLM de análise (Claude, com as skills da PGFN, ou outro) recebe apenas o
Markdown tokenizado. Mas atenção ao limite: o Gemini da etapa de detecção **vê o texto da
peça** (nomes, endereços, valores, fatos) — só não vê os IDs. Por isso o canal institucional
continua sendo condição, não detalhe.

---

## 7. LangExtract: avaliação

### 7.1. O que é

Biblioteca Python do Google, código aberto (Apache 2.0), versão 1.7.0 instalada. Declara não
ser "produto oficialmente suportado pelo Google". O `langextract.com` é um site **não oficial**
sobre a biblioteca. Função: extrair informação estruturada de texto longo com um LLM, a partir
de instruções e poucos exemplos, com **ancoragem na fonte** (cada extração aponta o trecho
exato do original).

### 7.2. O que ele faz bem — e por que importa aqui

| Recurso | Uso na anonimização |
|---|---|
| Poucos exemplos (few-shot) + instrução | Ensina o que é parte, sócio, advogado, juiz, órgão, sem treinar modelo. |
| Saída com esquema (controlled generation no Gemini) | JSON confiável; o modelo só pode responder `pessoa`/`empresa`/`endereco` com `papel` e `nome_canonico`. |
| Ancoragem com alinhamento aproximado | Nome com erro de OCR ("SCHNElDER") é ancorado; o trecho real vira variante para a busca. |
| Trechos + chamadas paralelas + passes múltiplos | Processo de milhares de folhas em minutos. |
| Provedores: Gemini API, **Vertex AI (com região)**, OpenAI, **Ollama (local)** | O mesmo código roda na nuvem institucional ou 100% local (peça curta, ou máquina com GPU). |
| Modo lote (Vertex/Gemini Batch) | ~50% mais barato para reprocessar acervos sem pressa. |
| Visualização HTML | Base para a tela de revisão do elenco (o HTML contém o original: só em `privados`). |

Detalhe relevante: o Presidio 2.2.364, **exatamente a versão fixada no `pyproject.toml`**, já
traz um `LangExtractRecognizer`. Mas o prompt dele é genérico (PII/PHI em inglês) e inclui
`DATE_TIME`, que o app veta. Por isso o protótipo usa o LangExtract diretamente, com prompt e
exemplos jurídicos em português.

### 7.3. Armadilhas encontradas no código-fonte (e como o protótipo as trata)

| Armadilha | Evidência | Tratamento no protótipo |
|---|---|---|
| **Uma ocorrência por extração.** O alinhamento liga cada extração a uma posição; se o nome aparece duas vezes no trecho e o modelo o lista uma vez, a segunda fica sem cobertura. | Reproduzido: "JOÃO DA SILVA" repetido no mesmo trecho, só a primeira ancorada. | LLM **descobre**; a **propagação determinística** acha todas as ocorrências. |
| **Descarte silencioso.** `extract()` usa `suppress_parse_errors=True`: trecho com JSON inválido vira lista vazia e só um *warning* no log. | `resolver.py`. | Invólucro próprio valida cada resposta, tenta de novo e **registra a falha** (fail-closed). |
| **Um trecho com erro derruba o documento inteiro** no modo paralelo (ex.: filtro de segurança do Gemini num trecho sobre crime). | `providers/gemini.py`: a exceção de um *future* aborta o lote. | O paralelismo fica no invólucro; cada trecho é isolado; filtros de segurança relaxados para texto jurídico. |
| Alinhamento parcial (`MATCH_LESSER`). | `resolver.py`. | A troca usa o texto, não o span parcial. |
| Trecho padrão de 1.000 caracteres. | O prompt fixo (~6 mil caracteres) dominaria o custo. | Trechos de 4.000 alinhados a página; cache por trecho. |

### 7.4. Veredito

**Adotar o LangExtract como motor de descoberta do elenco.** Ele substitui, com vantagem,
GLiNER + spaCy + heurísticas de capa + a maior parte das regras de descarte. Ele **não**
substitui: o Presidio para IDs (determinístico, com dígito verificador), a propagação, o mapa
reversível, a auditoria nem a decisão de política por papel.

Alternativas consideradas: SDK `google-genai` direto (menos dependência, mas recria-se
chunking, ancoragem e visualização); `LangExtractRecognizer` do Presidio (genérico demais);
Google Cloud Sensitive Data Protection/DLP (bom para IDs, fraco para nome em peça jurídica e
não classifica papel); LLM local via Ollama (mesmo código, mas em CPU é lento demais para
3.000 folhas).

---

## 8. Proposta: Anonimizador 3.0

### 8.1. Princípios

1. **O LLM classifica, o código troca.** O modelo nunca reescreve a peça: data, `R$`, tese,
   evento e órgão não passam pela geração de texto. Fidelidade garantida por construção.
2. **Elenco, não regex.** Num processo, as entidades sensíveis são poucas e repetidas. O
   elenco (quem é quem + papel) é do **processo inteiro**, não do arquivo: o sócio descoberto
   na PARTE_2 é trocado também na PARTE_1.
3. **Decisão por papel, não por palavra.** parte, sócio, advogado da parte, terceiro →
   troca; magistrado, servidor, procurador público, órgão, autor citado → fica (item H
   preservado, e reversível com um clique no elenco).
4. **Duas leituras.** Descoberta no texto com IDs mascarados; verificação no texto **já
   tokenizado** ("o que ainda identifica alguém aqui?").
5. **Fail-closed.** Página que nenhuma leitura conseguiu processar bloqueia a exportação.
6. **Revisão humana onde ela rende:** no elenco (dezenas de linhas), não nas folhas.

### 8.2. Fluxo

```
PDF / DOCX / TXT
  │  Extração fiel e rápida: PyMuPDF por página + âncora <!-- pagina-origem: N -->
  │  + marcadores do PDF como títulos (eventos) + OCR só em página-imagem
  ▼
IDs nesta máquina (Presidio: CPF, CNPJ, OAB, conta, placa…)  ── nada sai daqui
  ▼
Texto com IDs mascarados ──► Gemini via LangExtract (trechos de 4 mil, paralelo, cache)
  ▼
ELENCO: pessoa / empresa / endereço + papel + nome canônico + variantes (CAIXA ALTA, OCR, domínio)
  ▼
Tabela de papéis → o que troca e o que fica        (lista de alvos do usuário entra aqui)
  ▼
Propagação determinística: todas as ocorrências, sem diferenciar maiúsculas/acentos/quebra
de linha; sobrenome solto só onde o LLM ancorou (não come homônimo)
  ▼
Troca por token estável + mapa.json privado
  ▼
Gemini relê o texto JÁ tokenizado → o que sobrou entra no elenco → refaz a troca
  ▼
Fail-closed (página não lida bloqueia) · AUDITORIA.txt · revisão do elenco pelo usuário
  ▼
Exportável: .md tokenizado + ELENCO.md (token → tipo → papel, sem nome)
```

O `ELENCO.md` é uma melhoria de análise, não só de privacidade: o LLM de análise passa a saber
que `[EMPRESA_1]` é a executada e `[PESSOA_3]` é sócio, sem saber quem são. (Os tokens
continuam de identidade, não de papel — a recusa de `[EXECUTADA_1]` na governança continua
válida.)

### 8.3. O papel do Gemini, reconfigurado

| Antes (2.0) | Depois (3.0) |
|---|---|
| Operador abre o original no Gemini (Antigravity), manualmente, para caçar vazamento. | Gemini dentro do pipeline, pela API institucional (Vertex AI), com IDs já mascarados, em duas leituras automáticas e auditadas. |
| Conferência caso a caso, sem registro. | Elenco revisável + AUDITORIA + páginas não lidas bloqueando a exportação. |
| Antigravity no fluxo diário. | Antigravity como ambiente de desenvolvimento (e, se quiser, segunda opinião **só** sobre o Markdown tokenizado). |

### 8.4. O que sai do caminho padrão

GLiNER + torch + spaCy (gigabytes de instalação, minutos de CPU), Docling como extrator
padrão (fica como opção "tabelas difíceis"), heurística de capa, regras de descarte do §2 e a
geografia por regex. Ficam: Presidio para IDs, regras de fidelidade **de ID** (CNPJ público,
processo de precedente, NIRE), `KEEP_TERMS` (agora por igualdade, não prefixo), mapa,
auditoria, lista de alvos e base CNPJ (como sementes do elenco).

### 8.5. Tempo e custo estimados

Por chamada: prompt fixo ≈ 6 mil caracteres (~1,7 mil tokens) + trecho de 4 mil (~1,15 mil
tokens); resposta ≈ 250 tokens. Duas leituras (descoberta + verificação). Preços públicos de
setembro/2026, antes de desconto por cache implícito do prefixo.

| Processo | Trechos por leitura | Tempo LLM (16–32 em paralelo) | Gemini 3.1 Flash-Lite | Gemini 3.5 Flash |
|---|---|---|---|---|
| PARTE_1 (269 págs., 340 mil caracteres) | ~87 | 20–60 s | ~US$ 0,20 | ~US$ 0,60–1,10 |
| 3.000 págs. (4 milhões de caracteres) | ~1.023 | 3–8 min | ~US$ 2 | ~US$ 7–13 |

Somar ~20 s de processamento local para 3.000 páginas e o OCR das páginas-imagem (~2 s por
folha em CPU, pela medição da governança). Reexportação do mesmo processo com eventos novos
paga só as páginas novas (cache por trecho, imune à renumeração de tokens).

---

## 9. O que foi implementado neste ciclo

Tudo opcional e paralelo ao app atual: a tela, o `run_job` e as regras antigas não mudaram de
comportamento.

| Arquivo | O que é |
|---|---|
| `src/anonimizador_app/llm_discovery.py` | Adaptador LangExtract: prompt jurídico PT-BR, 4 exemplos fictícios (capa, petição, decisão com juíza e precedente, contrato social com OCR e domínio), trechos alinhados a página, invólucro fail-closed, cache por trecho, Gemini API / Vertex AI (São Paulo) / Ollama. Recusa chave ausente e explica por que não usar a gratuita. |
| `src/anonimizador_app/roster.py` | Elenco: uma entrada por entidade, papel normalizado, tabela de papéis, decisões do usuário preservadas (`elenco.json`). |
| `src/anonimizador_app/propagation.py` | Busca de todas as ocorrências indiferente a caixa, acento, apóstrofo e quebra de linha; sufixo societário; proteção contra homônimo; termos protegidos por igualdade. |
| `src/anonimizador_app/fast_extract.py` | PyMuPDF por página, âncoras, marcadores do PDF como títulos, OCR seletivo existente; DOCX com tabelas no lugar certo. |
| `src/anonimizador_app/pipeline_llm.py` | Orquestração em três fases (descoberta em todos os arquivos → verificação → troca final), fail-closed, AUDITORIA sem PII, `ELENCO.md`. |
| `tools/anonimizar_llm.py` | Linha de comando (ver anexo B). |
| `tools/sonda_vazamento.py` | Diagnóstico reproduzível do §2 (atual × proposto). |
| Núcleo (`detector.py`, `pseudonymizer.py`, `id_propagation.py`) e `target_list.py` | As cinco correções de desempenho do §4.2, sem mudança de resultado. |
| `extraction_cache.py` | Aceita outro extrator (`extract_fn`), com versão própria no cache. |
| Testes | 47 novos (elenco, propagação, adaptador LangExtract com modelo falso rodando a biblioteca real, pipeline, extração). Suíte total: 346, com as mesmas 2 falhas antigas específicas de Windows. |

---

## 10. Próximos passos

**Semana 1 — validar no lote real (o que não pude fazer daqui)**

1. Decidir o canal (Vertex AI institucional em `southamerica-east1`, ou Gemini API paga) com
   quem responde por dados na instituição, e confirmar que o modelo escolhido está disponível
   no endpoint regional (se não estiver, `--regiao` aceita outra região; avaliar o trade-off).
2. `pip install -e .[llm]` e rodar `tools/anonimizar_llm.py` no PARTE_1 (o mesmo do lote
   `222815`), com `gemini-3.1-flash-lite` e com `gemini-3.5-flash`.
3. Montar o gabarito mínimo (30–50 páginas anotadas localmente) e medir recall, precisão e
   preservação: atual × 3.0. Esse é o aceite — não a suíte verde.

**Semanas 2–3 — produto**

4. Tela: seletor de motor ("Gemini / local") e **tela de revisão do elenco** (tabela com
   tipo, papel, nº de ocorrências, página e um trecho curto de contexto; alternar
   trocar/manter) antes de gravar.
5. OCR de página-imagem pelo Gemini como opção para digitalizações ruins (o RapidOCR local
   continua como padrão).
6. Tirar GLiNER/spaCy/torch e Docling do caminho padrão (instalação cai de gigabytes para
   dezenas de megabytes).

**Se o app atual continuar em uso nesse meio-tempo (ganhos rápidos)**

- Já aplicado: as cinco correções de desempenho do §4.2 (mesma saída, 6 a 7× mais rápido nas
  regras).
- Geografia: só transformar "Evento N" em título quando ele iniciar a linha ou vier seguido de
  " - " e sigla de documento; nunca no meio da frase. *Já aplicado:* vira título só o
  cabeçalho colado (já marcado com `##`, depois de ponto/título/âncora, ou seguido de
  travessão sem preposição antes); "conforme o evento 12, …" fica na frase.
- `is_kept_term`: igualdade, não prefixo.
- Desligar para spans da lista de alvos e da capa as regras `_PUBLIC_ORG_MARKERS`,
  `is_pfn_officer` e `is_precedent_party`; restringir `is_pfn_officer` ao nome imediatamente
  seguido do cargo. *Já aplicado (parte):* pessoa listada com nome completo (2+ palavras)
  não cede mais ao `is_pfn_officer` dentro da lista de alvos — "A PGFN requer a citação do
  sócio FULANO" agora troca FULANO; variante parcial do nome continua cedendo ao procurador.
- `_read_docx`: parágrafos e tabelas na ordem do documento (já feito em `fast_extract.py`).

**Minuta de "Veredito 3.0" para o `GOVERNANCA.md`** (se aprovado)

> 3.0 (data). A premissa "local e sem LLM" cai: autos de milhares de folhas, digitalizados e em
> CAIXA ALTA não se anonimizam com NER genérico em CPU nem com regras sem contexto (sonda: 7 de
> 8 entidades vazaram com detector perfeito). O LLM entra **dentro** do app como detector e
> verificador, pelo canal institucional (Vertex AI, São Paulo), com IDs mascarados antes do
> envio; nunca reescreve a peça. Decisão por papel; propagação determinística; fail-closed;
> revisão do elenco. Aceite = gabarito com recall, precisão e preservação por tipo. Saem do
> padrão: GLiNER, spaCy, Docling, heurística de capa, regras de descarte sem contexto.
> Continuam: Presidio de IDs, KEEP_TERMS (igualdade), mapa privado, item H, veto de
> DATE/MONEY/LEGISLACAO. `.cursor/rules` atualizadas no mesmo passo.

---

## 11. Riscos e mitigações

| Risco | Mitigação |
|---|---|
| O LLM deixa passar um nome. | Propagação (basta ele achar uma vez no processo) + segunda leitura + lista de alvos + revisão do elenco + gabarito. |
| O LLM inventa um nome. | Só vale o que está no texto: a troca é por busca literal; nome inexistente não troca nada. Extração sem âncora é contada na auditoria. |
| O LLM marca tese, órgão ou data. | Tabela de papéis + termos protegidos por igualdade + a peça nunca é reescrita + revisão do elenco. |
| Homônimo (o sócio Souza × o ministro Souza). | Sobrenome solto só onde o LLM ancorou; nome composto propaga. |
| Filtro de segurança, erro de API, indisponibilidade. | Filtros relaxados para texto jurídico; nova tentativa; trecho isolado; página não lida bloqueia a exportação; cache. |
| Governança de dados. | Vertex AI institucional, região São Paulo, IDs mascarados antes do envio, nada de nível gratuito, mapa e elenco locais. |
| Dependência de fornecedor. | O LangExtract fala com Gemini, OpenAI e Ollama pelo mesmo código; elenco, propagação e troca não dependem do LLM. |
| Custo. | Flash-Lite como padrão; cache por trecho; reexportação paga só o novo. |

---

## Anexo A — Evidências reproduzíveis

```
python tools/sonda_vazamento.py          # §2: 7/8 no atual × 0/8 no proposto
python -m unittest discover -s tests     # suíte (PYTHONPATH=src)
```

Geografia (§3): `serialize_geography("Conforme o evento 12, a executada foi citada.")`.

## Anexo B — Como rodar o protótipo

```
pip install -e .[llm]

# Gemini API paga (chave de projeto com faturamento; a gratuita NÃO):
set GEMINI_API_KEY=...
python tools\anonimizar_llm.py autos_PARTE_1.pdf autos_PARTE_2.pdf

# Vertex AI (credencial: gcloud auth application-default login):
python tools\anonimizar_llm.py autos.pdf --provedor vertex --projeto MEU-PROJETO

# Depois de revisar o elenco (trocar "acao" para "manter" ou "tokenizar"):
python tools\anonimizar_llm.py autos.pdf --elenco saidas\privados\lote_...\elenco.json

# 100% local (peça curta):
python tools\anonimizar_llm.py peca.pdf --provedor ollama --modelo gemma3:12b
```

Opções úteis: `--modelo gemini-3.5-flash`, `--paralelo 32`, `--trecho 4000`,
`--verificacao 0|1|2`, `--sem-cache`, `--sem-legenda`, `--config` (lista de alvos e exclusões
do app).

## Anexo C — Medições de desempenho (PDF sintético de texto nativo)

| Cenário | Antes | Depois | Saída |
|---|---|---|---|
| App atual, regras + 43 alvos (com variantes de OCR), 300 págs. | 73,6 s | 11,7 s | idêntica byte a byte |
| App atual, regras + 120 alvos, 1.000 págs. | 688,8 s | 102,9 s | mesmas 18.000 trocas |
| Pipeline 3.0, parte local, 3.000 págs. (LLM simulado instantâneo) | 187,7 s | 18,7 s | 0 vazamentos, 45.000 trocas |

O tempo que sobra no app atual está nas outras heurísticas e na lista de alvos (proporcional
ao nº de alvos × tamanho do texto) — que a proposta 3.0 tira do caminho padrão. Sem GLiNER e
sem Docling nessas medições: eles somariam os ~14 min por PARTE_1 medidos na governança.
