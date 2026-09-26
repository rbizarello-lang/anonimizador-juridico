# Governança do desenvolvimento — Anonimizador Jurídico

**Versão do app:** 0.4.0 (indicador na janela e em cada lote)  
**Data deste mapa:** 29/08/2026  
**Atualizado:** 26/09/2026 (Veredito 3.0-E: PyMuPDF por folha como extrator padrão; Veredito 2.1: propagação do lote; aceite = lote eproc de 25/09).  
**Para quem:** Roger (uso do app) e quem voltar a este projeto daqui a semanas ou meses.  
**O que este arquivo é:** o mapa único do produto — o que se quis, o que se mediu, o que se fez, o que se recusou e **em que condição** cada recusa pode ser revista.  
**O que este arquivo não é:** ordem de código, manual do usuário, nem ata de um lote específico.

Se houver conflito entre um relatório de pesquisa e este mapa, **este mapa prevalece** até ser atualizado. Relatórios externos são catálogo e insumo; não são plano de sprint. O catálogo amplo da pesquisa de 29/08/2026 era o **entregável pedido**, não um defeito de diagnóstico.

---

## Veredito travado (29/08/2026, noite; retificado no mesmo dia)

Pesquisa ranqueou por recall; o app corta por fidelidade. Juntar heurística de capa + checkpoint novo + limiar 0,60 no mesmo passo misturaria duas variáveis. GLiNER atual em **0,75**. Um fator por vez.

### Veredito 2.0 (19/09/2026, noite)

O 1.0 assumiu que o residual **só avisa**. Por isso a passada local maximizou recall (partícula de razão, GLiNER no texto inteiro, lista acumulada) e abortou em P13: `[EMPRESA]` 169→482 no mesmo PARTE_1; CNAE/CTN art. 133 comidos; União/`R$`/SisbaJud intactos.

O 2.0 inverte o corte, **sem** baixar limiar. O app **não** chama Gemini.

| Eixo | O que vale no 2.0 |
|---|---|
| **Papel local** | Precisão e estrutura. IDs com DV, lista **deste** feito, capa, KEEP_TERMS. Empresa **não** gera partícula de 1 palavra. |
| **Papel Gemini** | **Fora do app.** O operador lê o original **e** o Markdown tokenizado no Gemini (ou outro destino). Confronta vazamento e estrutura lá. O app não envia arquivo, não guarda chave, não reconstrói texto. |
| **Motor interno** | `GeminiEngine` / checkbox / chave de API: **saiu** (20/09/2026). KEEP_TERMS e `is_kept_term` permanecem (hoje vivem no mesmo módulo). |
| **Afrouxar** | Menos gula local (melhor peça para essa leitura externa). Não é limiar 0,3, não é regex sem rótulo. |
| **Fidelidade** | Continua cortando no app. O mapa privado **não** vai para o Gemini. |
| **Fila** | §9 e §14. 2.0-B = tirar o motor interno. LeNER/`arthrod`/sombra/Marker caem. P3/P5 sobem. |

O parecer interno da mesma noite (anonimização + estrutura) foi **aceito e reavaliado**: o teto de “modelo melhor” é baixo; o ganho está em **dado determinístico + propagação + medição**. A lista de alvos manual (pedido do Roger) entra como primeira camada determinística; a base CNPJ, anual e recortada ao Sul, como segunda. O app abre em **janela própria** (`iniciar_app.vbs` / atalho), com link local `http://127.0.0.1:8765`.

| Eixo | O que vale |
|---|---|
| **Docling** | ~~Permanece o default.~~ **Superado em 26/09/2026 (Veredito 3.0-E, abaixo):** o default passa a ser o PyMuPDF por folha; o Docling fica como caminho legado. Marker, MinerU e `pymupdf4llm` continuam **fora**. |
| **Métrica 1** | “Não mexer no motor” = **não trocar o Docling**. Não é “não há o que fazer”: Markdown é export lossy; eventos fundidos ainda sangram (**P3**). CDA achatada também sangra (**P4**), mas **não gasta ciclo**. |
| **Folga de engenharia** | Serialização conservadora, âncora de página/evento, auditor PyMuPDF × Docling — **sem contaminar** o piloto de capa (heurística, GLiNER e limiar intocados). |
| **Sequência** | Capa (medir) → instrumento de medição / auto-auditoria → geografia P3 → camada determinística (lista de alvos **já no app**; CNPJ Sul em seguida; depois Aho-Corasick/RapidFuzz) → passada-sombra de caixa **antes** do LeNER → LeNER / `arthrod` / fine-tune só se o determinístico não bastar. |
| **Fora deste ciclo** | LLM local como motor, GLiNER2, Presidio-com-transformer, Hyperscan, embeddings para PII, VLM de página, perturbação de nomes. `rafola`/`ottema` fora **deste ciclo**, não perpétuo. |
| **Pergunta de produto em aberto** | Tokenizar juiz/servidor em “Documento eletrônico assinado por…”? Hoje **não**. O §10 aborta se a heurística de capa pegar magistrado. Só muda com decisão expressa neste arquivo. |

Adiar o `arthrod` **não** é “já temos GLiNER”: continua o melhor candidato isolado da mesma família. Fine-tune adiado está certo; o teto no corpo eproc continua sendo esse caminho. O corpus dourado, quando existir, serve à medição **e** ao fine-tune.

### Veredito 3.0-E — extração (26/09/2026)

Medido no lote eproc de 25/09/2026 (IDPJ + execução fiscal de origem; 11 PDFs, 698 folhas), com o texto de cada folha tirado do próprio PDF e o carimbo eproc (“Evento X, DOC, Página Y”) como chave de conferência. Sem PII nesta ata.

| Falha do caminho Docling | Medida no lote |
|---|---|
| O teto de 900 s devolve documento **parcial** (não é exceção; a reserva PyMuPDF não entra) | Execução fiscal de 251 folhas: saída termina na folha 45 |
| Folha com o texto **desenhado em vetor** (sem camada de texto, sem imagem) não aciona OCR | 104 folhas: contratos sociais, alterações contratuais, relatórios SERPRO |
| Âncora numerada por **contagem de quebras**: folha sem item no corpo não gera quebra | Folhas nativas sob a âncora errada: 80 de 83 num PARTE, 73 noutro (deslocamento de até 34) |
| Âncora deslocada engana o OCR (“a folha já tem texto”) | 11 folhas escaneadas não lidas (matrículas, prints) |
| OCR sem âncora descartado **e contado como lido** | 2 folhas (a AUDITORIA dizia “lidas por OCR”) |
| Carimbo eproc descartado como cabeçalho | Todas as folhas perdem a chave de citação |
| Regex de geografia | 28 títulos “## Evento 1” falsos partindo o carimbo lido por OCR |
| **Total** | **289 de 698 folhas (41%) sem conteúdo no exportável**; ~66 min de lote |

**Decisão.** O extrator padrão passa a ser o PyMuPDF por folha (`fast_extract`):

1. âncora = folha física do PDF, sempre; marcadores do PDF (evento, documento, página) viram títulos; carimbo eproc preservado;
2. OCR seletivo ampliado a três casos: **escaneada** (como antes), **vetorial** (folha só com carimbo e ≥ 50 caminhos desenhados) e **fonte quebrada** (> 2% de caracteres improváveis na camada de texto; o OCR **substitui** a camada ilegível). Página nativa legível continua sem OCR (§8.3);
3. tabela com grade que cobre a folha vira tabela Markdown no lugar dela (`find_tables`); folha de colunas sem grade (SISBAJUD, débitos da CDA, listagem de eventos) sai com as colunas alinhadas num bloco `text`; folha com camadas de texto **sobrepostas** (várias folhas impressas numa só, como no rastreador CNIS) vira uma tabela por camada, na ordem do PDF (o cabeçalho de cada camada define as colunas; a célula quebrada volta a ficar inteira). Tabela e colunas só entram se nenhuma palavra da folha se perder nem se duplicar;
4. cobertura no topo de cada Markdown (nativas / OCR / em branco / não lidas). Folha não lida fica marcada no lugar dela, aparece na AUDITORIA e **bloqueia o envio**;
5. geografia sem regex no corpo: os eventos vêm dos marcadores do PDF. A regex de “evento descolado” fica só no caminho legado, restrita a cabeçalho desde 25/09 (commit 7e12ce0): “conforme o evento 12, …” não vira mais título.

O Docling fica como caminho legado (`load_or_extract(prefer_docling=True)`), sem entrada na interface.

**Respostas do §8.8.**
1. *Problema medido:* P3 (geografia/âncora) e P5 (tempo e tamanho), no lote acima.
2. *Métrica 1:* a troca é só de extração; nada novo vira token por ela. Vale o aborto do §10.2.
3. *Um fator:* o extrator (OCR e tabela são parte dele). GLiNER, limiar 0,75, lista de alvos, regras e política: intocados.
4. *Aborto:* token novo em órgão, `R$`, data, evento ou sistema nas mesmas folhas; folha nativa com menos palavras que o PDF; folha não lida sem motivo no lote real.
5. *Condição de reabertura:* “Timeout / fatiamento (P5): medir depois da geografia” e “Auditor PyMuPDF × Docling” ocorreram (medido). “OCR em página com texto nativo” continua recusado para texto **legível**; a exceção é a camada comprovadamente ilegível (fonte quebrada), que o OCR não degrada.

**Aceite (mesmo lote, mesma `config.json`; só o extrator muda).** Estrutura: **698 de 698 folhas** com âncora na folha física; **0 não lidas**; 165 por OCR (48 escaneadas, 104 vetoriais, 13 com fonte quebrada); tabelas: grade Markdown onde há grade, 93 folhas em colunas alinhadas, 22 com camadas sobrepostas (rastreador CNIS, extratos RENAJUD) remontadas em tabela por camada; carimbo eproc em todas as folhas; documentos que faltavam (4ª e 5ª alterações contratuais, decisão inicial da execução, recibos de transação, relatório SERPRO) presentes. Extração do lote inteiro em ~12 min (antes: ~40 min de Docling com 41% de perda).

Anonimização (detecção intocada; gabarito local de ~35 entidades montado dos relatórios do Operador; só contagens). A primeira rodada acusou **regressão**: a razão social da sucessora em claro 3 → 213 vezes, toda no rastreador CNIS, onde o GLiNER fica no limiar (0,69 em lista vertical, 0,73 em linha corrida, 0,78 em linha de tabela Markdown; corte 0,75). Corrigido dentro do extrator (tabela por camada): 3 → 4. Resíduo nas folhas que já existiam: cerca de 17 menções a mais (nome partido por quebra de linha na prosa, tabela de um veículo só no RENAJUD, consulta DETRAN sem grade), todas de entidades que o mesmo lote tokeniza em outras folhas. Nas 289 folhas recuperadas as partes saem tokenizadas; endereços de contrato lido por OCR vazam mais (conteúdo novo, não regressão). **Próximo fator:** propagar o que já foi detectado (nome e ID) a todas as ocorrências do lote, a peça central do 3.0 sem LLM. Lote de aceite: `lote_20260926_034316`.

### Veredito 2.1 — propagação do lote (26/09/2026)

Sem canal institucional para LLM, o 3.0 anda sem ele: a peça central do desenho (elenco do processo inteiro + propagação determinística + um token por entidade) entra alimentada pela detecção local que já existe.

**Decisão.** O lote passa a rodar em duas fases (`pipeline.prepare_file` → `BatchRoster` → `pipeline.finish_file`; o mesmo na interface):

1. detecção em todos os arquivos, como antes;
2. elenco do lote (`batch_propagation.py`): as grafias de pessoa e empresa se juntam por entidade (mesma forma dobrada; empresa sem sufixo societário; prefixo com 2+ palavras que só aponta para uma entidade; pessoa com o mesmo primeiro e último nome; 1–2 letras de diferença por OCR). Ambíguo em pessoa não junta;
3. cada grafia com 2+ palavras de conteúdo é buscada no texto inteiro (maiúscula, acento, quebra de linha) e só preenche lacuna: o que a detecção já marcou não muda. Toda grafia da entidade leva o mesmo token;
4. IDs já mapeados (placa, chassi, RENAVAM, e-mail) e a raiz de CNPJ privado já mapeado seguem pelo valor exato, com o token do próprio ID (não é regex sem rótulo: é o valor que a detecção achou com rótulo).

Travas: entidade com cargo colado ao nome em qualquer arquivo (“assinado por NOME”, “NOME, Juiz Federal”, “protocolado por (NOME)”) não se propaga (item H); rótulo tomado por nome (“Tipo de Devedor”) e estrutura jurídica (“pessoa jurídica de direito privado”) não se propagam; “pessoa jurídica/física” viraram termo protegido. A ocorrência propagada passa por termo protegido, termo genérico e higiene, mas **não** repete as regras amplas por ocorrência (juiz/PFN em até 160 caracteres, parte de precedente, nome banal): a entidade é julgada uma vez, no elenco, como já valia para nome completo da lista de alvos. Endereço fica fora: a detecção de endereço engole trecho de cláusula e propagar espalharia o estrago.

**Respostas do §8.8.** (1) Medido: 4 a 8 tokens por parte; o mesmo nome trocado numa folha e em claro noutra; ~500 raízes de CNPJ em claro. (2) Métrica 1: só propaga o que a detecção já aceitou; travas acima. (3) Um fator: a propagação (extração fixa na 3.0-E; GLiNER, limiar, lista, capa e regras de detecção iguais). (4) Aborto: token novo em órgão, `R$`, data, evento, sistema ou termo jurídico; juiz que deixa de ficar em claro; entidade do gabarito com mais menções em claro que no lote antigo. (5) Condição: AVALIACAO-2026-09 (plano B do 3.0) e Veredito 3.0-E.

