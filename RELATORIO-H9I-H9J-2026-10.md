# Relatório da sessão — lote B (H9-i) e teste ponta a ponta com IA externa (H9-j)

**Período:** 16 a 19/10/2026 · **Repositório:** `rbizarello-lang/anonimizador-juridico` (privado) · **Pasta de trabalho:** `C:\Users\User\Documents\Anonimizador_Juridico`
**Branch com tudo isto:** `claude/h9j-teste-argon` (parte de `claude/ollama-local`) · **Versão final:** app 0.4.4, detecção 2.12.1 · **Suíte:** 1275 testes, todos passando.

Este documento resume tudo o que foi feito numa sessão longa de Claude Code, para a continuação em outra conta. Não traz nenhum dado de caso: só contagens, nomes de arquivo, decisões e comandos. Os relatórios com dados reais ficam **fora do git**, em pastas privadas do PC do Operador (seção 10).

---

## 1. Como a sessão começou

O Operador (Procurador da Fazenda Nacional) anexou o log de uma conversa com uma equipe de bots (Alfred, Hacker, Mike, Pesquisador, Anonimiza 3.0, Bot de ideias, Sherlock) sobre **IA local no anonimizador**: Ollama, Gemma 4 E4B e qwen3 numa RX 580 de 8 GB, testes de OCR por modelo de visão, canal `ollama-local`, e a regra de que **nada sai do PC antes de anonimizado**. O log terminava com um trabalho interrompido por limite de uso em outra conta: o **lote B** do H9-i (nove itens, mais verificação e tarefas fora do código), com um WIP guardado em `wip/h9i-b1-parcial` ("não usar").

Base de partida: `claude/ollama-local` no commit `94eb8c0` (lote A concluído, 1202 testes, Gemma como modelo padrão, qwen3:4b como reserva).

## 2. Fase 1 — lote B do H9-i (app 0.4.2, detecção 2.11.0)

Procedimento: criei `claude/h9i-lote-b`, apliquei o WIP, rodei a suíte (passava, mas sem testes próprios) e escrevi `tests/test_h9i_lote_b.py`. Achei e corrigi uma falha real (endereço listado não casava com "nº" antes do número).

| Item | O que ficou pronto |
|---|---|
| **B1** | Importador do formato combinado "Para o Anonimizador" do Nexus (abas Pessoas, Empresas, Processos, CDAs, Contatos, Veiculos), com **resumo antes de salvar** (quanto de cada tipo, linhas descartadas por motivo) e quatro caixas novas na aba Caso (Outras grafias, Processos, CDAs, Contatos e veículos). Papel do Nexus vira papel do elenco por tabela fixa e só de particular ("procurador" = advogado da parte). Lê também as colunas `nome_fantasia` e `apelidos`. |
| **B2** | Lista do caso como **chave única também no modo com IA** (`case_roster.py`): nome, grafias e frases da empresa viram variantes de UMA entrada; a menção do LLM e a do motor local que casam com a lista vão para ela **antes** da higiene e do juízo do lote; a lista vence a lista protegida. |
| **B3** | Processos, CDAs, contatos e veículos da lista trocados em todo o texto, sem rótulo (`case_list.py`, `ListaDoCaso`); raiz de CNPJ cobre todas as filiais; zero à esquerda comido pela planilha volta; NIRE listado. Depois, **índice de dígitos** no lugar de uma regex por valor (carteira inteira: 56 s → 1,8 s por 400 mil caracteres). |
| **B4** | Nome completo num token só (`name_join.py`): dois pedaços encostados viram um span; chave: mesma chave, depois lista do caso, depois mais palavras, empate à esquerda (regra escolhida pelo Operador). |
| **B5** | Julgador citado em ementa fica em claro (`is_precedent_judge`); nome da lista segue trocado. |
| **B6** | Reenvio do trecho que a IA devolveu vazio (versão inicial: mesmo texto; ver fase 4). |
| **B7** | A IA poder retirar nome achado só pelo detector de nomes (`llm_may_retract`, **desligado**, só conta na AUDITORIA). |
| **B8** | Gemma 4 E4B como modelo padrão (já estava no `94eb8c0`). |

