# Anonimizador Jurídico — panorama do projeto (05/10/2026)

**Para quem:** Roger (Operador) e qualquer leitor, pessoa ou IA, que precise entender o estado do
projeto sem ter acompanhado as conversas.
**Base:** repositório privado `rbizarello-lang/anonimizador-juridico`, `main` em `6811a42`, mais o
PR #5 (aberto, CI verde). Nenhum dado real de caso neste documento.
**Documentos de referência:** `GOVERNANCA.md` (mapa oficial; prevalece em caso de conflito),
`AVALIACAO-2026-09.md` (avaliação e proposta 3.0), `README.md` (uso).

---

## 1. Resumo em um minuto

- O app troca identificadores de peças jurídicas por tokens (`[PESSOA_1]`, `[CPF_2]`…) e guarda o
  mapa reversível só nesta máquina. Datas, valores, eventos, teses e órgãos ficam no texto.
- Há **dois motores de descoberta de nomes**, no mesmo app:
  - **motor local (versão 2.x)**: GLiNER + regras, 100% nesta máquina. É o padrão e o único
    validado em lote real;
  - **motor LLM (versão 3.0)**: Gemini via LangExtract, com CPF/CNPJ mascarados antes do envio e o
    motor local rodando junto como "piso". Está **pronto no código, mas nunca foi rodado contra o
    provedor real**.