**Aceite (mesmo lote e config; `lote_20260926_083235`).** Folhas que já existiam, contra o lote de 25/09: devedora 4 → 0 em claro, sucessora 3 → 0, antiga razão social 7 → 0, sócio A 7 → 2, sócio C 7 → 5, placas 3 → 1, raiz de CNPJ ~500 → 0; juízes iguais (item H); nenhum alerta novo na AUDITORIA. Um token por entidade: sócio C, devedora e sucessora 1 cada (eram 2, 5 e 4); sócio A 2 (eram 8; a grafia sem o prenome fica à parte: sem LLM, juntar arrisca homônimo). A primeira rodada espalhou um falso positivo antigo (“pessoa jurídica” como empresa, 15 → 80 trocas): corrigido com as travas acima (→ 0). Sem mudança (a detecção não os acha, não há o que propagar): ex-sócia, um empregado, sobrenome dos advogados, perito, endereços de contrato lido por OCR. Pré-existente e só ampliado: juíza na coluna “Juiz solicitante” do SISBAJUD e secretário-geral da Junta já eram tokenizados (o cargo está no cabeçalho da coluna, não colado ao nome).

---

## Como usar este documento

| Você quer… | Vá para… |
|---|---|
| Ver o que está travado | o veredito acima e §9 |
| Entender o produto em uma tela | §1 e §2 |
| Lembrar por que fidelidade manda | §3 (métricas) e o glossário |
| Ver o que ainda está quebrado | §5 |
| Ver o que já está no app | §7 |
| Reabrir uma ferramenta que foi recusada | §8 (tabela + condição de reabertura) |
| Saber o próximo passo | §9 (fila 2.0) e §14 |
| Julgar o teste de capa que está rodando agora | §10 |

**Regra de ouro para reabrir o que foi descartado:** não se reabre “porque o card do HuggingFace é bonito”. Reabre-se quando a **condição de reabertura** da tabela do §8 estiver cumprida e quando o experimento for **um fator por vez** (não misturar troca de modelo + queda de limiar + heurística no mesmo lote).

---

## Glossário

Termos técnicos aparecem no texto. A primeira vez, a ideia está aqui.

| Termo | Em linguagem simples |
|---|---|
| **Anonimizar / pseudonimizar** | Trocar um dado identificável por um marcador estável (`[PESSOA_1]`). O original fica no mapa privado, não no arquivo que se envia. |
| **Token** | O marcador. Exemplos: `[CPF_1]`, `[EMPRESA_1]`, `[ENDERECO_1]`. O mesmo dado vira o mesmo token em todo o lote. |
| **Mapa reversível** | Arquivo JSON na pasta `saidas/privados`. Diz o que cada token era. **Nunca viaja** com o Markdown exportável. |
| **Markdown** | Texto com títulos e tabelas simples. É o que o app grava em `saidas/exportaveis` para análise por um modelo de linguagem. |
| **NER** | *Named Entity Recognition* — o motor que tenta achar, no texto, trechos que sejam pessoa, empresa ou endereço. Não é a camada que acha CPF ou número de processo. |
| **Presidio** | Biblioteca da Microsoft para achar dados pessoais. Neste app ela entra **só com regras** (regex + validadores de CPF/CNPJ/processo). Sem o motor de “entender português” ligado. |
| **Regex** | Padrão de texto (“se parecer CPF, marque”). Regex **faminta** é regra larga demais, que come pedaço da peça sem âncora (por exemplo “qualquer sequência de 11 dígitos”). |
| **Âncora** | Rótulo que o próprio documento já traz (`EXEQUENTE`, `EXECUTADO`, `Nome do Devedor`). A heurística de capa só marca o que está **ao lado** da âncora. |
| **GLiNER** | Modelo local que recebe rótulos em português (`pessoa`, `empresa privada`, `logradouro`) e tenta achar esses trechos. Roda nesta máquina. |
| **spaCy** | Outra biblioteca de texto. O modelo `pt_core_news_md` é português “de notícia”. Aqui só entra se o GLiNER **não carregar**. |
| **Docling** | Extrator de PDF: transforma a peça em Markdown. OCR só se a página não tiver texto nativo útil. |
| **PyMuPDF** | Extrator de PDF de reserva, se o Docling falhar. |
| **Limiar (threshold)** | Nota mínima de confiança do modelo. No app: **0,75** (75%). Quanto mais baixo, mais o modelo marca — inclusive o que não deveria. |
| **Recall** | “Quantos dos dados que deveriam ser tokenizados de fato foram.” Alto recall com peça desfigurada **não** é sucesso neste produto. |
| **Super-redação** (*over-redaction*) | Tokenizar o que a peça precisa continuar mostrando: data, `R$`, tese, União, SISBAJUD, número de evento. |
| **Fidelidade > recall** | Preferimos um nome que escape pontualmente a descaracterizar o documento. |
| **Checkpoint** | Um modelo já treinado, baixável (em geral no HuggingFace), pronto para usar. Trocar de checkpoint não é treinar de novo. |
| **Fine-tune** | Treinar um pouco mais um modelo já existente, com exemplos do nosso domínio (capa eproc, CDA, petição em CAIXA ALTA). |
| **HuggingFace** | Repositório público de modelos. Um “card” é a ficha do modelo (o que detecta, licença, tamanho). |
| **eproc / PJe** | Sistemas de processo eletrônico. A capa e a CDA já rotulam as partes. |
| **CDA** | Certidão de Dívida Ativa. Traz “Devedor”, endereço, valores. |
| **KEEP_TERMS** | Lista de termos que o app **não** tokeniza: União, PGFN, SISBAJUD, teses, SNIPER/CENSEC/SIMBA/SERASAJUD/CCS-Bacen, AGU/PGF/PGE/PGM, etc. **Não** inclui Serasa (empresa privada). |
| **Lista de alvos** | O contrário da exclusão: nomes **e** CPF/CNPJ da carteira que o usuário informa para o app **sempre** tokenizar naquele caso. Aceita em 29/08/2026 (nomes); CPF/CNPJ da lista em 09/09/2026. Ver §8.5. |
| **Passada-sombra de caixa** | O GLiNER lê uma *cópia* em que trechos TODO-MAIÚSCULOS viram Title Case; o Markdown original não muda. Mesmo modelo, mesmo limiar 0,75. Ver §8.7.B. |
| **Janela nativa** | O app abre numa janela deste computador (não numa aba qualquer do Chrome). O endereço local continua `http://127.0.0.1:8765`. |
| **Dados Abertos do CNPJ** | Cadastro público de CNPJs que a Receita Federal publica para download. Usado **localmente** (índice SQLite) para descobrir a razão social a partir do CNPJ e distinguir empresa privada de órgão público. Ver §8.5. |
| **Sprint / ciclo** | Um recorte de trabalho com um experimento de cada vez. |
| **IR** | Representação interna estruturada do PDF (o que o Docling já extraiu) **antes** de virar Markdown. O Markdown achata tabela e perde página. |
| **Furniture** | Cabeçalho e rodapé repetidos em cada página. Marcar é melhor do que apagar. |
| **Geografia do processo** | No Markdown, conseguir dizer *em que página do PDF*, *em que evento eproc* e *em que bloco de peça* (o trecho entre páginas de separação) está um trecho. Não é o mapa completo de relações entre autos. |
| **CPU / GPU** | Processador comum vs. placa de vídeo. Este app foi desenhado para **CPU no Windows**. |
| **SPDX / licença** | Identificador de licença. Sem licença clara, o modelo não vira dependência de produto. |

---

## 1. O produto, em um parágrafo

App **Windows local** (abre com `iniciar.bat`, tela NiceGUI em `http://127.0.0.1:8765`) para **anonimizar peças jurídicas brasileiras antes de análise por um modelo de linguagem**. O usuário — que não precisa programar — escolhe PDF, DOCX, TXT ou MD; o app extrai o texto, troca identificadores por tokens persistentes e grava:

- o Markdown em `saidas/exportaveis`
- o mapa reversível **só** em `saidas/privados`

A premissa absoluta é **fidelidade da peça**: eventos, datas, valores, teses e órgãos valem mais do que capturar todo residual identificável. O destino de análise recebe **apenas** o Markdown já tokenizado — nunca o original, nunca o mapa.

---

## 2. Planejamento inicial

### 2.1. Problema de origem

Peças de execução fiscal e afins não podem ir cruas a um modelo de linguagem: nomes, CPF, CNPJ, endereço e advogados identificam pessoas e empresas. Redigir “no atacado” (qualquer data, qualquer número, qualquer órgão) destrói a peça para análise jurídica. Era preciso um meio-termo **local**, revisável na mesma tela, com o usuário no controle das regras.

### 2.2. Decisões de desenho tomadas cedo (e que continuam)

| Decisão | Por quê |
|---|---|
| App local, Windows, CPU | Não mandar o original para nuvem como passo obrigatório. |
| NiceGUI (não Streamlit / Gradio / Flet) | Uma tela só, duas abas (Anonimizar e Regras), fácil de reabrir. |
| Extração PDF com Docling; PyMuPDF se falhar | Texto nativo primeiro; OCR só se a página estiver “cega”. |
| Presidio só com regras (IDs brasileiros) | CPF, CNPJ, processo CNJ, OAB etc. são validáveis. Não misturar com NER inglês. |
| Tokens `[TIPO_n]` persistentes no lote | O mesmo CNPJ vira o mesmo `[CNPJ_1]` em todos os arquivos do lote. |
| Mapa separado do exportável | Quem analisa a peça não recebe a chave. |
| Gemini existe, default **desligado** | Motor interno **sai** (20/09/2026). A leitura (original + tokenizado) é fora do app, pelo operador. O app não é cliente da API. |
| Varredura residual **avisa**, não substitui | Sem Gemini interno, o aviso residual some com o motor. Leftover vira conferência externa. |

### 2.3. Pipeline atual (o caminho do arquivo)

```
PDF / DOCX / TXT / MD
    → extração (Docling no PDF; OCR só se faltar texto nativo; senão PyMuPDF)
    → Presidio (IDs estruturados: CPF, CNPJ, processo, e-mail, telefone, OAB, IDs rotulados)
    → heurística de cabeçalho (capa / polo / CDA rotulada)     ← camada nova em 29/08/2026
    → GLiNER local (pessoa / empresa privada / logradouro), limiar 0,75
         (spaCy PT só se o GLiNER não carregar)
    → junção: IDs estruturados vencem NER se houver sobreposição
    → tokens no Markdown; mapa JSON privado
    → [fora do app] operador lê original e MD tokenizado no Gemini
```

### 2.4. O que o app já tokeniza com confiança (IDs)

CPF (dígitos verificadores); CNPJ (numérico ou alfanumérico 2026); número CNJ de processo; e-mail; telefone (desligável na aba Regras); OAB clássica e variantes eproc (`OAB/RS n.º`, `N.OAB-`); e, **quando vierem com rótulo**: RG, matrícula de imóvel, CEP, Pix, agência/conta, RENAVAM, placa, chassi/VIN, data de nascimento, título de eleitor.

**Rótulo, para P10 (refinamento 02/09/2026):** palavra no texto (`PLACA`, `RENAVAM`, `Data de Nascimento`) **ou** cabeçalho de coluna Markdown (`UF/PLACA`, `N.Renavam`, `Chassi`) **ou** rótulo de campo de formulário (`Nome da Mãe`, `Título de Eleitor`). Regex **sem nenhum** desses rótulos continua proibida.

Código eproc do procurador da lista (`PR077705`, `P3412557`) tokeniza-se na capa/linha `Procurador(es):` e propaga a mesma string; `PFN`+dígitos ao lado de PFN permanece em claro.

**De propósito não há** regex sem rótulo para UUID, 11 dígitos soltos, placa solta, conta solta, CNH/PIS/IE de fábrica. Isso seria regex faminta.

---

## 3. As duas métricas (como se julga qualquer mudança)

Toda alteração, todo relatório de pesquisa e todo teste de lote se lê nestas duas colunas. A **Métrica 1 corta a Métrica 2**: se a estrutura quebrar, o ganho de amplitude não conta.

| Métrica | Pergunta | Estado em 29/08/2026 |
|---|---|---|
| **1. Manutenção da estrutura** | A peça continua analisável **e localizável**? Datas, `R$`, teses, União/PGFN, SISBAJUD sobrevivem **e** dá para dizer *qual evento, qual documento, qual página*? | **Parcial.** O conteúdo jurídico (KEEP_TERMS, filtros, Presidio cego de NLP) chega. A extração ainda sangra: Markdown lossy, eventos fundidos (**P3**). CDA achatada (**P4**) existe e **não** entra na fila. Não se troca o Docling; não se lê isso como “nada a fazer”. |
| **2. Amplitude da anonimização** | Pessoa, empresa **privada** e logradouro (via + número) viram token, sobretudo na capa eproc em CAIXA ALTA? | **Fraca no NER.** IDs estruturados ok. Capa era o buraco. Heurística de cabeçalho entrou no código; o aceite é o **teste no lote real** (você está nesse teste agora). |

**O que nunca se tokeniza:** datas; valores em `R$`; números de evento; teses, artigos e leis (CTN, LEF, CPC…); órgãos públicos; sistemas (SISBAJUD, RENAJUD, CNIB, INFOJUD); cidade, UF, comarca, vara ou juízo como se fossem endereço ou empresa.

**O que se tokeniza com confiança:** IDs estruturados; nome completo de pessoa física; razão social privada; logradouro com via e, quando houver, número.

---

## 4. Objetivos

### 4.1. Objetivo permanente

Entregar um Markdown **analisável** e **suficientemente** anonimizado para uso profissional, sem nuvem obrigatória, sem mapa junto, com o usuário no controle das regras.

### 4.2. Objetivo deste ciclo (agosto/2026)