**Medição no lote real do IDPJ** (só contagens, gabarito revisado, cache da Gemma, sem chamar o modelo): vazamento por categoria caiu (sócios 17→14, terceiros 18→15, endereços 16→9, CPF 6→4, CNPJ 3→1, CEP 8→6). A régua pegou uma **regressão minha**: a palavra do meio do nome de um servidor público (43 menções) era lida como grafia de OCR de uma pessoa da lista e trocada. Corrigi em duas etapas: o pedaço de uma palavra da lista que cai dentro do nome de pessoa "manter" não é trocado, **salvo** se a forma mantida contiver o nome inteiro da entrada da lista. O gabarito do IDPJ também foi atualizado (matrícula passou a "manter", com cópia de segurança).

Versão 0.4.2 gravada, GOVERNANCA atualizado.

## 3. Importante: o Nexus

Analisei o exportador do Nexus (`C:\Users\User\Documents\NEXUS CURSOR\src\lib\export-anonimizador.js`) contra o contrato `formato_exportacao_nexus.txt`. Está conforme. Pendência **no Nexus**: `papelPessoa` está como `TODO(human)` e devolve vazio, então a coluna `papel` da aba Pessoas sai vazia (não bloqueia). O recorte "Operação" basta para testes; a carteira inteira traz 1656 processos e 5375 CDAs.

## 4. Fase 2 — teste ponta a ponta com IA externa (16/10 à noite)

**O que o Operador fez:** baixou partes de um processo real, anonimizou com LLM local, anexou os exportáveis a uma IA externa (Gemini "Argon") que redigiu uma peça sobre o texto anonimizado, e na conta institucional entregou a peça, as chaves e os documentos a outro modelo (Gemini 3.8) para **desanonimizar**.

**Avaliação feita aqui** (leitura autorizada dos autos originais; quatro subagentes Sonnet de leitura, auditados por amostra):

| Eixo | Resultado |
|---|---|
| Rede/sigilo | 376 chamadas à Gemma, todas em 127.0.0.1; **0 tentativas de rede** |
| Estrutura | Íntegra: 130/130 âncoras, tabelas e títulos preservados |
| Vazamento | 75 ocorrências em claro (CDA, placas, chassi, conta, processos, sobrenomes soltos, nomes partidos, endereço parcial, registro civil de menores) |
| Falsos positivos | 17 claros (papel processual virando nome, servidor, bancos de extrato, processo em ementa) |
| Fragmentação | A empresa requerida em 6 a 9 tokens; uma devedora em 5 a 6 |
| Desanonimização | **Correta: 45/45 marcadores no lugar certo**, texto ao redor idêntico, nenhum token remanescente |
| Fatos da peça | 86 afirmações: 66 corretas, 9 imprecisas, 8 sem prova, 3 erradas. Os erros graves foram **extrapolação do redator** (inferiu parentesco e papel que o texto não dizia), não indução do exportado; a fragmentação agravou duas imprecisões |
| Jurídico | Pronta com ajustes (marcadores de pendência, sigilo nível 2, pedidos prematuros, fatos contraditos); sem as skills do plugin o resultado foi considerado muito bom |
| Tempo | **1h38** para 130 folhas |

**Causa-raiz principal dos vazamentos:** 45 das 130 folhas traziam ~29.800 caracteres U+200B (espaço de largura zero) vindos do PDF, **muitas vezes o único separador entre palavras** (medido: ~5 mil pontos). Regras com rótulo falhavam. (Mais tarde medimos que isto **não** explicava as respostas vazias da Gemma; ver fase 5.)

**Outro achado:** a lista do caso do Nexus não entrou na rodada porque o Operador trocou a pasta privada na tela e o app passou a ler o `config.json` da pasta nova, vazio, **sem avisar**.

**Sugestões do Argon (Gemini) sobre o ambiente, avaliadas:**
- Acolhida: lista do caso como prioridade sobre a higiene do LLM (já feito no B2); `think=False` e `temperature=0` (já eram assim).
- Acolhida em parte: tag sem visão do Gemma (medido: o modelo roda 100% na GPU, sem divisão; não trocar a tag); fusão por CPF adjacente (só em versão estrita e medida).
- Recusada: sobreposição entre trechos do LLM (os trechos já respeitam parágrafo e o cache seria invalidado).

## 5. Fase 3 — correções H9-j (app 0.4.3, detecção 2.12.0)