- Nesta rodada de trabalho (04 e 05/10) foram feitos:
  - correção do piso local do modo LLM (H4);
  - CI no Windows;
  - **pipeline único**, para que a única diferença entre os dois motores seja a detecção (H5,
    PR #5).
- **O próximo passo depende de você:** mesclar o PR #5 e rodar a ferramenta de comparação no lote
  de 25/09 (seção 5, passo A). Depois disso vêm a escolha do canal do LLM e a medição com o
  gabarito.

---

## 2. As duas versões

| | **Motor local (2.x)** | **Motor LLM (3.0)** |
|---|---|---|
| Quem acha os nomes | GLiNER (spaCy de reserva), lista de alvos, base CNPJ, heurísticas de capa, eproc, tabela, endereço | Gemini lê o texto (com CPF/CNPJ já mascarados) e aponta quem é quem e o papel de cada um; o motor local roda junto como "piso" |
| O que sai da máquina | Nada | O texto da peça com os IDs mascarados: **nomes, endereços e fatos vão em claro ao provedor** |
| Quem troca o texto | O código, de forma determinística | O código, de forma determinística (o LLM nunca reescreve a peça) |
| Estado | Em uso; validado no lote eproc de 25/09 (gabarito 52 de 52; 698/698 âncoras de folha) | Implementado e testado com provedor simulado; **nenhuma chamada real feita** |
| Como se escolhe | Padrão: o app sempre abre nele | Na aba **Lote**, "Motor de descoberta" → LLM → "Configurar LLM…"; vale só até fechar o app; cada rodada pede confirmação |
| Instalação | `pip install -e .` | `pip install -e .[llm]` (LangExtract, google-genai, keyring) |
| Proteções | Regras de fidelidade (na dúvida, o texto fica) | As do local, mais: confirmação a cada rodada (canal, arquivos, caracteres); chave só no Gerenciador de Credenciais do Windows; folha que o LLM não leu passa pelo motor local ou fica em quarentena; segunda leitura do texto já tokenizado |

**O "piso local"** (ligado por padrão no modo LLM): o motor local roda em todas as folhas e o
resultado é a soma do que os dois acharam; o LLM só acrescenta proteção, nunca tira. O custo é que
os falsos positivos do GLiNER também entram, e por isso o C3 deve medir o LLM **com e sem piso**.

**O que cada versão precisa para ser usada com segurança**
- Local: nada além do que já existe.
- LLM: canal aprovado institucionalmente (seção 6) e conta com faturamento. Chave gratuita **não**
  serve: os termos gratuitos permitem uso do conteúdo pelo provedor, e há sigilo fiscal (CTN,
  art. 198).

---

## 3. O que foi feito

### 3.1. Até 01/10 (já estava no PR #2, mesclado em 04/10)

- **Fila 3.x "estrutura primeiro" (E1–E14):**
  - token que não come data ou valor;
  - folha de separação do eproc legível;
  - `INDICE.md` e `ELENCO.md` do lote;
  - OCR que não funde linhas;
  - âncora visível por folha `[fl. N · Evento E · CÓDIGO · p. P]`;
  - rua com nome de pessoa tratada como endereço;
  - banco do SISBAJUD em claro;
  - marca de empresa usada sozinha;
  - grupo de empresas ligadas `[EMPRESA_3·G1]`.
- **Motor LLM (L1–L3, H2, H3):**
  - canais `gemini-pago` e `vertex-sp`;
  - tela de escolha e de revisão do elenco;
  - confirmação por rodada;
  - fallback local e quarentena;
  - piso local;
  - paralelismo 4;
  - ensaio sem rede (`tools\ensaio_llm.py`).
- Limpeza dos identificadores do caso antigo nos arquivos atuais. **No histórico do git eles
  continuam** (seção 6).

### 3.2. Nesta rodada (04–05/10)

| Item | O que foi feito | Onde |
|---|---|---|
| Parecer de arquitetura | Um advisor dedicado (Fable 5.1) revisou o desenho dos dois motores e apontou: (a) o piso do LLM recebia o GLiNER com menos filtros que o próprio modo local; (b) PR grande sem CI; (c) os dois pipelines divergiam depois da detecção, então comparar os dois mediria pipeline e detector misturados | — |
| **H4 — piso local com o juízo do lote** | No modo LLM, o que o GLiNER acha passa pelo mesmo filtro do modo local: procurador e juiz ficam em claro, banco do SISBAJUD fica em claro, rua com nome de pessoa vira endereço. No código anterior, 4 de 5 testes novos falhavam | PR #3 (mesclado) |
| `cache_llm` documentado | As respostas do provedor ficam em `saidas\privados\cache_llm`, com nomes em claro; README e LEIA-ME passam a dizer que essa pasta é tão sensível quanto o `mapa.json` | PR #3 |
| **CI** | `.github/workflows/testes.yml`: a suíte inteira roda no Windows com Python 3.14 a cada PR e a cada push na `main` | PR #2/#4 (mesclados) |
| Teste do ícone | O único teste que falhava no CI dependia de uma pasta da sua máquina; agora usa pasta temporária | PR #4 (mesclado) |
| **H5 — pipeline único (1ª etapa)** | O modo local passa a poder rodar pelo mesmo pipeline do LLM, sem LLM (`run_local_job`), e as regras de lote do local (marca de uma palavra, IDs já vistos no lote, revisão residual) passam a valer também no modo LLM. Ferramenta de aceite `tools\comparar_pipelines.py`. **A tela ainda usa o motor local de antes** | **PR #5 (aberto, CI verde)** |

**Números de agora:**
- Suíte: 761 testes passam no Linux, com 4 falhas que são só de ambiente (exigem Windows ou
  torch); no Windows, pelo CI, passa inteira.
- Versões: app 0.4.1; detecção 2.8.2 na `main` e 2.8.3 com o PR #5.

---

## 4. O que falta, em ordem

| # | Etapa | Quem | Depende de |
|---|---|---|---|
| A | Mesclar o PR #5 e rodar `comparar_pipelines.py` no lote de 25/09 | **Você** | — |
| B | Passo 4 do H5: a tela passa a usar o pipeline único no modo local | Claude | A dar "iguais" |
| C | Decidir o canal do LLM (Vertex AI em São Paulo ou Gemini API paga) | **Você + encarregado de dados** | — (pode correr em paralelo a A) |
| D | Guardar a chave e fazer o ensaio e a fumaça em documento sintético (L4, passos 1–2) | **Você** | C |
| E | Rodar um documento real curto pelo LLM, com revisão do elenco (L4, passo 3) | **Você** | D |
| F | C3: o lote de 25/09 pelo LLM, **com e sem piso**, medido com gabarito e régua de estrutura (L4, passo 4) | **Você** roda; Claude ajuda a ler as contagens | B e E |
| G | Veredito 3.0 no `GOVERNANCA.md`: o LLM entra ou não como motor do app, e com ou sem piso | **Você** decide; Claude redige | F |
| H | Expurgo do histórico do git (identificadores reais do caso antigo) | **Você** decide; Claude executa | — (quando quiser) |

Critérios de aborto do C3 (qualquer um encerra o desenho do LLM): estão no `GOVERNANCA.md`, §9,
"L4 — critérios de aborto". Os principais:
- token em órgão, `R$`, data, comarca, evento ou termo jurídico;
- União ou PGFN virando `[EMPRESA_n]`;
- juiz ou procurador tokenizado;
- entidade do gabarito com **mais** menções em claro que no lote local;
- regressão da régua de estrutura;
- chave ou nome em claro em arquivo gerado;
- folha não lida sem registro.

---

## 5. O que depende de você, e como fazer

Todos os comandos abaixo são para o **PowerShell, aberto na pasta do projeto** (a pasta que tem
`app.py` e `iniciar_app.vbs`). Onde aparece `python`, use o Python do ambiente do projeto:
`.venv\Scripts\python.exe`.

### Passo A — mesclar o PR #5 e comparar os dois caminhos (agora)

1. **Mesclar:** no GitHub, abra o PR #5 ("H5: pipeline único…") e clique em *Merge pull request*.
   O CI já está verde.
2. **Atualizar a cópia local:**
   ```
   git checkout main
   git pull
   .venv\Scripts\python.exe -m pip install -e .[llm]
   ```
3. *(Opcional, recomendado)* **Rodar a suíte na sua máquina:**
   ```
   .venv\Scripts\python.exe -m pip install pytest
   .venv\Scripts\python.exe -m pytest -q
   ```
   Esperado: tudo passa (no CI do Windows passa inteira).
4. **Comparar** (usa o GLiNER real e o cache que você já tem; o OCR não roda duas vezes):
   ```
   .venv\Scripts\python.exe tools\comparar_pipelines.py "C:\caminho\da\pasta\com\os\PDFs do lote de 25-09"
   ```
5. **Ler o resultado** na última linha da tela:
   - `RESULTADO: iguais` → me avise; eu faço a etapa B.
   - `HA DIFERENCA` → me passe só a tabela da tela (arquivo, linhas, diferentes, reagrupados).
     **Não me envie o `DIFERENCAS.txt`**: ele fica em
     `saidas\privados\comparacao_...\DIFERENCAS.txt` e pode ter nome em claro.
6. A comparação grava dois lotes de teste (pastas `comparacao_..._atual` e `_unico` em
   `saidas\privados`). Depois de conferir, você pode apagá-los.

### Passo C — decidir o canal do LLM (pode ser em paralelo)

- **Vertex AI, região `southamerica-east1` (São Paulo):** dados processados no Brasil e contrato
  institucional do Google Cloud. É a opção recomendada pela avaliação de setembro.
- **Gemini API paga** (chave de projeto com faturamento): mais simples, mas **não garante
  residência dos dados no Brasil**.
- Leve ao encarregado de dados: o texto vai ao provedor com nomes e fatos em claro, e só CPF/CNPJ e
  afins são mascarados antes. A decisão é institucional, não técnica. Registre-a no
  `GOVERNANCA.md` (eu redijo se você me disser a decisão).

### Passo D — chave, ensaio e fumaça (depois de C)

1. Confirme no Google Cloud que **o projeto da chave tem faturamento ativo**. O app não consegue
   verificar isso.
2. **Guarde a chave** de um destes jeitos. **Nunca cole a chave no chat, em arquivo ou na linha de
   comando:**
   - pela tela: aba **Lote** → "Motor de descoberta" → LLM → **Configurar LLM…** → campo de senha;
   - ou pelo terminal: `.venv\Scripts\python.exe tools\anonimizar_llm.py --guardar-chave` (ele
     pede a chave sem mostrá-la).
   - No Vertex: `--guardar-vertex`, com projeto e região.
3. Rode o ensaio sem rede, que deve terminar com todas as conferências passando:
   ```
   .venv\Scripts\python.exe tools\ensaio_llm.py
   ```
4. Verifique o canal; o esperado é "LIBERADO":
   ```
   .venv\Scripts\python.exe tools\anonimizar_llm.py --verificar --canal gemini-pago
   ```
5. **Fumaça:** um documento **inventado**, de uma ou duas folhas, sem dado real:
   ```
   .venv\Scripts\python.exe tools\anonimizar_llm.py teste_sintetico.pdf --canal gemini-pago --paralelo 2 --confirmo-envio
   ```
   Confira:
   - a AUDITORIA diz "motor LLM: gemini-pago" e mostra **0 falhas por trecho**;
   - a pasta `saidas\privados\cache_llm` foi criada;
   - repetir com `--sem-rede` reaproveita tudo do cache, sem nova chamada.

### Passo E — um documento real curto (depois de D)

Escolha uma peça de poucas folhas e rode **pela tela**, com o motor LLM. Leia a confirmação
(canal, arquivos, caracteres) antes de aceitar. Depois:
- revise o elenco na tela: quem foi mantido em claro e quem foi trocado;
- corrija o que estiver errado (trocar ↔ manter) e regrave sem rede;
- leia o texto final.

### Passo F — o C3 (depois de B e E)

O lote de 25/09 pelo motor LLM duas vezes: com o piso ligado (padrão) e com o piso desligado
(interruptor na aba **Regras**, ou `--sem-piso-local` na linha de comando). Para cada lote LLM,
rode:
```
.venv\Scripts\python.exe tools\medir_estrutura.py "<pasta dos PDFs>" "<pasta exportável do lote LLM>"
.venv\Scripts\python.exe tools\medir_vazamento.py saidas\privados\gabaritos\caso_auca_2026-09.json "<último lote local>" "<lote LLM>"
```
As duas ferramentas imprimem só contagens e rótulos, sem nomes: essa saída pode ser compartilhada
comigo. Compare com os critérios de aborto (seção 4).

### Decisão H — histórico do git (quando quiser)

O repositório é **privado**, mas os identificadores reais do caso antigo seguem no histórico (desde
o primeiro commit). As opções são:
- reescrever o histórico (`git filter-repo`): exige que todas as cópias locais sejam clonadas de
  novo;
- criar um repositório novo a partir do estado atual.

Diga qual prefere e eu preparo os passos.

---

## 6. Cuidados com dados (valem para tudo acima)

| Arquivo ou pasta | Contém | Pode sair da máquina? |
|---|---|---|
| `saidas\exportaveis\lote_...\*.md`, `AUDITORIA.txt`, `ELENCO.md`, `INDICE.md` | Texto tokenizado; contagens | Sim (é o produto) |
| `saidas\privados\lote_...\mapa.json` | A chave dos tokens | **Não** |
| `saidas\privados\lote_...\elenco.json`, `AUDITORIA_PRIVADA.txt` | Nomes reais | **Não** |
| `saidas\privados\cache_llm`, `cache_extracao`, `cache_ner` | Respostas do provedor e texto extraído, em claro | **Não** |
| `saidas\privados\comparacao_...\DIFERENCAS.txt` | Linhas que mudaram, possivelmente com nomes | **Não** |
| `saidas\privados\gabaritos\` | Gabarito com nomes reais | **Não** |

---

## 7. Onde está cada coisa

| O quê | Onde |
|---|---|
| Decisões, métricas, vereditos e roteiro do L4 | `GOVERNANCA.md` (§9: fila 3.x, H3, H4, H5, L4) |
| Avaliação de setembro e proposta 3.0 | `AVALIACAO-2026-09.md` |
| Uso do app e do motor LLM | `README.md` |
| Pipeline do motor local | `src/anonimizador_app/pipeline.py` |
| Pipeline do motor LLM e pipeline único | `src/anonimizador_app/pipeline_llm.py` |
| Elenco do lote (juízo de procurador, banco, rua, marca) | `src/anonimizador_app/batch_propagation.py` |
| Ferramentas de medição | `tools/comparar_pipelines.py`, `tools/medir_estrutura.py`, `tools/medir_vazamento.py`, `tools/ensaio_llm.py` |
| Testes | `tests/` (CI: `.github/workflows/testes.yml`) |