1. Fechar a **capa** do eproc (parte privada, advogados da parte, endereço rotulado) **sem** mexer no GLiNER nem no limiar 0,75. Único experimento de amplitude agora.
2. **Geografia do processo (P3)** — âncora de página + evento descolado + auditor PyMuPDF × Docling — na folga, **sem contaminar** o piloto de capa (não muda NER). Se não houver folga, em seguida ao aceite da capa. Não é árvore fina. **Não** inclui consertar a grade da CDA (P4).
3. Se a capa fechar e o **corpo** ainda vazar nomes: LeNER-Br **base** *ou* `dominguesm`, só PESSOA no merge.
4. Depois, isolado: `arthrod` no limiar **0,75**, três rótulos em português, sem `dob`/CPF. Adiar **não** é “já temos GLiNER”.
5. Ainda depois: fine-tune no esquema do produto. É o teto no corpo eproc; não some do mapa.

### 4.3. Objetivo explícito que *não* é deste ciclo

- Reconstrução fina da árvore eproc (mapa completo de relações entre eventos). A geografia (página / evento / bloco de peça) **é** deste ciclo; a árvore completa não.
- Embelezar ou “consertar” a grade da CDA no Markdown (P4). CDA em regra não é sigilosa ou não entra na análise via anonimização.
- Limpeza agressiva de cabeçalho/rodapé.
- Data de nascimento sem rótulo.
- Plugin genérico / marketplace.
- Religar Gemini como motor principal.
- Expandir recall com regex sem âncora.
- LLM local, GLiNER2, Presidio com transformer/Stanza como motor.
- Trocar o Docling por Marker, MinerU ou `pymupdf4llm`.
- `rafola` + `ottema` **neste ciclo** (reabertura no §8.1, não recusa perpétua).

### 4.4. Decisões do Ciclo 4 (02/09/2026)

Escopo novo, determinístico, fidelidade > recall. Nada sai da máquina.

1. **Cabeçalho de coluna e rótulo de formulário contam como rótulo (P10).** Placa/RENAVAM/chassi na tabela DETRAN; nascimento/filiação/título no cadastro Receita. Regex sem nenhum rótulo continua proibida.
2. **Tokenizar** domínio e marca da empresa alvo (`broggividros.com.br` e OCR colado) com o mesmo `[EMPRESA_n]` da razão. Não tokenizar `vidros` em tese jurídica.
3. **Tokenizar** código eproc do procurador da lista (`PR077705`, `P3412557`) na capa e por propagação. **Não** tokenizar `PFN2041305` ao lado de PFN.
4. **Não** neste ciclo: limiar 0,75, troca de GLiNER, Docling, OCR, alias/overlap do Ciclo 1, regex de CPF sem rótulo.

Lote de aceite: `lote_20260902_210844`. Gabarito APROVADO, 0 falhas. 218 testes.

### 4.5. Decisões do Ciclo 5a (03/09/2026)

Super-redação da lista de alvos. Fidelidade > recall. Sem mexer no GLiNER nem no limiar 0,75.

1. **Denylist de partícula.** UF, cidade, mês, estado civil, tese, órgão e palavra de giro **não** viram semente nem match (inclusive fuzzy: CAMPOS não come Campo Mourão; MARCOS não come março; CORREIA não come correio). A razão/nome completo da lista continua buscado.
2. **Não extrair** partículas de rótulo entre parênteses (`Nome Fantasia:`, `Empresa Individual`, `antiga`) nem da cauda `em recuperação judicial` / prefixo `Espólio de`.
3. **Lista persistida recortada** ao feito Cacau/Potinga/Conplan deste lote (167+119 → 15 empresas + 5 pessoas). Cópia da acumulada em `config.lista_acumulada_20260903.json`.
4. **Pastas:** se a raiz de textos e a privada coincidem, o lote grava `.md` em `exportaveis/` e o mapa em `privados/`.
5. **Não** neste passo: higiene de span (`\n`/HTML/rótulo), fuzzy só em nome completo, sobrenome isolado (Silva/Lopes), vazamento de ID, LeNER, limiar.

### 4.6. Decisões do Ciclo 5b (04/09/2026)

Determinístico, **antes** de reprocessar o lote `20260902_230718`. Sem mexer no GLiNER nem no limiar 0,75. A denylist do 5a permanece isolada (já coberta por teste unitário); este passo ataca o que ainda abortava a Métrica 1 mesmo com a lista recortada.

1. **Higiene de span** (`span_hygiene.py`), depois de `drop_protected_spans`: comentário HTML não vira pessoa; PERSON/ORG/LOCATION com duas ou mais quebras de linha são cortados na segunda; rótulo sozinho (`Conta:`, `CEP:`) cai; LOCATION tem teto de 90 caracteres e não engole data `dd/mm/aaaa`. Uma quebra de linha na razão da lista de alvos **continua** válida. IDs (CNPJ com hífen quebrado) não são cortados no `\n`.
2. **AUDITORIA.txt vê falso positivo do mapa:** seção `mapa.json` com palavras de dicionário/UF/tese (não copia nome de parte), âncora HTML, fusão de linhas e rótulo; no Markdown, CPF formatado em claro vira ALERTA e CNPJ formatado em claro é contado (pode incluir órgão público).
3. **CNPJ com espaço antes do DV** (`0001- 10`) passa a ser reconhecido. Filial `/0004-` por raiz **não** entra neste passo.
4. **Não** neste passo: sobrenome isolado (Silva/Lopes), agente público vs lista, CEP de coluna, matrícula, eproc de advogado fora da lista, política Debcad/SISPAR, LeNER, limiar.

### 4.7. Decisões do Ciclo 5c (04/09/2026)

Determinístico, **ainda antes** do reprocessamento. Encadeamento de regras, sem GLiNER nem limiar 0,75. Cada camada só vê o que a anterior deixou.

1. **Partícula comum só com cola onomástica ou rótulo.** Silva, Lopes, Souza, Marcelo, Campos não tokenizam sozinhos nem porque outro pedaço do mesmo alvo aparece a 40 caracteres. Precisam de: rótulo `DEVEDOR`/`SÓCIO`/`RÉU`/`ADQUIRENTE` imediatamente antes; **ou** outra partícula do mesmo alvo colada por espaço/quebra/`de`/`da`/`do`; **ou** span já emitido colado da mesma forma. `Silva e Lopes` são duas pessoas. Frase traseira (`Lopes da Silva`) é buscada como nome. Partícula distinta (CASTANHEIRA, BROGGI) continua isolada. Fuzzy de 1 letra **não** roda em partícula comum.
2. **Agente público.** `Juiz Federal` sem dois-pontos, `ministro`/`min.`/`rel. min.`, desembargador, diretor de secretaria, signatário, `Usuário:`, chefe da PFN, procurador da União. KEEP_TERMS cobre Supremo, desconsideração inversa, citação pelo correio.
3. **NER no corpo:** nome só com partículas banais (`Helena Lopes da Silva`) não tokeniza. Lista de alvos, heurística de capa e formulário rotulado vencem. CASTANHEIRA no meio salva o span.
4. **IDs que vazavam:** filial CNPJ pela raiz já vista (órgão público não entra); `CONTA SISPAR` / `INSCRIÇÃO SISPAR` / `DEBCAD:` rotulados; lista `matrículas nº X; nº Y`; CEP/telefone em coluna de tabela; código eproc na capa mesmo sem lista de advogados (`PFN`+dígitos e `REsp` continuam em claro).
5. **Normalização:** apóstrofo (`'`/`´`/`’`) na razão; `AVE`/`Ave.` como via; `pagina-origem:` sem `<!--` não vira pessoa; AUDITORIA conta CEP formatado em claro.
6. **Política Debcad/SISPAR:** rótulo → token (`[CONTA_n]`). Número solto sem rótulo **não** entra (P10).
7. **Não** neste passo: dicionário completo de português; OCR de nome completo com RapidFuzz; LeNER; limiar; QSA CNPJ; cruzar âncoras bruto × anonimizado.

---

## 5. Problemas identificados

Cada item tem **estado**: aberto, em teste, mitigado, fora de escopo.

| # | Problema | Métrica | Evidência | Estado |
|---|---|---|---|---|
| P1 | Capa eproc em CAIXA ALTA: razão social privada, advogados e logradouro da qualificação ficam em claro | 2 | Lote de execução fiscal: IDs estruturados tokenizados; zero `[PESSOA]` / `[EMPRESA]` / `[ENDERECO]` no mapa daquele lote. O texto da capa **chegou**; o GLiNER, no 0,75, **não marcou**. | **Em teste.** Heurística de cabeçalho no código (29/08/2026). Aceite = lote real, critérios do §10. |
| P2 | GLiNER `urchade/gliner_multi-v2.1` é genérico multilíngue, não jurídico; perde CAIXA ALTA eproc | 2 | Mesmo lote; corpus sintético já passava com motor fake | Aberto no **corpo** da peça. Não se “conserta” baixando o limiar. |
| P3 | **Geografia do processo** perdida ou fundida: não dá para apontar com segurança *qual evento, qual documento, qual página* | 1 | Lote real (269 páginas, PDF nativo ~8,5 MB): Markdown do Docling com **zero** `<!-- pagina-origem -->`. Há títulos `## Evento`, mas vizinhos se fundem (ex. 67+66) e há `## Evento:` sem número. O fallback PyMuPDF **já** grava página; o caminho padrão (Docling) **não**. O consolidado (§6.3) propunha âncora de página + quebra se o padrão de evento aparecer colado — sem reconstruir a árvore. | **Aberto.** Autorizado na folga, sem contaminar o piloto de capa. Conserto **sem** trocar o Docling. Detalhe no §5.1. |
| P4 | Markdown achata a grade da CDA (`rowspan` / tabela “bonita”) | 1 | Extração | **Não priorizar.** CDA em regra não é sigilosa ou não precisa ir à análise via anonimização. Não gasta ciclo. |
| P5 | Tempo e volume: processos de 2–3 mil folhas / 100–200 MB são uso real | 1 | Código + lote medido | **Aberto como requisito**, não só “custo aceito”. Limites e estimativas no §5.2. Continua sem Marker/MinerU como default. |
| P6 | OAB no formato eproc (`n.º`, `N.OAB-`) escapava | 2 (IDs) | Lote real | **Mitigado** no Presidio. |
| P7 | Localizador não achava menção se o modelo devolvesse caixa diferente | 2 | Capa em CAIXA ALTA | **Mitigado** (busca indiferente a maiúsculas). O buraco restante é **detecção**, não substituição. |
| P8 | spaCy descartava tudo se o score ficasse abaixo de 0,75 | 2 | Fallback | **Mitigado** (score fixo 0,80 se o spaCy entrar). spaCy **não** roda em paralelo com o GLiNER. |
| P9 | Órgão (vara, comarca, União) podia ser tentado como empresa | 1 | Filtros | **Mitigado** (`KEEP_TERMS` + filtro de órgão). Risco novo = motor guloso marcar o que a lista **não** conhece. |
| P10 | CEP / Pix / conta / placa / RENAVAM / chassi / nascimento / título sem rótulo | 2 | Deliberado | **Não é bug.** Abrir regex sem rótulo seria faminta. **Refinamento 02/09/2026:** cabeçalho de coluna e rótulo de campo de formulário **contam como rótulo**. Regex sem nenhum rótulo continua proibida. |
| P11 | Nomes só no mérito da petição (sem âncora de capa) | 2 | Esperado neste ciclo | Aberto de propósito. O piloto de capa **não** promete o corpo. Caminho aceito para fechar: camada determinística do §8.5 (lista de alvos + base CNPJ), antes de qualquer modelo novo. |
| P12 | Furniture (cabeçalho/rodapé da JF repetido) | 1 | “Alameda …, 313” em várias páginas | Aberto. **Marcar**, não apagar. |
| P13 | Super-redação: partícula da lista de alvos tokeniza UF, cidade, tese, mês, estado civil | 1 | Lote `20260902_230718` (IDPJ Cacau/Potinga): lista acumulada de 167 empresas + 119 pessoas; Paraná/Campo/casado/março/sistemas/recuperação judicial. Métrica 1 aborta. | **Mitigado no motor (5a).** Denylist + recorte da lista. Aceite = reprocessar o lote. |
| P14 | Span contextual engole âncora HTML, rótulo de campo (`Conta:`/`CEP:`) ou parágrafo alheio (endereço de centenas de caracteres) | 1 | Mesmo lote: SIDA; `[ENDERECO]` de 517 caracteres; `<!-- pagina-origem -->` como PERSON; 110 originais com `\\n` no mapa | **Mitigado no motor (5b).** Higiene de span. Aceite = mesmo reprocessamento. |
| P15 | Sobrenome/prenome banal isolado (Silva, Lopes, Marcelo) cola em juiz, servidor, tese e terceiro | 1 | Mesmo lote: fragmentação de identidade; chefe da PFN com prenome coincidente | **Mitigado no motor (5c).** Cola onomástica ou rótulo; `rel. min.`/`ministro`; NER não tokeniza nome só banal. CASTANHEIRA isolado continua. |

Três erros de conclusão já nomeados e **proibidos**:

1. Baixar o limiar 0,75 para o GLiNER atual “passar a ver” a capa — aumenta ruído no corpo inteiro.
2. Trocar o Docling porque a amplitude falhou — o extrator já entregou o texto da capa.
3. Ler “não trocar o Docling” como “não há o que fazer na Métrica 1” — Markdown lossy e eventos fundidos (P3) continuam; o conserto é serialização/auditor, não motor novo.

### 5.1. P3 — Geografia: o que o relatório já sugeria e o que falta no código

O consolidado (seção 6.3, “serialização conservadora”) **já** listava, sem motor novo:

- âncoras `<!-- pagina N -->` (ou equivalente);
- quebrar linha se o padrão de evento (`Evento 12`, `EVENTO Nº 12`) aparecer colado no meio de outro parágrafo;
- não promover `EXECUÇÃO FISCAL Nº …` nem `art. 185 do CTN` a título.

Isso é exatamente a geografia. O consolidado autorizou como **folga**, sem contaminar o piloto de capa. Continua assim: P3 não espera troca de GLiNER nem queda de limiar; também **não** inclui embelezar CDA (P4). Reconstrução fina da árvore eproc permanece fora de escopo.