O Operador acolheu **integralmente** o plano de correções. Principais:

- **Invisíveis do PDF** (`invisible_chars.py`, aplicado em `load_or_extract`): U+200B/U+2060 viram **um espaço** (apagar colaria as palavras); ZWNJ, ZWJ, BOM e hífen invisível saem. Vale no cache novo e no já gravado, sem reextrair nem refazer OCR.
- **Classes novas/ampliadas:** CDA com espaços; processo com dígito verificador errado depois de "Execução Fiscal n."; processo da Justiça Federal anterior ao CNJ; matrícula de certidão do registro civil (`[REGISTRO_CIVIL_n]`, 32 dígitos); Mandado de Procedimento Fiscal como processo administrativo; URL com identificador numérico; chassi/placa/RENAVAM em pares rótulo | valor, com "nº" e em lista; conta e agência em comprovante em coluna.
- **Falsos positivos:** papel processual nunca é nome; nome de pessoa para na vírgula e na palavra minúscula; juízo do lote também sobre as entradas da verificação; código de servidor do eproc é pista de agente público (a OAB não); contato institucional de órgão público (e-mail `.jus.br`/`.gov.br`, telefone/CEP/endereço de vara) fica em claro.
- **Identidade:** mesmo CNPJ colado a duas razões sociais = mesma empresa (substitui o sufixo de grupo do E13 nesse caso); o ELENCO ganhou a seção "Pode ser a mesma pessoa".
- **Lista do caso:** levada à pasta privada nova ao trocar de pasta, e **ALERTA na AUDITORIA** quando a rodada roda com a lista vazia.
- **Reversão:** o mapa passa a trazer a grafia `preferida` de cada token (com acento); `tools/reverter_lote.py` reverte e **confere uma desanonimização feita por outra IA** (reproduziu a conferência manual: 45 ocorrências, 43 iguais ao mapa, 2 com a abreviação de banco desfeita).
- **Avisos do lote** (`lote_alerts.py`): folhas com marca de sigilo e eventos citados ("REFER. AO EVENTO") que não estão no lote, na AUDITORIA e no INDICE; e **`INSTRUCOES_IA.md`** no exportável (tokens, não deduzir parentesco ou papel, preservar a força da prova, lote parcial, sigilo).
- B6 desligado por padrão (29 reenvios, 0 recuperados).

## 6. Fase 4 — rodada 2 e sua avaliação (17 a 18/10)

O Operador rodou de novo o mesmo lote com a lista do caso ativa.

| | Rodada 1 | Rodada 2 |
|---|---|---|
| Tempo | 1h38 | **52 min** |
| Chamadas à Gemma | 376 | 219 (o ganho vem do **cache**: 111 trechos reaproveitados) |
| Vazamento da lista conhecida | 71 ocorrências | **~11 (−85%)** |

Itens: CDA 12→1, placa 16→1, chassi 4→0, conta 2→1, registro civil 2→0, nomes e sobrenomes soltos 19→0, processos 3→0, MPF 1→0, endereço 1→0, URL 2→1. Nenhum detector do app acha mais nada no exportado. Falsos positivos corrigidos: servidor do eproc, "Requerida", e-mails institucionais, frase engolida.

**Achados novos e ajustes (app 0.4.4, detecção 2.12.1):**
1. **A fusão por CNPJ não agiu no lote real** (erro meu): a fusão lê o texto já mascarado, onde o CNPJ é `[CNPJ_n]`. Agora o token vale o CNPJ do mapa e o nome pode vir antes ou depois do CNPJ.
2. Banco do extrato ainda trocado: o cabeçalho "Data/hora" vem numa linha de tabela com `|`.
3. "A REQUERIDA" (com artigo/"ora") é rótulo de papel.
4. Palavra única minúscula não é pessoa ("em branco") e **nenhuma troca dentro de comentário HTML**.
5. CDA partida por quebra de linha; placa com "O" lido como zero em lista; conta/agência repetidas por produto na mesma folha.
6. **Trecho vazio volta em duas metades** (`LlmConfig.retry_empty`, ligado; cache próprio por metade). Medido: as respostas vazias (32 de 99, os mesmos ~29 de antes) **não** eram causa dos invisíveis; reenviar o mesmo texto com temperatura 0 não adianta. **Ainda não medido** o efeito das metades.
7. LEIA-ME: parágrafo de grupo só quando há grupo, cita `reverter_lote.py`; a AUDITORIA diz "resposta VAZIA do LLM (o LLM não falhou)" em vez de "falha".