Estado no código hoje:

| Camada | Página | Evento | Documento / bloco de peça |
|---|---|---|---|
| PyMuPDF (só se o Docling falhar) | Sim: `<!-- pagina-origem: N -->` e `## Pagina N` | Não (só o texto que estiver na página) | Não |
| Docling (padrão) | **Não** no Markdown exportado | Parcial: muitos `## Evento N`, mas fusão de vizinhos e `## Evento:` sem número | Só o que o eproc já escreveu (“PÁGINA DE SEPARAÇÃO”), sem âncora estável |

Caminho de conserto (sem trocar o extrator): aproveitar a página que o Docling já tem no documento interno, ou cruzar com o PyMuPDF só como **auditor de página**; quebrar eventos colados por regex de âncora. “Qual documento” = o bloco entre páginas de separação / cabeçalho de evento, não o nome do arquivo.

### 5.2. P5 — Limite de tamanho, tempo e processos de 100–200 MB

**Não há limite de megabytes por arquivo no app.** Os PDFs entram pelo caminho no disco (não há upload pelo navegador com teto típico de 10–50 MB). Também **não há teto de número de páginas**.

Há um teto de **tempo de extração Docling**: `document_timeout = 900` segundos (**15 minutos por PDF**). Se estourar, o app cai no PyMuPDF.

Lote já medido (PDF nativo de execução fiscal, um “PARTE”):

| Dado | Valor |
|---|---|
| Páginas | 269 |
| Tamanho do PDF | ~8,5 MB |
| Markdown gerado | ~340 mil caracteres / ~9,4 mil linhas |
| Âncoras de página no MD (Docling) | 0 |
| Títulos `## Evento` | 187 (com fusões) |

Tempo **não foi cronometrado** nesse lote. Ordem de grandeza honesta, CPU Windows, texto nativo (sem OCR):

| Etapa | O que pesa | Palpite para 269 páginas | Extrapolação para 2–3 mil páginas / 100–200 MB |
|---|---|---|---|
| Docling (tabelas ligadas) | Página a página | Minutos, em geral abaixo de 15 min se nativo | **Risco real de bater os 15 min** ou de ficar 30–90+ min se o timeout for alargado |
| Presidio (IDs) | Tamanho do texto | Segundos a ~1 min | Alguns minutos |
| Heurística de capa | Só o início do texto | Instantâneo | Instantâneo |
| GLiNER no texto inteiro | ~1.200 caracteres por fatia; ~300 fatias nesse lote | Provável de **vários minutos** (primeira carga do modelo é extra) | **Dezenas de minutos a mais de uma hora**, se o texto crescer na mesma proporção |
| OCR (se o PDF for imagem) | Cada página “cega” | Muito mais lento que nativo | Quase certamente estoura 15 min ou torna o lote impraticável num único arquivo |

Processo de 2–3 mil folhas / 100–200 MB **é requisito de uso**, não exceção. Com o desenho atual, um único PDF desse porte **não está dimensionado**. Caminhos a registrar (não implementados):

- continuar fatiando como o eproc já faz (`PARTE_n`), que é o hábito do lote de teste;
- subir o timeout do Docling **e** medir;
- geografia via PyMuPDF (rápido em texto nativo) mesmo quando o Docling extrai o conteúdo;
- não passar o GLiNER em 200 MB de uma vez — capa/heurística no começo; NER do corpo em fatias ou só depois de recortar eventos.

Isso **não** autoriza Marker, MinerU, nuvem ou OCR em página nativa.

---

## 6. Fontes que este mapa absorve

Estes textos **não substituem** a governança; ela os resume e os corta.

| Fonte | Onde estava | O que aproveitamos | O que não vira plano |
|---|---|---|---|
| Briefing de status 0.3.0 | Conversa + relatório de status para pesquisa | Premissas, stack, duas métricas, o que já estava mapeado | — |
| Relatório de pesquisa NER (29/08/2026) | `Downloads/relatorio-ner-anonimizador-juridico.md` | Diagnóstico (domínio + caixa); catálogo HuggingFace completo — **entregável pedido**, não defeito; cards e licenças | A **ordem 1–10** e o “dois patches no mesmo passo” (heurística + troca de GLiNER + limiar 0,60) |
| Resposta de produto | `resposta-relatorio-ner.md` neste repositório | Ordem do ciclo; aceite/adiamento/recusa ponto a ponto; critérios de sucesso/aborto da capa | Tom inicial “inflar o catálogo”; “Métrica 1 já está boa” sem P3. **Corrigido** na própria resposta após a avaliação |
| Relatório consolidado | `Downloads/relatorio-consolidado-anonimizador-juridico.md` | Síntese das duas pontas; Métrica 1 de serialização (seção 6); matizes (`arthrod` ≠ já mapeado; `rafola`/`ottema` não perpétuo; fine-tune = teto no corpo) | A frase “heurística não está no app”; serialização “completa” com embelezar CDA — **P4 recortado** neste mapa |
| Código 0.3.0 + heurística de cabeçalho | `src/anonimizador_app/` | O que de fato roda | — |

---

## 7. O que já está no produto (para não “descobrir” de novo)

### Já em uso

- NiceGUI; Python 3.14 no ambiente local (o projeto pede ≥ 3.11).
- Docling 2.x; PyMuPDF de reserva; python-docx.
- Presidio Analyzer só com PatternRecognizers; IDs vencem NER (prioridade 100).
- GLiNER `urchade/gliner_multi-v2.1`, rótulos `pessoa` / `empresa privada` / `logradouro`, limiar **0,75**.
- spaCy `pt_core_news_md` (reserva `lg` / `sm`) **só se o GLiNER não carregar**.
- KEEP_TERMS, filtro de órgão, filtro de via, pessoa com duas ou mais palavras.
- Localizador indiferente a maiúsculas.
- Heurística de cabeçalho (`HeaderHeuristicEngine`), camada **antes** do GLiNER: âncoras de capa/polo e linhas rotuladas de Devedor/Endereço no primeiro bloco. Não substitui o GLiNER no corpo.
- Gemini implementado, default desligado, UI em pausa.
- Varredura residual avisadora.
- Lista de alvos: caixas “Empresas a anonimizar”, “Pessoas a anonimizar” e “CNPJ e CPF da carteira” na aba Regras; **denylist de partícula** (Ciclo 5a).
- Higiene de span (Ciclo 5b): HTML/rótulo/fusão/teto; `AUDITORIA.txt` varre o mapa sem copiar PII.
- Encadeamento 5c: partícula comum só com cola onomástica ou rótulo; NER recusa nome só banal; filial CNPJ; SISPAR/Debcad rotulados; eproc na capa sem lista.
- Janela de aplicativo local + atalho; link `http://127.0.0.1:8765`.
- Geografia no Markdown do Docling (`<!-- pagina-origem: N -->`); `AUDITORIA.txt` no lote.
- Índice CNPJ Sul no código; inerte até existir `dados/cnpj_sul.sqlite`.
- Testes unitários na última contagem (capa, alvos, CNPJ, geografia). O caminho GLiNER real **não** é o CI unitário; há smoke opcional.

### Já rejeitado de forma estável (não é “novidade de pesquisa”)

OpenAI Privacy Filter; tokens de papel (`[EXECUTADA_1]`); Streamlit / Gradio / Flet; regex faminta; Gemini como destino ou como motor principal deste ciclo.

---

## 8. Alternativas: o catálogo completo, com recusa e reabertura

Esta é a seção que permite **voltar** ao que hoje não se faz. Três carimbos:

- **Veto de schema** — não ligar, em nenhum motor, rótulos DATE / TEMPO / MONEY / LEGISLACAO / JURISPRUDENCIA / VALUE / `dob`. Corta o que tokenizaria o que a peça precisa mostrar.
- **Fora deste ciclo** — recusado agora; a coluna “Quando reabrir” diz o gatilho.
- **Adiado** — entra na fila **depois** de uma medição, não junto com outro experimento.

### 8.1. Motores NER e PII (pesquisa de 29/08/2026)

| Item | Origem da sugestão | Decisão | Por quê (simples) | Quando reabrir |
|---|---|---|---|---|
| Heurística de cabeçalho eproc/PJe/CDA | Pesquisa item 4; 1º na nossa ordem | **Aceito e implementado** | A capa já rotula a parte. Não depende de o modelo “aprender” CAIXA ALTA. | Já está no código. O que falta é o **aceite no lote real** (§10). Se falhar: revisar âncora/janela; **não** compensar com outro modelo no mesmo passo. Se a âncora nem existir no Markdown → problema de extração (§9.2), não de HuggingFace. |
| LeNER-Br base `pierreguillou/ner-bert-base-cased-pt-lenerbr` | Pesquisa item 2 | **Aceito com ressalva, ciclo 2** | Tokenizer *cased* (respeita maiúsculas). Treinado em petição/acórdão BR. Fecha o **corpo**, não a capa. Rótulos de lei/tempo/jurisprudência **proibidos** no merge. Só PESSOA no primeiro smoke; LOCAL só com via; ORGANIZACAO desligada no primeiro smoke. Conferir licença (card sem SPDX) antes de dependência de produto. | **Só se** o piloto de capa passar **e** o corpo ainda vazar nomes. Um checkpoint, não two. Large (~335 M) fora. Não plugar no Presidio. Card: https://huggingface.co/pierreguillou/ner-bert-base-cased-pt-lenerbr |
| `dominguesm/legal-bert-ner-base-cased-ptbr` | Pesquisa item 3 | **Alternativa** ao LeNER base, não segundo motor | Mesmo esquema; CC-BY-4.0 explícita. F1 de paper ≠ eproc. | Escolher **este ou** o pierreguillou, nunca os dois no mesmo merge (super-redação duplicada). Card: https://huggingface.co/dominguesm/legal-bert-ner-base-cased-ptbr |
| `arthrod/gliner-mmbert-small-ptbr-pii-full-3x-v1` | Pesquisa item 1 (era o 1º do ranking) | **Adiado.** Adiar **não** é “já temos GLiNER”. | Melhor candidato **isolado** da mesma família. Treino PII em PT-BR; empresa **não** é rótulo nativo forte. O card usa limiar 0,3 — incompatível com fidelidade. A pesquisa sugeriu 0,55–0,70; isso é outro experimento. | Prova **isolada**, **depois** da capa (e, de preferência, depois do smoke LeNER se ele existir): **mesmo limiar 0,75**, três rótulos em português, **sem** aliases `dob`/CPF/e-mail. Não no mesmo lote que a heurística. Card: https://huggingface.co/arthrod/gliner-mmbert-small-ptbr-pii-full-3x-v1 |
| Variantes ettin 68M/32M da série arthrod | Pesquisa item 1 | **Adiado** (herda o item acima) | Mais leves se a latência do mmBERT incomodar. | Só depois (ou no lugar) do arthrod principal, mesmas regras de limiar e rótulos. |
| `rafola/BERT-base-pt-BR-person` + `ottema/bert-addresses-brazil` | Pesquisa item 5 | **Fora deste ciclo** (não perpétuo) | Dois BERT. Pessoa sim; **empresa não** — não fecha a capa. Endereço empurra cidade/UF, que o filtro de via **já recusa**. | Se, **depois** da capa, o vazamento for *somente* pessoa no corpo **ou** *somente* rua+número, pode-se reavaliar **um** dos dois, limitado a parágrafo candidato. Não os dois em série “por via das dúvidas”. Cards: https://huggingface.co/rafola/BERT-base-pt-BR-person · https://huggingface.co/ottema/bert-addresses-brazil |
| `E3-JSI/gliner-multi-pii-domains-v1` e `knowledgator/gliner-pii-large-v1.0` | Pesquisa item 6 | **Fora deste ciclo** | Mesma família já em uso; `cpf`/`dob` retrabalham o Presidio e ferem a política. Inferior ao arthrod em PT-BR. | Só se o arthrod isolado falhar **e** ainda se quiser ficar em GLiNER. Sem `dob`. Cards: https://huggingface.co/E3-JSI/gliner-multi-pii-domains-v1 · https://huggingface.co/knowledgator/gliner-pii-large-v1.0 |
| Presidio `TransformersNlpEngine` / Stanza | Pesquisa item 7 | **Fora deste ciclo** (arquitetura) | O Presidio “cego” de NLP é desenho: overlap estável. Ligar engine NLP **não** é detector novo; bagunça score. Stanza-PT é jornal, não jurídico. | Se um dia o LeNER entrar, entra no `LocalNerEngine` (lado do GLiNER), **não** dentro do Presidio. Docs: https://github.com/microsoft/presidio/blob/main/docs/analyzer/nlp_engines/transformers.md |
| GLiNER2 `ottema/gliner2-ptbr` e Fastino PII | Pesquisa item 8 | **Fora deste ciclo** | Biblioteca nova (`gliner2`), não é troca drop-in. Fastino: precisão “legal” ~0,35 no benchmark citado; otimizado a recall de redação. Domínio chat/PII. | Só depois de esgotar GLiNER 1.x (atual + arthrod isolado). Não como atalho. Cards: https://huggingface.co/ottema/gliner2-ptbr · https://huggingface.co/fastino/gliner2-privacy-filter-PII-multi |
| LLM local (Gemma 2–9B Q4, Llama 3.1 8B Q4, NuExtract-2.0-2B) | Pesquisa item 9 | **Fora deste ciclo** | Pode inventar um trecho que **não está** no documento. Sem “o span tem de ser substring”, é inaceitável. Runtime à parte (Ollama/llama.cpp). Residual **já existe** sem LLM. | Depois de capa + (se houver) LeNER filtrado. Só como **árbitro residual** em parágrafo já marcado, temperature 0, schema fechado, span = substring. Não é o “Gemini local” nem motor da peça inteira. Card NuExtract: https://huggingface.co/numind/NuExtract-2.0-2B |
| Fine-tune GLiNER ou LeNER em páginas eproc anotadas | Pesquisa item 10 | **Adiado.** Teto no corpo eproc **permanece**. | Maior teto nos **três** rótulos no domínio real. Anotar a **capa** que a âncora já resolve é desperdício. | Depois de medir capa e, se houver, LeNER. Anotar só pessoa / empresa privada / logradouro. **Não** anotar data, valor, lei, órgão, juiz, servidor. ~30 páginas reais, CAIXA ALTA. Backbone candidato: arthrod **ou** Legal-BERT-pt + cabeça NER de três rótulos — escolher na hora, não agora. |
| Baixar o limiar 0,75 (0,60 / 0,55 / 0,3) | Implícito nos cards | **Veto neste ciclo** | Mais marcas no corpo inteiro. Não prova CAIXA ALTA; prova que o modelo ficou guloso. | Nunca no mesmo passo que outro experimento. Se um dia se testar, é o **único** fator daquele lote, no GLiNER **já escolhido**, com aborto se órgão/`R$`/tese tokenizar. |
| Ligar DATE / TEMPO / MONEY / LEGISLACAO / JURISPRUDENCIA / `dob` | Vários cards | **Veto de schema** | É exatamente o que a peça precisa mostrar. | Não reabre. Filtro de rótulo não é “depois a gente vê”: é condição de existência de qualquer motor jurídico. |

### 8.2. Tabela “não priorizar” da pesquisa (permanece fora; reabertura excepcional)

| Artefato | Motivo da recusa | Reabrir? |
|---|---|---|
| `celiudos/legal-bert-lgpd` | Rótulos DATE e MONEY; large 1,33 GB. F1 alto no que é **proibido** tokenizar. | Não, mesmo com DATE/MONEY desligados: schema e peso errados. https://huggingface.co/celiudos/legal-bert-lgpd |
| UlyssesNER / `ronunes/bertimbau-base-ulyssesner_br-bcod-self_learning` | Esquema legislativo (lei, órgão, evento). Tokenizaria tese. | Não. Schema errado. |
| John Snow Labs `legner_lener_*` | Mesmo LeNER, **licença comercial**, stack Spark. | Só se houver decisão expressa de licença paga. Duplica o item LeNER open. |
| OpenMed-PII-Portuguese-* | PII clínico (amount/currency). | Não. Domínio errado. |
| Stanza-PT NER, Flair `jneto04/ner-pt`, spaCy `pt_core_news_lg` como motor principal | Jornal; Flair marca TIME/VALUE; spaCy-lg **já é reserva** de carga. | Não como motor principal. |
| Albertina PT-BR, Legal-BERTimbau STS, MediAlbertina | Encoder ou NER médico/PT-PT. Sem cabeça útil para partes de EF. | Não para este produto. |
| OpenAI Privacy Filter | Já rejeitado: redige data; trata CNJ como conta. | **Não.** Confirmed pelo consolidado. |
| `lucasorrentino/Contractner` | Contratos SEC/inglês. | Não. |

### 8.3. Extração / serialização (consolidado, Métrica 1)

| Item | Decisão | Por quê | Quando reabrir |
|---|---|---|---|
| Trocar Docling por Marker | Fora | GPU no modo bom; colunas; risco de nuvem (`--use_llm`). | Só se o recorte deixar de ser eproc nativo em CPU. |
| Trocar Docling por MinerU | Fora | Stack pesada; pior no recorte *legal* de um teste 2026 (o consolidado: MinerU ganha paper acadêmico e perde aqui). | Idem. |
| `pymupdf4llm` como motor | Fora | AGPL; rebaixa lei/regulamento a lista Markdown. | Não como export padrão. PyMuPDF **já é** fallback e candidato a **auditor**, não a segundo Markdown. |
| LlamaParse / Unstructured cloud | Fora | Sai do app local. | Não, enquanto local for premissa. |
| OCR em página com texto nativo | Já recusado no código; **exceção em 26/09/2026:** camada de texto ilegível (fonte quebrada, > 2% de caracteres improváveis) | Inventa espaço e funde coluna — quando o texto nativo é legível. | Não, para texto legível. **OCR seletivo** em página que só tem carimbo eproc + imagem (contrato social digitalizado) ou + texto desenhado em vetor **não** é este item: a peça nativa não é relida. Veredito 3.0-E. |
| VLM do Docling (`granite_docling`) | Fora | Pode alucinar célula numérica na CDA. | Não. |
| Reconstrução fina da árvore eproc | Fora de escopo | Mapa completo de relações entre eventos. | Produto futuro. **Não** confundir com P3 (geografia). |
| Geografia: âncora de página + evento descolado (consolidado §6.3, sem CDA) | **Autorizada na Métrica 1**, na folga, sem contaminar o piloto de capa. **26/09/2026:** âncora = folha física e evento pelos marcadores do PDF (`fast_extract`), sem regex no corpo. No caminho legado a regex de “evento descolado” só age em cabeçalho (25/09, commit 7e12ce0). | Sem isso a análise jurídica não aponta o lugar do trecho. PyMuPDF já tem página; o Docling padrão perde. | Veredito 3.0-E. |
| Embelezar grade da CDA no Markdown | **Não priorizar** (P4) | CDA em regra não é sigilosa / não entra na análise anonimizada. | Só se um dia a CDA passar a ser o objeto da análise. |
| Serialização “completa” do consolidado (CDA uma linha por lançamento + página + evento) | Recortar | Página/evento **sim**; CDA **não**. | Implementar só o recorte de geografia. |
| Auditor PyMuPDF × Docling | **Autorizada**, útil para P3 e P5. **Superada em 26/09/2026:** o auditor mediu 41% de folhas perdidas e o PyMuPDF passou a ser o extrator. | Página nativa é barata; se o Markdown perder bloco, alertar ou injetar trecho cru. | Veredito 3.0-E. A cobertura por folha (nativa/OCR/em branco/não lida) substitui o auditor. |
| PyMuPDF por folha como extrator padrão (`fast_extract`) | **Aceito em 26/09/2026** | Âncora pela folha física, marcadores do eproc, sem teto de tempo, carimbo preservado; tabela com grade em Markdown, sem grade em colunas alinhadas. | Reabrir se uma folha nativa perder palavras em relação ao PDF ou se tabela Markdown desfigurar peça no lote real. |
| Furniture: marcar `[CABECALHO]` / `[RODAPE]`, não apagar | Autorizada com folga | Apagar come evento. | Depois da geografia, se o rodapé ainda atrapalhar. |
| Timeout / fatiamento para 100–200 MB (P5) | **Aberto como requisito.** **26/09/2026:** o teto do Docling cortou 206 de 251 folhas sem aviso; com o PyMuPDF não há teto e o texto nativo leva segundos. O custo passa a ser o OCR (~2–9 s por folha sem texto). | Hoje: sem teto de MB. | Medir o OCR em processo de 2–3 mil folhas; paralelizar se preciso. |

### 8.4. Produto / UI / política (já estáveis)

| Item | Decisão | Reabrir? |
|---|---|---|
| Streamlit / Gradio / Flet | Recusado | Não. NiceGUI permanece. |
| Tokens de papel `[EXECUTADA_1]` | Recusado | Não. Papel processual não é identidade. |
| Gemini como motor do app (`detect()` no original ou pós no MD) | **Sai** (20/09/2026) | A leitura dupla (original + tokenizado) é **fora** do app. Não reabre cliente de API neste produto. |
| Enviar original ou mapa **pelo app** | Recusado | O operador pode abrir o original no Gemini por conta própria. O app não envia. `mapa.json` continua privado. |
| Regex faminta / IDs unlabeled | Recusado | Não. Regras custom **estreitas** na aba Regras continuam opcionais. |

##### Checklist §8.8 — Gemini fora do app (20/09/2026)

Não é religar nem inverter o motor interno. É tirá-lo.

1. **Problema medido.** O checkbox “Usar Gemini” chama API no original; o plano 19/09 ainda previa pós interno. Nenhum dos dois é o fluxo real: o operador já confronta original e tokenizado **fora**.
2. **Métrica 1.** O app continua sem tokenizar União/`R$`/tese. O Gemini externo não escreve de volta no lote.
3. **Um fator.** Remover `GeminiEngine`, UI de chave e `gemini_enabled` é um passo de código, **depois** ou **à parte** do recorte da lista (2.0-A). Não mistura com GLiNER/limiar.
4. **Aborto.** Se a remoção religar `detect()` por engano, ou se o app passar a enviar arquivo: para. `mapa.json` não acompanha o MD.
5. **Condição.** Decisão expressa em 20/09/2026: leitura dupla fora do ambiente do app.

### 8.5. Camada determinística de dados (aceita em 29/08/2026, noite)

Decisão de produto, não de modelo: **duas camadas que não dependem de NER** para fechar empresa privada no corpo da peça.

| Camada | O que é | Decisão | Observações |
|---|---|---|---|
| **Lista de alvos (manual)** | Campo nas abas Anonimizar e Regras onde o usuário informa os nomes (empresas e, se quiser, pessoas) **e** os CPF/CNPJ da carteira que aquele caso deve tokenizar. O app localiza cada nome no texto inteiro, indiferente a maiúsculas, acentos e quebra de linha, e aplica o token. Em pessoa, também o prenome/sobrenome isolado (≥5 letras). Palavra com **6+ letras** aceita **1 caractere** trocado, inserido ou omitido (OCR: `OBDULIO`/`ABDULIO`). CPF/CNPJ da lista: só número com dígito verificador que o usuário listou (formatado, compacto ou, no CPF, letra de OCR); **não** é regex de 11 dígitos soltos. CNPJ de órgão público (raiz União/PGFN/INSS etc.) é ignorado. Se `dados/cnpj_sul.sqlite` existir, o CNPJ listado também localiza a razão nesta rodada, mesmo quando o número não aparece na peça. Não são muitos nomes por caso. | **Aceita e implementada**; reforçada em 30/08/2026 após o lote PR; fuzzy OCR em 31/08/2026. **Ciclo 5a:** denylist de partícula. **Ciclo 5c:** prenome/sobrenome banal só com rótulo de parte ou cola onomástica (DE/DA/DO); `Silva e Lopes` não se apoiam. **09/09/2026:** campo de CPF/CNPJ da carteira. Lista vazia = camada inerte. | Não é regex faminta. KEEP_TERMS, exclusão e denylist vencem. Empresa de uma palavra é aceita. CASTANHEIRA isolado continua; Silva isolado não. Frase `Lopes da Silva` sim. Fuzzy de 1 letra não roda em partícula comum. |
| **Base Dados Abertos do CNPJ (Receita Federal)** | Índice local (SQLite) do cadastro público de CNPJs: razão social, natureza jurídica. Quando um CNPJ válido aparece no texto, o app consulta o índice e tokeniza a razão social onde ela aparecer. Natureza 1xxx não vira empresa privada. | **Aceita e no código** (29/08/2026, noite): `src/anonimizador_app/cnpj_index.py` + `cnpj_lookup.py` + `tools/importar_cnpj_sul.py`. Sem o arquivo `dados/cnpj_sul.sqlite` a camada é **inerte**. Download **anual**, recorte **RS/SC/PR**. QSA (sócios) **não** entra neste passo. | Roda 100% local. Ressalva do recorte: devedora sediada fora do Sul fica fora do índice — a lista manual cobre. |

As duas camadas são determinísticas: precisão alta, risco de super-redação perto de zero, nenhum efeito sobre GLiNER, limiar ou heurística de capa.

### 8.6. Avaliação do parecer (anonimização + preservação estrutural)

O parecer (veredito + soluções A–H + recusas + plano em fases) foi **reavaliado neste ciclo**. A lógica se sustenta. Não reabre modelo; não baixa limiar.

| Tese do parecer | Avaliação | Consequência neste mapa |
|---|---|---|
| **1.a** Teto baixo de “modelo melhor”; ganho em dado determinístico e propagação | **Aceita.** LeNER, `arthrod` e fine-tune atacam o mesmo problema (NER em CAIXA ALTA) com a mesma classe de ferramenta. | Permanecem no **fim** da fila (§9, passo 7). |
| **1.b** Sem corpus/harness, cada experimento é no olho | **Aceita.** O §10 humano não escala para “um fator por vez”. | Auto-auditoria no lote **já existe** (`AUDITORIA.txt`, capa vs corpo; desde 5b também o mapa: dicionário/HTML/fusão/rótulo, sem PII). Label Studio / precisão por tipo **ainda pendente**, e continua **antes** de fine-tune. |
| **1.c** Heurística de capa frágil por layout (eproc/TRF4 ≠ PJe ≠ peça avulsa) | **Aceita.** Não é defeito: a âncora só precisa achar o nome uma vez; a lista de alvos e o CNPJ cobrem o resto. | Não se “conserta” a capa com GLiNER mais baixo. |
| Decisões sem ressalva (Docling, limiar 0,75, veto DATE/MONEY/LEGISLACAO, LLM local fora, geografia via IR, PDF em PARTES) | **Endossadas.** | Intocadas. |
| Recusas (Hyperscan, embeddings PII, truecaser treinado, perturbação, VLM de página) | **Endossadas.** | Permanecem fora (§8.7). |
| **H.** Tokenizar juiz/servidor em “Documento eletrônico assinado por…” | **Não**, até decisão expressa. | O §10.2.4 continua abortando se a capa pegar magistrado. |
| Fases 0→5 (medir → instrumento → geografia → determinístico → sombra de caixa → modelo só se faltar) | **Aceitas.** A frase do parecer vale: *medir antes de trocar; esgotar o determinístico antes de outro ciclo estatístico.* | §9 abaixo. |

Ajuste de produto já absorvido (Roger): **duas camadas** — lista manual (poucos nomes por caso) **e** Dados Abertos do CNPJ, download **anual**, recorte **Sul**. Ressalva mantida: o arquivo de razões sociais da Receita **não traz UF**; o filtro cruza estabelecimentos de RS/SC/PR. Devedora fora do Sul fica coberta pela lista manual.