**Limites que ficam:** CNPJ da União sem máscara em claro (decisão de política); nomes de terceiros em ementa de precedente; razão social antiga e grafia errada do original; prenome com acento sozinho. Sem lista do caso (caso novo), as folhas de resposta vazia dependeriam só do detector de nomes.

## 7. Sugestões externas avaliadas

- **Sete sugestões de substância** (reversibilidade, perfil de caso persistente, réguas dentro do lote, cópia da config por lote, marca → empresas no ELENCO, cache de extração por folha, renomear `gemini_detector.py`): acolhidas total ou em parte, com ordem de execução (seção 9). Observação sobre reversibilidade: o mapa liga cada token a uma **lista** de grafias, então ele não reconstrói o texto **byte a byte**; por isso a reversão "semântica" foi feita (`reverter_lote.py`) e a exata ficou como decisão.
- **Etiqueta com papel dentro do token** (`[PESSOA_FISICA_01 - SÓCIO_EXECUTADO]`): **não recomendado.** O papel que temos vem da Gemma e é instável (a mesma pessoa aparece como "parte, sócio, terceiro"); foi o que mais induziu erro no Argon; muda no tempo e entre peças; e token curto e opaco é mais seguro para a reversão. Alternativa: papel no ELENCO, marcado como **confirmado** (capa) ou **sugerido** (palpite da Gemma).

## 8. Hardware: parecer da placa de vídeo (sessão paralela)

Uma sessão separada pesquisou RTX 5060 Ti 16 GB × RTX 3090 24 GB (usada ou importada), PC com Ryzen 5 4500, 24 GB de RAM e RX 580 8 GB. Conclusão registrada no branch `claude/youthful-tesla-664011` (`pesquisas/parecer_placa_video_2026-10.pdf` e `.html`, `pesquisas/placa_video_2026-10.md`):
- A R$ 7 mil a 3090 **não compensa**; passaria a valer até cerca de R$ 6 mil e testada antes de pagar (as comuns usadas apareciam entre R$ 4.800 e R$ 6.000 no Mercado Livre).
- O modelo de nota ~40 citado nos posts (Qwen3.8-Flash-Next, ~41,5 GiB comprimido) não cabe inteiro nos 24 GB; o resto iria para a RAM do PC.
- **Recomendação final: RTX 5060 Ti 16 GB + um pente de 16 GB de RAM no lugar do de 8 GB (32 GB).**
- Estimativa (não medida): lote de 130 folhas em ~15–25 min com a 5060 Ti (hoje 52 min com cache, 1h38 sem).

## 9. Tarefas pendentes (consolidado)

**Rodada 3 (próximo passo imediato):** reabrir o app (cabeçalho **v0.4.4**), conferir que a lista do caso segue ativa na AUDITORIA, rodar o mesmo lote e medir contra as rodadas 1 e 2 (vazamento, falsos positivos, fragmentação, respostas vazias com as metades, tempo). Os scripts de medição estão descritos na seção 11.

**Pequenas:** cópia do `config.json` e das versões na pasta privada de cada lote; linha "marca → empresas" no ELENCO; marcar no ELENCO o papel como confirmado × sugerido.
**Médias:** réguas leves dentro do lote (opção desligada); reversão exata (mapa por ocorrência, mais sensível: só se o Operador quiser).
**Grandes (ciclo próprio, com decisão do Operador):** perfil de caso persistente e numeração estável de tokens entre lotes (`casos/<id>/elenco_caso.json`; GOVERNANCA 8.6-F).
**Decisões do Operador:** nomes "agente público?" que o gabarito manda trocar e o elenco mantém; quando ligar `llm_may_retract`; **mesclar o branch no `main`**; política do CNPJ da União e de contatos institucionais; baixar ou não o qwen3.5:4b; o que fazer com `tmp_dbg.py` e a pasta `Presidio/`, soltos no repositório (não foram tocados).
**No Nexus:** implementar `papelPessoa`.
**Folga:** renomear `gemini_detector.py` → `protected_terms.py` (depois de mesclar, com módulo de compatibilidade); cache de extração por folha (medir antes); pesquisa dirigida das listas de palavras comuns e de sistemas protegidos; OCR de imagens dentro de Word.
**Compra:** decidir a placa (seção 8).