O que este ciclo **não** antecipa: RapidFuzz/Aho-Corasick (ainda depois do CNPJ); corpus Label Studio (ainda passo 4); ONNX/cache/mapa-por-processo (P5). A passada-sombra está **no código e desligada**, para não contaminar o teste de capa.

### 8.7. Parecer de 29/08 (soluções ainda não mapeadas na pesquisa HuggingFace)

Aceito como catálogo. Não reabre modelo; não baixa limiar.

| Item | Decisão | Estado |
|---|---|---|
| **A. Dados Abertos CNPJ** | Aceito; anual; Sul; QSA depois | Código pronto; falta o download humano para o SQLite existir |
| **A-bis. Lista de alvos manual** | Aceito pelo Roger no mesmo ciclo | **Implementado** (aba Anonimizar + aba Regras). Em 30/08: casa acento, quebra de linha e partículas do nome já informado (≥5 letras, fronteira de palavra). Em 31/08: 1 caractere de OCR em palavra ≥6 letras. Não explode a razão social em “COMÉRCIO”/“TRANSPORTES”. Em 09/09: campo de CPF/CNPJ da carteira (com DV; órgão público fora). |
| **A. IBGE municípios / prenomes** | Adiado | Depois da base CNPJ; prenome só como *filtro* de NER, nunca detector |
| **B. Passada-sombra de caixa** | Aceito como experimento de um fator | **No código, desligada por padrão** (aba Regras). Mesmo GLiNER, 0,75. Só ligar **depois** do teste de capa |
| **C. Aho-Corasick + RapidFuzz** | Aceito depois do CNPJ | Só variantes de entidade *já confirmada*; limiar alto. Recorte de sufixo societário já está na lista de alvos |
| **D. Corpus dourado / Label Studio** | Aceito como instrumento, não como produto | Anotar na máquina; PII não sai. Antes de LeNER/`arthrod` |
| **E. Auto-auditoria pós-lote** | Aceito | **Implementado**: `AUDITORIA.txt` (KEEP_TERMS, token×R$/data, capa vs corpo, âncoras de página) |
| **F. ONNX / cache de extração / mapa por processo** | Aberto com P5 | Depois da geografia; mapa por processo quando PARTES rodarem em lotes separados |
| **G. Geografia via IR do Docling** | Aceito | **Implementado o recorte** (acentos por *fold*, não regex frágil). Auditor PyMuPDF ainda não cruza conteúdo |
| **H. Tokenizar juiz/servidor** | **Não**, até decisão expressa | O §10 continua abortando se a capa pegar magistrado |
| Hyperscan, embeddings PII, truecaser treinado, perturbação, VLM de página | Recusados | Permanecem fora |
| Acesso em janela de app + link próprio | Pedido do Roger neste ciclo | **Implementado**: `iniciar_app.vbs` / atalho; `http://127.0.0.1:8765`; `iniciar_navegador.bat` se quiser o Chrome/Edge |

### 8.8. Checklist para reabrir um item da tabela

Antes de gastar um ciclo em algo hoje descartado, responder por escrito (pode ser um parágrafo neste arquivo, numa data nova):

1. Qual problema **medido** (P1–P12) isso ataca — não o card do modelo.
2. A Métrica 1 continua cortando? O que **não** pode virar token neste teste?
3. É **um** fator? (Se a resposta for “modelo novo **e** limiar novo **e** âncora nova”, recortar.)
4. Qual o critério de aborto? (Copiar o espírito do §10.3: órgão/`R$`/tese/comarca = para.)
5. A condição da coluna “Quando reabrir” já ocorreu? Se não, não reabre.

---

## 9. Próximos passos (ordem que vale)

### Fila 3.x — estrutura primeiro (26/09/2026, vale sobre as filas abaixo)

**Decisão do Operador (26/09/2026).** A estrutura vale mais que a perfeição da anonimização. A IA que recebe o exportável tem de conseguir analisar o caso com fidelidade: sem equívoco e sem perda de informação (folha, valor, data, tabela, evento, página). A anonimização está em nível aceitável (Veredito 2.1); o acabamento fino fica para uma passada do Operador em IA institucional. **Ajuste de recall está pausado.** Token que destrói conteúdo (come valor, data, código, termo jurídico, rótulo, referência a evento) é defeito de **estrutura**, não de anonimização: entra nesta fila.

**Régua:** `tools/medir_estrutura.py <pasta dos PDFs> <pasta do lote>` (só contagens; compara PDF × texto extraído × exportável). Linha de base, lote eproc de 25/09 (`lote_20260926_083235`): 698/698 âncoras; 0 folhas não lidas; 0 folhas com extração fraca; valores em R$: 563 no PDF, **0 perdidos**; datas: 1.230, **10 perdidas na troca**; **31 tokens colados em número**; 1 token em cabeçalho de tabela; 0 título só-rótulo; 0 evento partindo frase. Anonimização (pausada, só para não regredir): `tools/medir_vazamento.py saidas/privados/gabaritos/<caso>.json <lotes...>` — o gabarito tem dados reais e fica só na pasta privada.

| # | Item | Evidência (lote de 25/09) | Aceite |
|---|---|---|---|
| E1 | **Token que come conteúdo.** (a) CNPJ alfanumérico casou “CONTRIBUICAO” + espaços de coluna + dia da data (`[CNPJ_n]/09/2023`, 20×; são as 10 datas perdidas): exigir dígito na base e no máximo um espaço entre as partes. (b) Telefone dentro do protocolo CNIB (`[TELEFONE_n].…-IA-773`, 4×) e ID colado em código mais longo: descartar span encostado em `[.,/-]dígito`. (c) Endereço que atravessa célula `|` ou vão de coluna, ou termina no meio de número/código (`[ENDERECO_n],12`, `[ENDERECO_n].350`, `[ENDERECO_n].1-A-3`): higiene corta no limite. (d) Rótulo e referência trocados (“Tipo de Devedor”, “r. despacho Ev. 56”, prenome solto compartilhado): conferir no lote novo. | régua | 0 data e 0 valor perdidos na troca; 0 token colado em número; sem regressão no gabarito. |
| E2 | **Página de separação do eproc.** Rótulos (“Tipo documento: / Evento: / Data: / Usuário:”) saem separados dos valores; a IA tem de adivinhar os pares. Renderizar como “Evento N — TIPO — data hora — usuário”. | leitura do exportável | metadado do evento em uma linha em toda folha de separação. |
| E3 | **Índice do lote** (`INDICE.md` no exportável): por arquivo, eventos e documentos (marcadores do PDF) com faixa de folhas, folhas lidas por OCR, tabelas, e documento que continua de um PARTE para o outro. | marcadores já extraídos | índice bate com os marcadores; citação “evento, documento, página” localizável. |
| E4 | **Fidelidade do OCR.** RapidOCR (modelo mobile) perdeu ~4 de 45 linhas numa folha de contrato. Medir perda de linha (referência: a 5ª alteração contratual existe vetorizada num PARTE e com texto nativo na execução fiscal, evento 10) e testar escala maior / modelo server / segunda leitura. | 1 folha auditada à mão | perda de linha medida por lote; meta ≤ 1%. |
| E5 | **Legenda do elenco sem nomes** (`ELENCO.md`: token → tipo → nº de ocorrências → papel quando a capa diz: exequente, executado, requerido, advogado da parte). A IA de análise sabe quem é a executada sem saber o nome. | o pipeline LLM já grava; o local não | legenda no exportável; nenhum nome nela. |
| E6 | **Escala (P5).** OCR é o custo (~6 s por folha sem texto): paralelizar por processo antes de 2–3 mil folhas. | 165 folhas de OCR em ~12 min | tempo por folha de OCR medido; lote de 3 mil folhas viável. |

**3.0 com LLM (LangExtract), aguardando decisão do canal pelo Operador.** O código de descoberta já existe (`llm_discovery.py`, `pipeline_llm.py`, `tools/anonimizar_llm.py`). Falta: (L1) seletor de motor na interface (“Local 2.1” / “LLM”) com aviso do canal; (L2) guardar a chave fora do projeto (variável de ambiente ou Gerenciador de Credenciais do Windows), nunca em `config.json`, e registrar o canal usado na AUDITORIA e na linha de versão; (L3) tela de revisão do elenco (tipo, papel, ocorrências, trocar/manter); (L4) aceite no lote de 25/09 com as duas réguas. Alerta registrado: com LangExtract, o texto vai ao provedor com IDs mascarados, mas **nomes, fatos e valores em claro**; o que decide o tratamento é o canal da chave (gratuita: pode treinar e ter revisão humana; paga: não treina; Vertex AI institucional em São Paulo: contrato de nuvem).

### Ciclo 4 (02/09/2026) — concluído

Determinístico, medido no PARTE_1, gabarito APROVADO (`lote_20260902_210844`). 4a tabela DETRAN (placa/RENAVAM/chassi); 4b formulário Receita (nascimento/filiação/título); 4c eproc de procurador; 4d domínio/marca da empresa alvo. Intocado: GLiNER, limiar 0,75, Docling, OCR, regex de CPF sem rótulo.

### Fila 2.0 (substitui a ordem 1–8 abaixo para o que ainda não começou)

Intocado em todo o 2.0: limiar GLiNER **0,75**; Presidio sem NLP; veto DATE/MONEY/LEGISLACAO; item H (juiz); P10 sem rótulo; `mapa.json` não viaja com o exportável. Gemini **não** é cliente do app.

| Ordem | O quê | Condição | Não misturar com |
|---|---|---|---|
| **2.0-A. Alvos (agora)** | **Código 20/09/2026.** Recortar a persistida **a este IDPJ** + política: empresa = frase completa e prefixo com **≥2** palavras de conteúdo; **sem** partícula de 1 palavra extraída da razão. Pessoa: 5c. Backup da acumulada, como em 5a. Reprocessar o mesmo PDF (cache). Aceite = lote. | CTN/CNAE em claro; Paraná/Campo/casado = 0 no mapa; capa §10; `LTDA` não explode em claro porque a partícula comeu o miolo | GLiNER; limiar; denylist nova além desta política |
| **2.0-B. Tirar o Gemini do app** | **Código 20/09/2026.** `GeminiEngine`, checkbox, chave e `gemini_enabled` saíram. KEEP_TERMS fica. A leitura dupla é no Gemini **fora**. Aceite de lote permanece o de 2.0-A. | App não chama API | Limiar |
| **2.0-C. Geografia P3** | Medir âncoras Docling + eventos descolados. Auditor PyMuPDF × conteúdo. O Gemini externo lê o que o MD tiver; o app cria a âncora. | Não muda NER. Sem CDA (P4). | Troca de extrator |
| **2.0-D. Volume P5** | Timeout Docling, hábito PARTES, cache; GLiNER não em 200 MB de uma vez | 2–3 mil folhas é requisito | Marker, MinerU, OCR nativo |
| **2.0-E. PyMuPDF primário (só se a tabela continuar ilegível)** | Um lote: PyMuPDF como extrator **default**. A reconstituição de grade, se houver, é no Gemini **fora**. Não é `pymupdf4llm` (AGPL) nem Marker. | `R$`/datas iguais ao lote Docling do mesmo PDF | MinerU, VLM, OCR nativo |
| **2.0-F. GLiNER opcional** | Default **off** só se, na conferência externa, o corpo vazar pouco (capa já fecha por alvos+heurística) | União intacta; limiar continua 0,75 se religar | Queda de limiar; LeNER no mesmo passo |
| **2.0-G. CNPJ Sul** | Inalterado: Roger baixa ZIPs; reduz vazamento no MD que o Gemini externo vai ler | Lista cobre o caso pontual | QSA neste passo |
| **Fora da fila 2.0** | LeNER, `arthrod`, `rafola`/`ottema`, GLiNER2, passada-sombra, Label Studio, fine-tune, Privacy Filter, regex unlabeled, tokens `[EXECUTADA_n]`, Hyperscan, embeddings PII, árvore fina eproc, grade CDA, juiz/H | O FN residual é o passo B | — |

Histórico 1.0 (ciclos 4–5c) permanece abaixo só como ata. A ordem operacional passou a ser a tabela 2.0.

### Ciclo 5a (03/09/2026) — código feito; aceite = lote

Denylist de partícula + recorte da lista + separação de pastas quando coincidem. Intocado: GLiNER, limiar 0,75, Docling, OCR, LeNER.

### Ciclo 5c (04/09/2026) — código feito; mesmo aceite de lote

Partícula comum só com apoio; filial CNPJ; SISPAR/Debcad rotulados; matrícula em lista; CEP de coluna; eproc na capa; apóstrofo/`AVE`; cues de juiz/PFN. Intocado: GLiNER, limiar 0,75, Docling, OCR, LeNER.

| Ordem | O quê | Condição | Não misturar com |
|---|---|---|---|
| **0. Feito (5a)** | Recortar a lista ao feito; denylist de partícula; se raízes coincidem, `.md` em `exportaveis/` e mapa em `privados/` | Medir no reprocessamento: Paraná/Campo/casado/março = 0 no mapa | LeNER; limiar |
| **0b. Feito (5b)** | Higiene de span (HTML, rótulo, fusão, teto); AUDITORIA do mapa; CNPJ `0001- 10` | âncora HTML ≠ PERSON; SIDA não tokeniza `Conta:`/`CEP:` | LeNER; limiar |
| **0c. Feito (5c)** | Partícula comum só com cola onomástica ou rótulo; NER recusa nome só banal; filial por raiz; SISPAR rotulado; matrículas no plural; CEP de coluna; eproc na capa; `AVE`/apóstrofo | Silva/Lopes isolados ≠ token; `o réu Silva` sim; `LOPES DA SILVA` sim; `Silva e Lopes` ≠ token; CASTANHEIRA isolado sim | LeNER; limiar; RapidFuzz; dicionário PT inteiro |

A fila operacional 1–8 desta tabela foi **substituída** pela fila 2.0 no topo desta seção. Não religar LeNER/sombra/Marker “porque o 1.0 tinha um passo 6”.

**Se o piloto de capa falhar:** não compensar com GLiNER PII nem com LLM no app. Revisar âncora, janela e recorte da lista. Se as âncoras **não estiverem** no Markdown extraído, o diagnóstico volta para extração/layout (**P3**). O Gemini externo não conserta P13.

---

## 10. Como julgar o teste de capa (lote real)

O corpus sintético **já passava** e **não** mede este piloto. Mede-se no lote eproc de execução fiscal.

Nas atas e neste arquivo: falar em “a executada”, “advogados da capa”, “polo ativo público”, “lote eproc”. Sem copiar nome, CPF ou razão social reais.

### 10.1. Sucesso (tem de ocorrer **junto**)

1. Razão social **privada** da capa ou “Nome do Devedor” → `[EMPRESA_n]`.
2. Advogados da capa da parte privada → `[PESSOA_n]` (duas ou mais palavras; âncora `ADVOGADO` / `Procurador(es)` / `OAB`).
3. Endereço com via **e** número → `[ENDERECO_n]`; cidade, UF, bairro e CEP rotulado **não** viram endereço (CEP continua com o Presidio, se rotulado).
4. **Não** há token em União, Fazenda Nacional, PGFN, INSS, TRF4, data, `R$`, evento, SISBAJUD/RENAJUD/CNIB/INFOJUD, tese, artigo, sistema.
5. IDs que já saíam tokenizados continuam; a heurística não briga no overlap.
6. Nomes **só** no mérito **podem** continuar. Isso **não** reprova o piloto.
7. Sobrenome banal isolado (Silva/Lopes sem rótulo de parte e sem cola `da`/`de`) permanece; `rel. min.` / `ministro` não viram pessoa da lista; CASTANHEIRA isolado tokeniza.

Observação: o nome **já extraído da âncora** pode aparecer tokenizado em juntadas posteriores (“juntada da executada”). Isso não é varrer o mérito; é o mesmo dado da capa.

### 10.2. Aborto (qualquer um encerra o desenho — não se “ajusta limiar”)

1. Token em órgão, `R$`, data, comarca, UF solta, evento eproc ou sistema.
2. A regra só funciona varrendo a peça inteira ou caindo para lista de nomes sem âncora.
3. Polo ativo público vira `[EMPRESA_n]`.
4. Juiz, diretor de secretaria ou procurador da União em **linha de evento** no meio dos autos tokenizado pela mesma regra.
5. Queda de limiar ou troca de checkpoint “para ajudar” o piloto.

### 10.3. Depois do teste, atualizar este arquivo

Acrescentar em §11 uma linha: data, passou / abortou / passou com ressalva, e o que isso libera no §9. Sem PII.

---

## 11. Histórico curto do ciclo

| Quando | O quê |
|---|---|
| Até 0.3.0 | Extração Docling; Presidio de IDs; GLiNER local; Gemini pausado; fidelidade no README; aba Regras. |
| Lote eproc (PARTE_8) | Conteúdo jurídico ok (datas, `R$`, teses, União); amplitude da capa ruim (IDs sim; pessoa/empresa/endereço não). Geografia (P3) já falhava — não se lia como “estrutura boa”. |
| Correções pontuais | OAB eproc; localizador case-insensitive; filtro vara/comarca; spaCy score de reserva. |
| 29/08/2026 | Pesquisa NER externa (catálogo). Resposta de produto (ordem invertida). Consolidado (acrescenta Métrica 1 de serialização). |
| 29/08/2026 | Heurística de cabeçalho no código; 57 testes. |
| 29/08/2026 (este arquivo) | Mapa de governança. Teste humano do mesmo PDF, em paralelo. |
| 29/08/2026 (noite) | P3: geografia autorizada na folga, sem contaminar o piloto. P4 (CDA) sai da fila (sangra, não gasta ciclo). P5: sem teto de MB; timeout Docling 15 min; 2–3 mil folhas / 100–200 MB registrado como requisito. |
| 29/08/2026 (noite) | Governança retificada pela avaliação da resposta NER: catálogo era entregável, não defeito; “não trocar Docling” ≠ “nada a fazer”; `arthrod` = melhor candidato isolado da família; `rafola`/`ottema` fora do ciclo, não perpétuo; fine-tune = teto no corpo. Veredito travado no topo deste arquivo. |
| 29/08/2026 (noite) | Aceita a **camada determinística** (§8.5): lista de alvos manual na aba Regras + base local dos Dados Abertos do CNPJ (download anual, recorte região Sul). Entra como passo 3 do §9, antes do LeNER. Não toca GLiNER, limiar nem heurística de capa. |
| 29/08/2026 (noite) | **Lista de alvos implementada**: caixas “Empresas a anonimizar” e “Pessoas a anonimizar” na aba Regras; motor `ListaDeAlvos` antes dos demais contextuais; 64 testes verdes. Vazia por padrão — não interfere no teste de capa pendente. Base CNPJ segue como próximo item da camada. |
| 29/08/2026 (noite) | Parecer (anonimização + estrutura) absorvido: determinístico > modelo; medição antes de LeNER; passada-sombra antes do LeNER; juiz/servidor **não** tokeniza (H). Lista de alvos também na aba Anonimizar. Janela de app + atalho + `http://127.0.0.1:8765`. CNPJ no código (inerte sem SQLite). Geografia recortada no Docling. `AUDITORIA.txt` por lote. |
| 29/08/2026 (noite, 2º ciclo) | **Reavaliação do parecer:** teses 1.a/1.b/1.c e recusas endossadas; §8.6 gravado. Acesso em janela (`iniciar_app.vbs`, atalho, link próprio). Lista de alvos: sufixo societário. Geografia: *fold* de acento (NFD/cedilha). Passada-sombra **no código, desligada**. Auditoria: capa vs corpo. |
| 30/08/2026 | Lote eproc PR (PARTE_1, PDF já “ocred”): amplitude **melhorou** (IDs + `[PESSOA]`/`[EMPRESA]`/`[ENDERECO]` na capa e no corpo; União/`R$`/Fazenda/SISBAJUD intactos). **Passou com ressalva** (§10). Prenome/sobrenome soltos e razão com acento/quebra de linha em claro; cidade/UF na qualificação permanecem por regra; páginas de contrato social digitalizado sem camada de texto — carimbo eproc enganava o OCR. Ajustes no código no mesmo dia: lista de alvos flexível (acento, `\s+`, partículas ≥5 letras); OCR se página = carimbo + imagem; KEEP_TERMS cobre “dívida ativa” / “documento extraído”. |
| 30/08/2026 (noite, lote 224820) | Mesmo PDF PARTE_1, com lista de alvos preenchida. **Passou com ressalva** (§10). Capa: executada, sócios e advogados da parte privada tokenizados; polo ativo público, `R$`, Dívida Ativa, SISBAJUD/RENAJUD/CNIB e juiz da capa intactos. Lista de alvos cobriu acento, quebra de linha e partículas. **Regressão:** zero `[ENDERECO]` (o lote 17h32 tinha 25). Petição inicial deixa via+número em claro; CEP no formato `99.999-999` também. Partícula de sócio comeu o primeiro nome de outra empresa do grupo (token `[PESSOA]` no lugar da razão). Relatores de ementa e doutrina viraram `[PESSOA]`. CONTRSOCIAL/OUT digitalizados continuam só com carimbo eproc (OCR não recuperou o contrato). Alerta de “token encostado em data” é falso positivo (carimbo “Registrado por” + timestamp). |
| 30/08/2026 (noite, correção) | Camada determinística, sem mexer no GLiNER/Docling: âncora `sediada`/`domiciliado` + vírgula antes do número; partícula de pessoa não come razão (`COMÉRCIO`/`LTDA`); CEP com ponto de milhar; auditoria avisa via+número sem `[ENDERECO]` e só alerta data colada ao token. |
| 30/08/2026 (noite, OCR) | Leitura de página-imagem: o gatilho usava todos os XObjects do eproc (ícones) e ligava OCR no PDF inteiro, com motor chinês; na prática o contrato digitalizado saía só com o carimbo. Agora o Docling permanece nativo e o RapidOCR (latim) lê **só** a página com scan cobrindo a folha e pouco texto nativo. Não é OCR em página nativa (§8.3). |
| 31/08/2026 (lote 102322) | Mesmo PDF PARTE_1 após âncora de endereço, CEP com ponto, partícula×razão e OCR seletivo no código. **Passou com ressalva.** Partícula não come mais a outra empresa (`[EMPRESA]` no polo). CEP `99.999-999` tokenizado (15→25). `[ENDERECO]` voltou (0→5), mas via quebrada no meio da linha na qualificação da inicial permanece em claro. CONTRSOCIAL/OUT digitalizados **continuam só com carimbo** — o OCR seletivo **não chegou a gravar** neste run (`AUDITORIA` sem “Paginas lidas por OCR”). União/`R$`/SisbaJud/RENAJUD/CNIB e juiz da capa intactos. Segunda razão do grupo (não estava na lista de alvos) segue em claro, como esperado. |
| 31/08/2026 (lote 110056 + causa raiz do extrator) | Lote rodou já com o motor v5 latino e **de novo sem OCR e sem marcador**. Diagnóstico definitivo: o `docling-parse` (backend C++) **não abre caminho com acento no Windows** — a pasta `TESTE ANONIMIZAÇÃO` derruba o Docling em 0,04 s e **todos os lotes deste PDF caíram no `pymupdf-reserva`**, que não chamava o OCR seletivo. Três correções: (1) caminho não-ASCII passa a converter por **cópia temporária com nome ASCII**; (2) o caminho de reserva **também aplica o OCR seletivo**; (3) a AUDITORIA agora **nomeia o extrator** e emite ALERTA quando cair na reserva. Prova real fim a fim no PARTE_1: extrator `docling-ocr`, **53 páginas lidas por OCR, zero falhas**, 129 âncoras, 6 min. 94 testes verdes. |
| 31/08/2026 (lote 112803) | Primeiro lote em que o circuito OCR fechou. **Passou com ressalva.** Extrator `docling-ocr`, 53 páginas lidas, 129 âncoras, União/`R$`/SisbaJud/RENAJUD/CNIB intactos. Amplitude dispara com o texto novo (CEP 25→75, CNPJ 16→63, CPF 25→59, EMPRESA 59→125, ENDERECO 5→21, PESSOA 125→263). Ressalvas: (1) razões BROGGI/CRITÉRIUM fora da lista de alvos e grafias OCR (`LIDA`, `CONFECCÇÕES`, `ABDULIO`/`OBDULO`) ficam em claro; (2) vias na inicial sem âncora `sediada` (`Rua Suindara, 177`, `RUA NEI JOSÉ…, 563/579`); (3) partícula `BROGGI`/`ALTMANN` tokeniza marca e doutrina/relator vira `[PESSOA]`; (4) alerta `R$` é falso positivo de tabela (`CAPITAL R$` + sócio na linha seguinte); (5) juiz da capa tokenizado (`[PESSOA_1]`). |
| 31/08/2026 (pós-112803) | Quatro correções de fidelidade/amplitude, sem mexer em GLiNER/limiar: (a) BROGGI e CRITÉRIUM na lista de alvos do caso; (b) âncoras `sede para` / `passa a ser` / `filial em` + via com quebra de linha e CAIXA ALTA; (c) 1 caractere de OCR só na lista de alvos, palavra ≥6 letras; (d) `drop_protected_spans` descarta juiz da capa, relator de ementa e autor de doutrina (GLiNER), salvo se o nome estiver na lista de alvos. Item H permanece: magistrado **não** se tokeniza. |
| 31/08/2026 (causa do OCR mudo + correção) | Diagnóstico no PDF real: o RapidOCR **recusava** a configuração — `lang_type=latin` não existe no PP-OCRv6 pequeno (default da 3.9.x) — e a exceção era engolida página a página; por isso o lote saiu sem OCR e sem aviso. Correção dupla: (1) motor fixado em **PP-OCRv5 mobile latino** (enums `OCRVersion.PPOCRV5` + `LangRec.LATIN` + `ModelType.MOBILE`), validado nas folhas digitalizadas do PARTE_1 — 53 páginas elegíveis, ~2 s/folha após a carga, contrato social e prints de site legíveis; (2) **falha de OCR deixou de ser silenciosa**: `apply_page_ocr` grava `<!-- ocr-falhas: N -->` e a AUDITORIA emite ALERTA quando folha digitalizada fica só com o carimbo. Escala de renderização mantida em 2×: testes com 3× e 4× deram ganho ≤2% (o limite é o scan de origem, não o DPI). 91 testes verdes. |
| 02/09/2026 (Ciclos 1–3) | Amplitude determinística no mesmo PARTE_1: lista de alvos (partícula, OCR 1 caractere, prefixo de razão, endereço). Baseline C3 `lote_20260902_172813`, 189 testes. Três vazamentos deixados para o Ciclo 4: domínio `broggividros`, códigos eproc `PR077705`/`PR077717`. |
| 02/09/2026 (Ciclo 4, lote 210844) | **Concluído.** Cabeçalho de coluna = rótulo (P10); placa/RENAVAM/chassi; nascimento/título/filiação; eproc de procurador com `PFN`+dígitos em claro; domínio/marca da empresa alvo no mesmo `[EMPRESA_n]`. Gabarito APROVADO, 0 falhas. Âncoras 129; IDs C2 e endereços C3 estáveis. 218 testes. Sem GLiNER/limiar/Docling/OCR. |
| 03/09/2026 (Ciclo 5a) | Lote IDPJ `20260902_230718` abortou por super-redação (P13): lista acumulada 167+119; Paraná/Campo/casado/março/sistemas. **Mitigado no motor:** denylist de partícula (seed + match fuzzy); parêntese/`em recuperação judicial` não geram partícula; lista recortada a 15 empresas + 5 pessoas deste feito (backup `config.lista_acumulada_20260903.json`); se raízes coincidem, `.md` em `exportaveis/` e mapa em `privados/`. Intocado: GLiNER, limiar, Docling. |
| 04/09/2026 (Ciclo 5b) | Antes do reprocessamento: higiene de span (HTML/rótulo/fusão/teto; 1 `\\n` na razão da lista permanece); `AUDITORIA.txt` varre o mapa sem PII e conta CPF/CNPJ formatado em claro; CNPJ com espaço antes do DV. Intocado: GLiNER, limiar, Docling, LeNER. |
| 04/09/2026 (Ciclo 5c) | Encadeamento antes do teste: partícula comum só com cola onomástica ou rótulo (não janela de 40); frase traseira Lopes da Silva; `ministro`/`rel. min.`; NER recusa nome só banal; filial CNPJ; SISPAR/Debcad rotulados; matrículas no plural; CEP de coluna; eproc na capa; apóstrofo e `AVE`. Intocado: GLiNER, limiar, Docling, LeNER. |
| 06/09/2026 | KEEP_TERMS: SNIPER, CENSEC, CCS-Bacen, SIMBA, SERASAJUD, AGU, PGF, PGE, PGM (não Serasa). OAB canônica UF+número (`OAB/SP 123.456` = `OAB 123456/SP`). RG preserva DV `X`. `_PFN_CUE`: procurador do Estado/Município e PGF/AGU por cargo, não por sigla solta na linha. Intocado: GLiNER, limiar, Docling, CID, CNH, ETL CNPJ. |
| 09/09/2026 | Lista de alvos: campo **CNPJ e CPF da carteira** (aba Anonimizar e Regras). Só número com DV que o usuário listou; CNPJ de órgão público ignorado; planilha Nexus passa a trazer os documentos além dos nomes. Com o SQLite Sul no disco, CNPJ da lista localiza a razão mesmo se o número não estiver na peça. Intocado: GLiNER, limiar, Docling. |
| 19/09/2026 | Abertura do atalho: o processo da janela nativa (NiceGUI/pywebview) deixou de ser tratado como “cópia na memória”; subprocessos do launcher rodam sem janela do System32; segundo clique foca a janela existente; porta 8765 ocupada por `http.server` é liberada sem diálogo. Log em `saidas/app.log`. Intocado: GLiNER, limiar, Docling. |
| 19/09/2026 (lote 213102) | Mesmo PARTE_1 (cache `docling-ocr` de 02/09; 53 págs OCR; 129 âncoras). **Abortou por P13.** Lista persistida acumulada de novo (240 empresas + 59 pessoas; CPF/CNPJ da carteira vazio). Capa: executada, sócios e advogados da parte privada tokenizados; União/`R$`/SisbaJud/RENAJUD/CNIB e juiz da capa intactos. Mérito desfigurado: CNAE/objeto social e CTN art. 133 (comércio, indústria, “nome individual”). Segunda razão do grupo em claro. Próximo fator: recortar a lista a este feito, como em 5a — sem GLiNER/limiar. |
| 19/09/2026 (plano 1b) | Gemini pós-passada no Markdown já tokenizado (estrutura + leftovers). `detect()` no original permanece pausado. |
| 19/09/2026 (2.0) | Avaliação do zero: suíte 295 OK (não mede piloto); lote 213102 P13 (`[EMPRESA]` 169→482; CNAE/CTN comidos; 129 âncoras). **Afrouxar = menos gula local**, não limiar 0,3. |
| 20/09/2026 | Gemini **fora do app**: o operador lê original e MD tokenizado lá. Motor interno (`GeminiEngine`, checkbox, chave) **sai**. O app não envia arquivo nem `mapa.json`. 2.0-B passa a ser essa remoção, não pós-passada interna. |
| 20/09/2026 (2.0-A/B no código) | Partícula de 1 palavra em empresa desligada. Lista persistida: acumulada 240+59 em `config.lista_acumulada_20260920.json`; recorte deste PDF (frase completa no cache) = 15 empresas + 18 pessoas. `GeminiEngine`/UI/chave/`tools/smoke_gemini.py` removidos. KEEP_TERMS permanece. Aceite = reprocessar o PARTE_1 + `AUDITORIA.txt` + §10. Intocado: GLiNER, limiar 0,75, Docling. |
| 20/09/2026 (lote 222815) | Mesmo PARTE_1, cache `docling-ocr` 02/09. **Medido.** `[EMPRESA]` 482→141 ocorrências, 35→**5** IDs (gabarito 169/5). CTN 12 e CNAE 4 em claro; `fundo de comércio` 1; União 7 / `R$` 66 / SisbaJud 1; Paraná/Campo/casado/sistemas = 0 no mapa. Capa: `[EMPRESA]` 1 (sem partícula). 129 âncoras. `gemini_used` falso. Suíte 299 OK (não substitui este lote). |
| 25–26/09/2026 (lote eproc 192945) | Primeiro lote completo de um IDPJ + execução fiscal (11 PDFs, 698 folhas). Medido contra o PDF: **289 folhas (41%) sem conteúdo**, âncoras deslocadas em dois PARTEs, cada parte dividida em 4 a 8 tokens. Ver Veredito 3.0-E. |
| 26/09/2026 (Veredito 3.0-E, lote 034316) | **Passou com ressalva.** PyMuPDF por folha como extrator padrão; OCR em escaneada, vetorial e fonte quebrada; tabela por grade, colunas alinhadas e tabela por camada; cobertura com trava de envio; geografia pelos marcadores do PDF, sem regex no corpo. 698/698 folhas, 0 não lidas. Regressão de anonimização achada (sucessora 3 → 213) e corrigida no extrator (→ 4); resíduo de ~17 menções nas folhas antigas. Libera: propagação de nome e ID já detectados (3.0 sem LLM). Suíte 369 OK. |
| 26/09/2026 (Veredito 2.1, lote 083235) | **Passou.** Propagação do lote em duas fases: elenco das grafias por entidade, busca em todo o texto só nas lacunas, um token por entidade, IDs e raiz de CNPJ pelo valor exato. Nenhuma entidade do gabarito com mais menções em claro que no lote de 25/09; juízes iguais; partes com 1 token (sócio A com 2). Falso positivo “pessoa jurídica” achado na primeira rodada e corrigido. Versão visível na janela e em cada lote (0.4.0). Suíte 390 OK. |