## 10. Para continuar em outra conta

**Ambiente:** Windows 11, PowerShell/Git Bash, Python do `.venv` do projeto. Rodar a suíte:

```bash
cd "C:/Users/User/Documents/Anonimizador_Juridico"
PYTHONPATH="src;tests" .venv/Scripts/python.exe -m unittest discover -s tests
```

As linhas `ERROR ... OCR falhou na pagina ... misto.pdf` no meio da saída são de testes que simulam falha de propósito; o que vale é `Ran N tests ... OK`.

**Fora do git (privado, só no PC do Operador):**
- `saidas\privados\` (mapas, caches, `config.json` com a **lista do caso**, `gabaritos\` do IDPJ, `regras\` com o contrato do Nexus e a lista de tarefas).
- `Desktop\Teste anonimiza\` (PDF do teste, `config.json` próprio, `exportaveis\` e `privados\` das rodadas 1 e 2, `privados\RELATORIO_TESTE_ARGON_2026-10-17.md`, `privados\RELATORIO_RODADA2_2026-10-18.md` e `privados\avaliacao_subagentes\` com os quatro relatórios).
- **A lista do caso é por pasta privada:** o `config.json` de cada pasta tem a sua. O app agora leva a lista ao trocar de pasta, mas confira a linha "Lista do caso usada" na AUDITORIA.

**Armadilhas desta sessão (úteis a quem continuar):**
- Heredocs com `\n`, `\W`, `\d` dentro de strings Python no shell viraram quebras de linha ou foram mal escapados várias vezes: prefira a ferramenta de edição de arquivos, ou `chr(10)`/`chr(92)` em scripts curtos.
- Os testes de pipeline rodam com texto **já mascarado** (CNPJ como `[CNPJ_n]`): o teste da fusão por CNPJ precisou ser refeito por isso.
- `pytest` não está instalado; usa-se `unittest`.
- Reaplicação do cache do lote real: `tools\reaplicar_cache.py <PDFs> --modelo gemma4:e4b` (sem rede, sem chamar o modelo, ~25 a 35 min) e a régua `tools\medir_vazamento.py <gabarito> <lote A> <lote B>`.

**Higiene de dados neste repositório:** antes de subir, os valores reais que apareceram em comentários e testes (placas, chassi, CDA, processos, conta, nomes) foram trocados por valores inventados, e o histórico das 20 etapas foi condensado num commit só. Os commits originais ficam num branch **local** (`backup/h9j-antes-da-limpeza`) que **não** foi enviado ao GitHub porque contém esses valores.

## 11. Mapa de arquivos novos ou muito alterados

| Arquivo | Para quê |
|---|---|
| `src/anonimizador_app/case_list.py`, `case_roster.py` | Regras e entradas da lista do caso (B1–B3) |
| `src/anonimizador_app/spreadsheet_targets.py` | Importador do Nexus, resumo antes de salvar |
| `src/anonimizador_app/name_join.py` | Nome completo num token (B4) |
| `src/anonimizador_app/invisible_chars.py` | Invisíveis do PDF |
| `src/anonimizador_app/lote_alerts.py` | Sigilo, eventos ausentes, `INSTRUCOES_IA.md` |
| `src/anonimizador_app/roster_identity.py` | Fusão por CNPJ (texto mascarado) |
| `src/anonimizador_app/llm_discovery.py` | Trecho vazio em duas metades (`split_in_halves`) |
| `src/anonimizador_app/table_labels.py`, `src/anonimizador_juridico/recognizers.py` | Placa, chassi, conta, CDA, processo antigo, registro civil, MPF, URL |
| `src/anonimizador_app/settings.py` | Lista do caso por pasta, `llm_may_retract` |
| `tools/reverter_lote.py` | Reverte e confere desanonimização |
| `tests/test_h9i_lote_b.py`, `tests/test_h9j_teste_argon.py` | Testes novos |
| `GOVERNANCA.md` | Entradas H9-i lote B e H9-j (inclui a rodada 2) |
| `RELATORIO-H9I-H9J-2026-10.md` | Este relatório |