---

## 14. Anonimizador 2.0 — teses, regras e catálogo

Decisão expressa de 19/09/2026, **retificada em 20/09/2026**. Não apaga o 1.0: o histórico §§1–13 continua. A fila que **vale** é a 2.0 no §9.

### 14.1. Tese

O 1.0 tratou o Gemini como detector **dentro** do app (pausado) e o residual como aviso. A passada local maximizou recall e abortou em P13.

O 2.0, depois da retificação: o app só produz Markdown tokenizado e mapa privado. **Quem lê original e versão tokenizada é o Gemini fora do app**, no ambiente do operador. Lá ele confronta vazamento e estrutura. O app não chama API, não guarda chave, não reescreve o lote.

Afrouxar as rédeas = menos gula local, para essa leitura externa ficar útil. Não é limiar 0,3. O Gemini externo **não** grava de volta no app: se a local comer “fundo de comércio”, a tese já saiu desfigurada.

O `mapa.json` **não** acompanha o Markdown. Abrir o PDF original no Gemini é gesto do operador, não função do Anonimizador.

### 14.2. Pipeline 2.0 (alvo)

```
PDF → extração (Docling; PyMuPDF primário só no 2.0-E) + geografia
    → Presidio (IDs com DV / rotulados)
    → Lista deste feito (frase + prefixo ≥2; sem partícula de 1 palavra em empresa)
    → Capa / tabela rotulada / formulário / via+número
    → GLiNER 0,75 (liga até o 2.0-F)
    → KEEP_TERMS + higiene
    → tokens + mapa privado + AUDITORIA
    → [fora] operador abre original e MD tokenizado no Gemini
```

### 14.3. Regras — o que muda e o que não

| Regra | 1.0 | 2.0 |
|---|---|---|
| KEEP_TERMS / órgão / `R$` / tese / evento | Não tokeniza | Igual. |
| Empresa na lista | Partículas da razão (1 palavra ≥5) | Frase completa + prefixo **≥2** palavras de conteúdo. Sem `COMÉRCIO`/`INDÚSTRIA`/`INDIVIDUAL` como semente. |
| Pessoa na lista | 5c: cola ou rótulo | Igual. |
| CPF/CNPJ da carteira | Listado com DV | Sobe: fecha ID sem partícula de razão. |
| Regex sem rótulo (P10) | Proibida | Proibida. |
| GLiNER 0,75 no texto inteiro | Default on | On até conferência externa; depois pode desligar (2.0-F). Limiar **não** cai. |
| Passada-sombra | No código, off | Fora da fila. |
| Gemini no app | Pausado / previsto como pós | **Sai.** Leitura dupla fora. |
| Juiz / “assinado por” (H) | Não | Não, até decisão expressa. |
| Docling | Default | Default. PyMuPDF primário só se a tabela continuar ilegível (2.0-E). |
| DATE / MONEY / LEGISLACAO | Veto | Veto. |

### 14.4. Catálogo §8 relido (urnas)

**Veto permanece:** schema DATE/MONEY/`dob`; limiar < 0,75; `mapa.json` na nuvem **pelo app**; `[EXECUTADA_n]`; Privacy Filter; regex unlabeled; juiz/H; OCR nativo; VLM Docling; LlamaParse; Presidio-transformer; `pymupdf4llm`; Hyperscan/embeddings/truecaser; **cliente Gemini no app**.

**Cai de prioridade:** Marker, MinerU, LeNER, `arthrod`, GLiNER2, LLM local, fine-tune, passada-sombra, Label Studio, árvore fina eproc, grade CDA, pós-passada interna.

**Sobe:** geografia P3; auditor PyMuPDF × conteúdo; volume P5.

**Reabre com recorte:** PyMuPDF como primário — um lote, se a tabela do MD continuar ilegível na leitura externa.

**Inalterado:** heurística de capa; CNPJ Sul; RapidFuzz só de entidade já confirmada; NiceGUI; auto-auditoria.

### 14.5. Testes que o 2.0 exige

295 OK não mede o piloto. Faltam:

1. Empresa da lista **não** tokeniza `comércio` / `nome individual` / CTN art. 133 (suíte cobre o caso sintético).
2. Sem `GeminiEngine` nem chave persistida; o pipeline não chama API (2.0-B).
3. Aceite = lote eproc + `AUDITORIA.txt` + §10.

### 14.6. Aborto 2.0-B

A remoção não pode deixar um caminho que ainda poste o original. `mapa.json` não sai com o exportável.

---

## 12. Onde está cada coisa no repositório

| Peça | Caminho |
|---|---|
| Este mapa | `GOVERNANCA.md` |
| Resposta de produto ao catálogo NER | `resposta-relatorio-ner.md` |
| README (como abrir o app) | `README.md` |
| Heurística de capa | `src/anonimizador_app/header_heuristic.py` |
| Junta dos motores contextuais | `src/anonimizador_app/contextual.py` |
| GLiNER / spaCy | `src/anonimizador_app/local_ner.py` (limiar `DEFAULT_THRESHOLD = 0.75`) |
| KEEP_TERMS e filtros | `src/anonimizador_app/gemini_detector.py` |
| IDs estruturados | `src/anonimizador_juridico/recognizers.py` |
| Pipeline | `src/anonimizador_app/pipeline.py` |
| Lista de alvos | `src/anonimizador_app/target_list.py` / `target_ids.py` |
| Cabeçalho de coluna (placa/RENAVAM/chassi) | `src/anonimizador_app/table_labels.py` |
| Formulário Receita (nascimento/título/filiação) | `src/anonimizador_app/form_fields.py` |
| Código eproc de procurador | `src/anonimizador_app/eproc_codes.py` |
| Índice CNPJ | `src/anonimizador_app/cnpj_index.py` / `cnpj_lookup.py` / `tools/importar_cnpj_sul.py` |
| Extração padrão (PyMuPDF por folha, tabelas, cobertura) | `src/anonimizador_app/fast_extract.py` |
| OCR seletivo (escaneada, vetorial, fonte quebrada) | `src/anonimizador_app/ocr_pages.py` |
| Cobertura por folha e trava de envio | `src/anonimizador_app/coverage.py`, `pipeline.py` (`build_job_result`) |
| Propagação do lote (elenco, um token por entidade) | `src/anonimizador_app/batch_propagation.py`, `pipeline.py` (`prepare_file`/`finish_file`) |
| Versão em uso (janela, AUDITORIA, LEIA-ME) | `src/anonimizador_app/version.py` |
| Régua de estrutura (PDF × extraído × exportável) | `tools/medir_estrutura.py` |
| Régua de vazamento (gabarito privado) | `tools/medir_vazamento.py` + `saidas/privados/gabaritos/` (fora do git) |
| Geografia (só caminho legado Docling) | `src/anonimizador_app/geography.py` |
| Auto-auditoria | `src/anonimizador_app/audit.py` |
| Higiene de span | `src/anonimizador_app/span_hygiene.py` |
| Janela / atalho | `src/anonimizador_app/desktop.py`, `iniciar_app.vbs` |
| Teste sintético da capa | `tests/test_header_heuristic.py` |

Relatórios externos (não versionados neste repositório, salvo se você os copiar):

- `Downloads/relatorio-ner-anonimizador-juridico.md`
- `Downloads/relatorio-consolidado-anonimizador-juridico.md`

---

## 13. Manutenção deste mapa

Quando uma decisão mudar, **não apague** a linha antiga da tabela do §8: mude a coluna “Decisão”, acrescente a data na “Quando reabrir” e uma linha no §11. O valor deste arquivo é o **histórico reabrível**, não a última moda do HuggingFace.

Fidelidade continua a métrica de corte: um miss pontual de nome no corpo é preferível a tokenizar União, SISBAJUD ou `R$`.
