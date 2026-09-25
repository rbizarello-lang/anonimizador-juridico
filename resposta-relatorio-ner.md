# Resposta ao relatório de pesquisa NER

**Projeto:** Anonimizador Jurídico  
**Versão do app confirmada no código:** 0.3.0  
**Documento respondido:** relatório de pesquisa de 29/08/2026 (“motores NER ainda não mapeados”)  
**Data desta resposta:** 29/08/2026  
**Natureza:** decisão de produto. Não é ordem de implementação e não altera o código.  
**Nota de 29/08/2026 (noite):** esta resposta foi avaliada contra o relatório consolidado. O corte de ciclo permanece. Três matizes e a Métrica 1 de extração entram abaixo — sem reabrir o sprint NER.

Este texto responde, ponto a ponto, às sugestões do relatório. A ordem e os títulos abaixo acompanham o original o máximo possível. O veredito interno (heurística de cabeçalho primeiro; LeNER filtrado depois; não baixar o limiar 0,75; não trocar o GLiNER no mesmo passo) permanece; o que muda é o nível de detalhe: cada bloco do relatório ganha um aceite, aceite com ressalva, adiamento ou recusa, com o porquê nas duas métricas do produto.

---

## Glossário (usado daqui em diante)

- **NER** (reconhecimento de entidades nomeadas): o motor que, no texto da peça, tenta achar trechos que sejam pessoa, empresa privada ou logradouro. Não é o mesmo que achar CPF ou número de processo — isso já é outra camada.
- **Token:** marcador estável no Markdown, no formato `[PESSOA_1]`, `[EMPRESA_1]`, `[ENDERECO_1]`. O valor original vai para o mapa privado, fora da pasta exportável.
- **Limiar** (às vezes chamado de *threshold*): nota mínima de confiança do modelo para aceitar uma detecção. No app o valor é **0,75** (75%). Quanto mais baixo o limiar, mais trechos o modelo marca — inclusive o que não deveria.
- **Super-redação** (*over-redaction*): trocar por token algo que a peça precisa continuar mostrando (data, `R$`, tese, órgão público, SISBAJUD, evento). É pior, neste produto, do que um nome que escape pontualmente no corpo.
- **Fidelidade > recall:** preferimos deixar um dado passar a descaracterizar o documento. Recall alto com estrutura quebrada não é sucesso.
- **Checkpoint:** um modelo já treinado, baixável (em geral no HuggingFace), pronto para inferência. Trocar de checkpoint não é o mesmo que treinar de novo.
- **Fine-tune:** treinar um pouco mais um modelo já existente, com exemplos do nosso domínio (capa eproc, CDA, petição em CAIXA ALTA).

---

## 1. Posição geral

O relatório acerta o diagnóstico (domínio + CAIXA ALTA, não extração). O catálogo amplo era o **entregável pedido** — o que ainda não estava na seção 3 do status —, não um defeito. O erro é tratar o ranking por recall como ordem de sprint. O buraco medido no lote eproc de execução fiscal não é “falta de um motor HuggingFace”: IDs estruturados (CPF, CNPJ, processo e afins) já saem tokenizados; nomes da parte, razão social privada e advogados da capa, em CAIXA ALTA, ficam visíveis; União, datas, `R$`, eventos e teses permanecem — o que é o comportamento certo da **camada NER** da Métrica 1. Trocar o GLiNER, baixar o limiar 0,75 ou ligar um NER jurídico com rótulos de lei e data **não** é o próximo passo. O único experimento de amplitude agora é a **heurística de cabeçalho** (capa, tabela de partes, linha rotulada da CDA). Se a capa fechar e o corpo ainda vazar nomes, aí sim um piloto pequeno do LeNER-Br **filtrado** (só pessoa; jogar fora tempo, legislação e jurisprudência). O restante da lista — LLM local, GLiNER2, Presidio com transformer ou Stanza, especialistas `rafola`/`ottema` *neste ciclo*, Privacy Filter e a tabela “não priorizar” — fica de fora deste ciclo.

**Métrica 1 de extração, que esta resposta originalmente não cobria:** “não mexer no motor” vale para **não trocar o Docling**. Não vale como “não há o que fazer”. Markdown é export lossy; eventos vizinhos fundidos e CDA achatada ainda sangram. Folga de engenharia, se houver, vai para serialização conservadora, âncora de página/evento e auditor PyMuPDF × Docling — **sem contaminar o piloto de capa**. Marker, MinerU e `pymupdf4llm` não entram como motor.

Nas duas métricas:

| Métrica | O que mede | Estado hoje | O que esta resposta protege |
|---|---|---|---|
| **1. Manutenção da estrutura do texto** | A peça continua analisável: datas, valores, eventos, teses, órgãos públicos, SISBAJUD/RENAJUD/CNIB/INFOJUD, números de evento — **e**, na extração, localizável (página / evento / bloco) | **Parcial.** Conteúdo jurídico (KEEP_TERMS, filtro de órgão/via, Presidio cego de NLP) já está protegido. Extração ainda sangra: Markdown lossy, eventos fundidos, CDA achatada. Não se troca o Docling; não se lê como “nada a fazer” | Não baixar limiar, não ligar rótulos DATE/TEMPO/MONEY/LEGISLACAO, não varrer a peça inteira com regex, não trocar o extrator neste ciclo |
| **2. Amplitude da anonimização** | Pessoa, empresa privada e logradouro viram token, sobretudo na capa eproc em CAIXA ALTA | Falha no lote real. O corpus sintético já passa; o eproc não | Fechar a capa primeiro, com âncora + duas ou mais palavras; só depois, se preciso, NER jurídico filtrado no corpo |

---

## 2. O que já está no produto (para a pesquisa não tratar como novidade)

O relatório declara, na seção de escopo, que Presidio PatternRecognizers, `urchade/gliner_multi-v2.1`, spaCy `pt_core_news_md`, Docling, PyMuPDF e NiceGUI **não** são novidade. Confirmamos no código da versão 0.3.0. Além disso, vários “achados” do relatório já existem como política ou como filtro — e precisam ser lidos como **já mapeado / já em uso / já rejeitado**, não como proposta nova.

### Já em uso

- **Presidio só com PatternRecognizers**, sem motor NLP embutido. CPF (com dígitos verificadores), CNPJ (numérico ou alfanumérico), número CNJ, e-mail, telefone, OAB, e identificadores **com rótulo** (RG, CEP, Pix, agência/conta, RENAVAM, placa, matrícula de imóvel). IDs estruturados vencem a sobreposição (prioridade 100). Isso já cobre o que vários checkpoints PII redescobrem como `cpf`, `email`, `phone`.
- **GLiNER** `urchade/gliner_multi-v2.1`, rótulos abertos exatamente `pessoa`, `empresa privada`, `logradouro`, limiar **0,75**. API `GLiNER.from_pretrained` — a mesma que o relatório chama de “integração baixa” no item 1.
- **spaCy em português só se o GLiNER falhar ao carregar**, não se o GLiNER “errar” um nome. A cadeia de reserva é `pt_core_news_md` → `pt_core_news_lg` → `pt_core_news_sm`. O `lg` **já está** na reserva; não é candidato a motor principal.
- **Docling** como extrator padrão de PDF (OCR só se a página não tiver texto nativo útil); **PyMuPDF** se o Docling falhar.
- **NiceGUI** na porta local; aba Regras (ligar/desligar tags, regex estreita, lista de exclusão).
- **KEEP_TERMS** e filtros de fidelidade: União, Fazenda Nacional, PGFN, INSS, STF/STJ/TRF4, SISBAJUD/RENAJUD/CNIB/INFOJUD, teses/peças (`embargos`, `agravo`, `execução fiscal`, etc.). Órgão público não vira `[EMPRESA_n]`. Localização sem pista de via (`rua`, `av.`, `alameda`, `r.` …) não vira `[ENDERECO_n]`. Pessoa exige duas ou mais palavras. Termos genéricos demais são recusados.
- **Localizador já é indiferente a maiúsculas:** se o modelo devolver o nome, a ocorrência em CAIXA ALTA na peça é encontrada. O buraco do lote **não** é o encaixe do token; é a detecção.
- **Gemini** existe no código, default **desligado**, UI em pausa. Não é motor do lote atual.
- **Varredura residual** (`scan_residual`) já existe no motor local: lista o que ainda parece pessoa/empresa/logradouro depois da troca. Não é um segundo modelo.

### Já rejeitado (e o relatório confirma)

- **OpenAI Privacy Filter:** já rejeitado no briefing de produto. Redige data; trata número CNJ como conta. O relatório acerta em mantê-lo fora; não é item novo.

### Já medido no domínio real

- Corpus sintético do app: `[PESSOA]` / `[EMPRESA]` já aparecem.
- Lote eproc de execução fiscal: dezenas de IDs estruturados tokenizados; **zero** `[PESSOA]` / `[EMPRESA]` / `[ENDERECO]`. Na capa, a executada (razão social privada) e os advogados da capa permanecem em claro; União e valores em `R$` permanecem — correto na Métrica 1, insuficiente na Métrica 2.

---

## 3. Escopo, exclusões e requisitos de aceite do relatório

**Posição: aceitamos o recorte; recusamos a implicação de que o ranking do relatório é a ordem de implementação.**

O relatório limitou-se ao que “não está na seção 3 do status” e manteve os requisitos: local no Windows (CPU ok), não redigir datas/valores/leis/órgãos/eventos/sistemas, convivência com Presidio e tokens já existentes, Python 3.11+ (o ambiente real é 3.14), licença clara para app local.

Concordamos com tudo isso. Dois reparos:

1. **“Integração preferencial a Python 3.14 / transformers”** não é restrição de produto. O GLiNER atual já roda neste ambiente. Não usamos isso como argumento para trocar de família de modelo.
2. O critério de ranqueamento do relatório (“ganho esperado de recall em pessoa / empresa / logradouro”) **inverte a prioridade do produto**. Aqui a Métrica 1 corta a Métrica 2. Por isso o item que o relatório colocou em 4º (heurística de cabeçalho) sobe para único experimento imediato, e o item que ele colocou em 1º (troca drop-in do GLiNER) **não** entra no mesmo passo.

Requisitos de aceite que o relatório listou e que esta resposta **reafirma**:

- Não tokenizar datas, valores, leis, órgãos públicos, números de evento, SISBAJUD, RENAJUD, CNIB, INFOJUD.
- IDs estruturados do Presidio continuam vencendo NER.
- Tokens continuam `[PESSOA_n]`, `[EMPRESA_n]`, `[ENDERECO_n]`.
- Sem regex faminta (regra larga varrendo a peça inteira).
- NiceGUI permanece.

---

## 4. Diagnóstico do gap

**Posição: aceitamos o diagnóstico; recusamos a conclusão de que “qualquer motor da família LeNER-Br” é o primeiro remédio.**

O relatório diz: o lote (IDs estruturados tokenizados e zero `[PESSOA]` / `[EMPRESA]` / `[ENDERECO]`) aponta falha de **domínio + caixa**, não de extração. Modelos de HAREM/notícia e o GLiNER multilíngue genérico perdem nomes e razões sociais em CAIXA ALTA e confundem órgão público com empresa. LeNER-Br resolve parte disso **só se** `TEMPO`, `LEGISLACAO` e `JURISPRUDENCIA` forem descartados no merge; sem esse filtro, o recall sobe e a Métrica 1 cai.

Isso está certo, e o código confirma o meio do caminho:

- Extração (Docling/PyMuPDF) entregou o texto da capa; o Presidio tokenizou os IDs; o GLiNER, com limiar 0,75 e rótulos em português, **não marcou** a executada nem os advogados da capa.
- O localizador já acharia CAIXA ALTA se o modelo devolvesse o span. Não há bug de “não substitui maiúsculas”.
- KEEP_TERMS já impede União/PGFN/SISBAJUD de virarem empresa. O risco novo não é o KEEP_TERMS falhar no que já conhece; é um motor guloso marcar tese, lei ou órgão **fora** da lista.

O que o diagnóstico **não** autoriza:

- Baixar o limiar 0,75 para o GLiNER atual “passar a ver” a capa. Isso aumenta ruído no corpo inteiro da peça, não só na capa.
- Ligar LeNER com os seis rótulos do corpus.
- Tratar F1 de paper (0,98 em pessoa, 0,95 global) como previsão de eproc.

O ponto de falha mais barato de atacar é **estrutural**: a capa eproc/PJe e o bloco da CDA já rotulam a parte (`EXEQUENTE`, `EXECUTADO`, `ADVOGADO`, `Nome do Devedor`, `Endereço`). Isso não depende de o modelo “aprender” CAIXA ALTA.

---

## 5. Resposta ponto a ponto à lista ranqueada

A numeração abaixo é a do relatório (1 a 10). A nossa ordem de implementação é outra e está na seção 8. Para cada item: decisão, o que o relatório afirma, o que o app já faz, e o porquê nas duas métricas.

Legenda da decisão:

| Decisão | Significado |
|---|---|
| **Aceitamos** | Entra agora, neste ciclo, como experimento |
| **Aceitamos com ressalva** | Entra depois, com filtro obrigatório e um único checkpoint |
| **Adiamos** | Pode voltar a ser considerado, mas não neste passo e não misturado a outro experimento |
| **Recusamos** | Fora deste ciclo; não gasta esforço de encaixe |
| **Já mapeado / já em uso / já rejeitado** | O relatório descreveu o que o app já tem ou já descartou |

### 5.1. Item 1 — Troca drop-in do GLiNER (`arthrod/gliner-mmbert-small-ptbr-pii-full-3x-v1`)

**Decisão: adiamos. Não é o primeiro experimento. Não baixa o limiar. Não entra no mesmo passo que a heurística de cabeçalho.**

O relatório chama este checkpoint de “melhor primeiro experimento”: mesma família GLiNER, treino em ~984 mil amostras PII em português brasileiro, Apache-2.0, API idêntica (`GLiNER.from_pretrained`). Propõe labels `pessoa` / `empresa privada` / `logradouro` mais aliases PII em inglês (`first name`, `location street`, …) e limiar **0,55–0,70** (o card usa 0,3; o app usa 0,75). Variantes menores (`gliner-ettin-68m` / `32m`) serviriam se a latência do mmBERT incomodasse.

O que já está no app e o relatório trata como ajuste simples:

- A API drop-in **já é** a do produto. Trocar a string `urchade/gliner_multi-v2.1` por outro checkpoint da mesma lib não é arquitetura nova.
- Os três rótulos em português **já estão** em uso. Pedir também `first name` / `middle name` / `last name` muda o esquema de labels e mistura PII documental (CPF, RG, PIS, e-mail, telefone) com o que o Presidio já cobre melhor, com validador.
- Empresa **não** é rótulo nativo forte — o próprio relatório admite zero-shot para `organização` / `razão social`. É exatamente o gap da capa (razão social da executada em CAIXA ALTA).
- Treino PII sintético PT-BR, **não** eproc. O relatório admite cobertura parcial de CAIXA ALTA jurídico e incerteza em razão social.

| Métrica | Avaliação desta resposta |
|---|---|
| **1. Estrutura** | Risco médio. O card incentiva limiar 0,3; no produto isso é inaceitável. Mesmo 0,55–0,70 é chute sobre a peça inteira. Risco residual citado pelo relatório (cidade/UF virarem endereço; `dob` se o merge aceitar) atinge datas e comarca. O filtro de via do app mitigaria parte, mas baixar limiar aumenta o volume de candidatos que o filtro tem de recusar. |
| **2. Amplitude** | Ganho incerto exatamente onde o lote falhou. Pode melhorar nome e rua em PT-BR “de cadastro”; não há evidência de que veja a executada em CAIXA ALTA na capa eproc. Empresa continua o ponto fraco. |

**Por que não agora.** São dois experimentos escondidos num só: (i) checkpoint novo, ~140 M, labels em inglês misturadas; (ii) limiar mais baixo. Se a capa melhorar, não saberemos se foi o modelo ou o limiar. Se a estrutura piorar, não saberemos o que reverter primeiro. A heurística de cabeçalho ataca o mesmo buraco com precisão previsível e sem mexer no motor do corpo.

**O que fica em aberto (depois, isolado).** Se a capa fechar pela heurística e ainda quisermos medir este checkpoint, a prova tem de ser: **mesmo limiar 0,75**, mesmos três rótulos em português, sem aliases `dob`/CPF/e-mail, no mesmo lote. Qualquer coisa abaixo de 0,75 é outro experimento. As variantes ettin 68M/32M herdam a mesma decisão: adiadas, não alternativas imediatas.

**Não confundir com “já mapeado”.** O *motor* GLiNER está mapeado; este *checkpoint* PII não está em uso. Adiar não é o mesmo que “já temos”.

---

### 5.2. Item 2 — NER jurídico cased LeNER-Br (`pierreguillou/ner-bert-large-cased-pt-lenerbr` e o base)

**Decisão: aceitamos com ressalva. Piloto pequeno *depois* da heurística de cabeçalho. Só o base (~110 M). Só rótulo PESSOA no merge; LOCAL só com pista de via; ORGANIZACAO só se passar KEEP_TERMS + filtro de órgão — e, no primeiro smoke, preferimos nem ligar ORGANIZACAO. TEMPO, LEGISLACAO e JURISPRUDENCIA saem sempre.**

O relatório acerta o ponto técnico que o GLiNER atual não cobre: tokenizer *cased* (`do_lower_case=false`) treinado em acórdão/petição brasileiros. F1 de pessoa no card (~0,98 no base) é teto de laboratório, não de eproc. O large (~335 M) é lento em peça longa; o base é o smoke correto.

O relatório também acerta o risco: sem filtro, União, PGFN, TRF4 e “art. 185 do CTN” caem em ORGANIZACAO/LEGISLACAO. **Alto risco de super-redação se os três últimos rótulos passarem.** Isso não é ressalva cosmética: é condição de aceite. Sem o filtro, **recusamos** o item.

Integração proposta: `pipeline("token-classification")` com agregação e stride, ou `TransformersRecognizer` do Presidio com `labels_to_ignore`. A segunda via é o item 7; **recusamos** plugá-lo no Presidio (ver §5.7). O caminho aceitável, se o piloto vier, é o mesmo padrão do `LocalNerEngine` atual: prever → mapear rótulo → `locate_mentions` / KEEP_TERMS / `is_too_generic` → overlap em que o Presidio continua soberano nos IDs.

Licença: o relatório nota card sem SPDX e sugere preferir o item 3 (CC-BY-4.0) em caso de dúvida. Concordamos: se o piloto for para valer além de um smoke, a licença do `pierreguillou` precisa ser conferida no repositório **antes** de virar dependência de produto. Isso não impede um teste local pontual; impede tratar o large como padrão.

| Métrica | Avaliação desta resposta |
|---|---|
| **1. Estrutura** | Boa **somente** com o filtro de rótulo. Sem filtro, a Métrica 1 cai — o próprio relatório diz isso. KEEP_TERMS segura o que já conhece; não segura legislação e jurisprudência. Por isso o merge **não** pode ser “o modelo inteiro”. |
| **2. Amplitude** | Melhor cobertura *pronta* de CAIXA ALTA no **corpo** da peça, não na capa rotulada. Pessoa é o ganho. Empresa (ORGANIZACAO) é ambígua: mistura executada privada com União/TRF4. Logradouro (LOCAL) só com a pista de via que o app já exige. |

**Ressalvas obrigatórias do piloto (quando chegar a vez):**

1. Um checkpoint, não dois: **base**, não large; não ligar `dominguesm` no mesmo lote (ver §5.3).
2. Não baixar limiar de confiança no mesmo passo. Se o smoke usar nota do pipeline, o corte tem de ser alto e explícito.
3. Não varrer o large “em peça única” só porque a RAM cabe: o large aumenta latência e superfície de erro sem resolver a capa.
4. Internamente este modelo **já foi considerado** e não chegou a motor de produção (o produto ficou no GLiNER). Não é descoberta; é candidato *filtrado* para um segundo momento.

**Não é o primeiro experimento.** Fecha o corpo, não a capa. A capa tem âncoras; o corpo não. Gastar o primeiro ciclo aqui é inverter o diagnóstico que o relatório acertou.

---

### 5.3. Item 3 — NER jurídico em volume STF (`dominguesm/legal-bert-ner-base-cased-ptbr`)

**Decisão: aceitamos com ressalva, como *alternativa* ao item 2, não como segundo motor no mesmo merge.**

Mesmo esquema LeNER, CC-BY-4.0 explícita, ~110 M, CPU ok. Métricas reportadas (F1 global ~0,95; pessoa ~0,97) são teto de paper — o relatório pede para não tratar como eproc, e concordamos.

O relatório sugere usá-lo como “segundo parecer” no merge (união de spans PESSOA com score). **Recusamos essa forma.** Dois NER jurídicos iguais no mesmo lote duplicam super-redação: o que um marca como ORGANIZACAO/LOCAL o outro confirma. O ganho de recall é ilusório; o custo na Métrica 1 é real.

Como escolher, se o piloto LeNER vier: preferir **um** dos dois. Vantagem do `dominguesm`: licença clara. Vantagem do `pierreguillou` base: o relatório traz F1 de pessoa/LOCAL mais detalhado e é o smoke clássico LeNER-Br. A decisão de qual baixar pode esperar o momento do piloto; a decisão de **não ligar os dois juntos** é agora.

| Métrica | Avaliação desta resposta |
|---|---|
| **1. Estrutura** | Idêntica ao item 2: filtro de rótulo obrigatório. ORGANIZACAO marca Fazenda/União se o KEEP_TERMS falhar ou se o span for maior que o termo guardado. |
| **2. Amplitude** | Expectativa alta de CAIXA ALTA jurídico, ainda assim não medida em CDA eproc. Não justifica “ensemble”. |

---

### 5.4. Item 4 — Heurística de cabeçalho eproc / PJe / CDA (sem modelo)

**Decisão: aceitamos. Único experimento agora.**

O relatório descreve âncoras `POLO ATIVO`, `POLO PASSIVO`, `EXEQUENTE`, `EXECUTADO(A)`, `EMBARGANTE`, `ADVOGADO`, `OAB/XX`; razão social na linha da CDA “Nome do Devedor”; logradouro na linha “Endereço” da qualificação. Over-redaction baixo se a regra exigir âncora + duas ou mais palavras e rejeitar União / PGFN / Fazenda Nacional / INSS. CPU irrelevante. Licença: código próprio. Integração baixa: camada *antes* do GLiNER, só no primeiro bloco da peça e nas células rotuladas de tabela. Não substitui NER do corpo. CAIXA ALTA eproc é o regime nativo.

Isso **não** está no app hoje. Não é “já mapeado”. O KEEP_TERMS recusa órgão; não extrai a executada da célula ao lado. O GLiNER deveria extraí-la e não extrai.

| Métrica | Avaliação desta resposta |
|---|---|
| **1. Estrutura** | Alta, se o recorte for estreito. Âncora + duas palavras + KEEP_TERMS + logradouro só com via e número. Risco sobe se a regra varrer a peça inteira, se pegar linha de evento (`MAGISTRADO`, `SERVIDOR DE SECRETARIA`) no mérito, ou se tokenizar comarca/UF na linha de endereço sem exigir via. |
| **2. Amplitude** | Fecha o buraco **exato** do lote: capa e bloco de partes. Não aumenta recall no mérito da inicial — e o relatório admite isso. Neste passo, isso é aceitável. Um miss no corpo continua preferível a tokenizar União ou `R$`. |

**Ressalvas de desenho (obrigatórias no piloto, ainda sem código):**

1. **Onde vale.** Primeiro bloco da peça (capa / autuação) e células de tabela com rótulo visível. Linha da CDA “Nome do Devedor” / “Endereço” quando o rótulo estiver na mesma janela de 1–3 linhas. **Não** varrer petição, certidão, despacho, linha de evento do eproc.
2. **O que extrair.** Parte privada e advogados da capa ligados à parte privada. Razão social privada da CDA. Logradouro só com pista de via **e** número (o app já recusa cidade/UF soltos).
3. **O que recusar.** União, PGFN, Fazenda Nacional, INSS e demais KEEP_TERMS. Órgão na célula do polo ativo típico de execução fiscal federal **não** vira `[EMPRESA_n]`.
4. **OAB.** O número OAB já é Presidio. A heurística pode usar `OAB/XX` só como âncora para o **nome** do advogado, sem recortar o identificador de novo.
5. **Não é regex faminta.** Não basta uma lista de nomes próprios. A regra nasce da âncora. Sem âncora, não marca.
6. **Não substitui o GLiNER.** O motor atual permanece no corpo, limiar 0,75 intacto. A heurística é camada anterior, no trecho em que o lote falhou.

Critérios de sucesso e aborto deste piloto estão na seção 9.

---

### 5.5. Item 5 — Especialistas estreitos (`rafola/BERT-base-pt-BR-person` + `ottema/bert-addresses-brazil`)

**Decisão: recusamos neste ciclo. Não é recusa perpétua.**

Dois BERT-base (~110 M cada): um só de pessoa (declara ignorar topônimos que contêm nome de pessoa); outro decompõe RUA, NUMERO, BAIRRO, CIDADE, ESTADO, CEP, COMPLEMENTO. Empresa: não detecta. Over-redaction baixo no de pessoa; **alto** no de endereço se CIDADE/ESTADO/comarca forem tokenizados. O relatório já pede mapear só RUA+NUMERO (+COMPLEMENTO) para `[ENDERECO_n]` e lembra que CEP já é Presidio.

| Métrica | Avaliação desta resposta |
|---|---|
| **1. Estrutura** | O de endereço empurra cidade/UF — exatamente o que o produto não tokeniza. O filtro de via do app **já existe** e faz o recorte que o relatório pede “na mão”. Ligar o ottema para depois jogar fora CIDADE/ESTADO é custo sem ganho de desenho. |
| **2. Amplitude** | Pessoa: possível ganho, domínio não-eproc. Empresa: **zero**. Sem empresa, não fecha a capa do lote (a falha visível é razão social da executada + advogados). Dois modelos em série, mesmo limitados ao “parágrafo candidato”, duplicam o que a heurística de cabeçalho faz com âncora. |

**Por que fora deste ciclo, não da prateleira.** Não é licença (rafola é MIT). É encaixe agora: dois classificadores, composição de span de endereço, domínio errado, e o rótulo que mais falta na capa (empresa) não existe. Se a capa precisar de pessoa+empresa, a heurística resolve os dois; se o corpo precisar de pessoa em CAIXA ALTA, o LeNER filtrado é o candidato certo. Reabrir só se, **depois** da capa, o vazamento for *somente* pessoa no corpo **ou** *somente* rua+número — um dos dois, limitado a parágrafo candidato, não os dois em série.

---

### 5.6. Item 6 — GLiNER PII multilíngue com label `cpf` (`E3-JSI/gliner-multi-pii-domains-v1` e Knowledgator)

**Decisão: recusamos. Família já mapeada; checkpoint pior, para o nosso recorte, do que o item 1 — que já adiamos.**

Drop-in da mesma lib `gliner`. O E3-JSI lista `person`, `organization`, `address`, `cpf` (Apache-2.0). O relatório o considera inferior ao item 1 em especialização PT-BR. `knowledgator/gliner-pii-large-v1.0` tem F1 PII genérico melhor em banco sintético, sem especialização jurídica BR. Over-redaction médio: `date of birth` e IDs genéricos.

O que já está no app:

- GLiNER multilíngue **já é** o motor (`urchade/gliner_multi-v2.1`).
- Label `cpf` é retrabalho: o Presidio valida CPF. Não queremos NER “achando” dígitos que o validador já cobre, nem competindo no overlap.
- `date of birth` é rótulo que o produto **não** quer no merge.

| Métrica | Avaliação desta resposta |
|---|---|
| **1. Estrutura** | Semelhante ao GLiNER atual se o prompt de labels for só pessoa/empresa/logradouro; piora se `cpf`/`dob` entrarem. Knowledgator large aumenta gulodice PII genérica. |
| **2. Amplitude** | Fraca a média em CAIXA ALTA jurídico BR — o relatório diz isso. Não é melhor que o item 1; o item 1 já não é o primeiro passo. |

Trocar um GLiNER genérico por outro GLiNER genérico, com PII documental por cima, é ruído de catálogo.

---

### 5.7. Item 7 — Presidio `TransformersNlpEngine` / `StanzaNlpEngine` com o modelo do item 2 ou 3

**Decisão: recusamos como arquitetura. Não é detector novo.**

O relatório é explícito: não é um detector novo; é o encaixe oficial do motor jurídico no Presidio, que o app já usa **só** com PatternRecognizers. Stanza NER PT é mais lento que spaCy e **não** é jurídico. O relatório ainda diz para não usar o NER default do Stanza-PT como substituto do GLiNER (é PER/ORG/LOC de jornal). Concordamos, e vamos além: **não ligar o engine NLP do Presidio**.

O que já está no app e o relatório descreve como “integração média-alta”:

- O Presidio está “cego” de NLP **de propósito** (`StructuredDetector` chama `analyze(..., nlp_artifacts=None)`).
- A junção NER + IDs **já existe** fora do Presidio: `process_file` corre o detector estruturado e o motor contextual, depois `resolve_overlaps`. IDs continuam com prioridade 100.
- Ligar `TransformersNlpEngine` muda scores, overlap e o contrato do Presidio. O ganho de detecção é só o do modelo plugado — o mesmo do item 2 ou 3, que se forem testados entram pelo `LocalNerEngine`, não pelo Presidio.

| Métrica | Avaliação desta resposta |
|---|---|
| **1. Estrutura** | Risco de regressão no que já funciona (CPF, CNPJ, processo, OAB). Mexer no motor que hoje só faz regex + validador para “oficializar” um transformer não melhora a capa e pode reordenar conflitos de span. |
| **2. Amplitude** | Zero além do modelo plugado. Não fecha o gap por si. |

`labels_to_ignore` e `model_to_presidio_entity_mapping` são controles reais, mas resolvem um problema que nós não precisamos criar. Se o LeNER filtrado vier, o filtro de rótulo fica na camada contextual, junto com KEEP_TERMS e pista de via — que já existem.

---

### 5.8. Item 8 — GLiNER2 PT-BR (`ottema/gliner2-ptbr` e Fastino GLiNER2-PII)

**Decisão: recusamos.**

Não é o pacote `gliner` atual. API `gliner2`, 200–307 M. Ottema: generalista PT-BR (atendimento/chat), não jurídico. Fastino: 42 tipos PII, recall alto no benchmark SPY, **precisão legal cerca de 0,35**. O relatório já diz: integração **alta** (lib nova, não drop-in); só valeria depois de esgotar o item 1; CAIXA ALTA jurídico BR baixa (domínio errado). Fastino otimizado a recall de redação — o oposto da regra fidelidade > recall.

| Métrica | Avaliação desta resposta |
|---|---|
| **1. Estrutura** | Fastino: médio-alto de super-redação por desenho. P legal 0,35 é incompatível com peça analisável. Ottema: médio em texto informal, irrelevante em CDA. |
| **2. Amplitude** | Recall de redação não é amplitude *útil*. Marcar mais trechos, inclusive o que não deve, piora as duas métricas ao mesmo tempo. |

Não “esgotamos o item 1” porque o item 1 está adiado. GLiNER2 não espera na fila: domínio errado, lib nova, e o Fastino é um Privacy Filter com outro nome.

---

### 5.9. Item 9 — LLM local com JSON schema (Gemma 2–9B Q4, Llama 3.1 8B Q4, `numind/NuExtract-2.0-2B`)

**Decisão: recusamos agora. Não é motor principal. Não é o análogo que “substitui” o Gemini neste ciclo.**

O relatório propõe extração estruturada (`pessoas[]`, `empresas_privadas[]`, `logradouros[]`), temperature 0, schema fechado, span obrigatoriamente substring do original. 2B Q4 possível em CPU; 8B lento demais para peça inteira. Integração alta (llama.cpp/Ollama à parte do torch atual). Não determinístico. Reserva como **árbitro residual** em parágrafo que a varredura residual marcou. Diz ainda: “religar Gemini viola a premissa atual; LLM local é o análogo aceitável”.

Três recortes nossos:

1. **Gemini pausado é premissa de *lote atual*, não um vácuo a ser preenchido por outro LLM.** O motor local já é o GLiNER. LLM local não é o próximo drop-in.
2. **Alucinar span que não existe é inaceitável**, mesmo com schema. Exigir substring reduz o dano, não o elimina (span deslocado, razão social pela metade, órgão colado no nome). Controle de qualidade vira revisão humana por parágrafo.
3. A varredura residual **já existe** sem LLM. Ligar Gemma/Llama/NuExtract em cima dela é um produto diferente (runtime, RAM, tempo por peça).

| Métrica | Avaliação desta resposta |
|---|---|
| **1. Estrutura** | Alto risco se o prompt for frouxo; ainda há risco com prompt rígido. Um span alucinado de lei ou órgão que por acaso exista em outro lugar do texto passa no teste de substring e quebra a peça. |
| **2. Amplitude** | O schema *pode* distinguir empresa privada de órgão — isso é o atrativo. Sem grounding estável e sem CPU folgada para peça longa, o ganho é teórico. |

Não moralizamos envio a modelo de linguagem. A recusa é de **encaixe, custo e determinismo** neste pipeline, não de política de nuvem. Se um dia a capa e o LeNER filtrado ainda deixarem um parágrafo residual, aí se discute um árbitro com schema e substring — não agora, e não como motor da peça inteira.

---

### 5.10. Item 10 — Fine-tune curto do GLiNER (ou do LeNER) no corpus sintético + páginas eproc anotadas

**Decisão: adiamos. Caminho com maior teto, não é o primeiro passo. Só depois de medir a heurística de cabeçalho e, se for o caso, o smoke LeNER filtrado.**

O relatório está certo no teto: é o único passo que ataca de frente “cobertura real do modelo neste domínio”. GLiNER documenta fine-tune com poucos exemplos. Backbone candidato: o checkpoint do item 1, ou LegalBert-pt (`raquelsilveira/legalbertpt_sc` / `dominguesm/legal-bert-base-cased-ptbr`) + cabeça NER só com três rótulos. LegalBert-pt **não tem cabeça NER pronta** — é modelo de linguagem. Concordamos: não é drop-in.

Esquema de anotação alinhado ao produto: **não** anotar data, valor, lei, órgão. Inferência do modelo resultante cabe em CPU; treino melhor com GPU. Esforço médio (anotar ~30 páginas reais de execução fiscal em CAIXA ALTA).

| Métrica | Avaliação desta resposta |
|---|---|
| **1. Estrutura** | Controlável se a anotação for a política do app. Risco: anotar “demais” (comarca, evento, polo público) e ensinar super-redação. |
| **2. Amplitude** | Único caminho com teto alto nos **três** rótulos no domínio real, inclusive corpo da peça. |

**Por que não agora.** Anotar 30 páginas reais é trabalho. Fazer isso antes de uma regra de capa é gastar anotação no trecho que âncora já resolve. Fine-tune do GLiNER “vencedor” pressupõe haver vencedor: hoje o vencedor da capa não é um checkpoint, é a heurística. O adiamento está certo; o **teto no corpo eproc** continua sendo este caminho — não some do mapa.

Quando voltar: anotar só o que o produto tokeniza (pessoa completa, razão social privada, logradouro com via+número). Não anotar juiz, servidor, órgão, tese, valor, data. Não misturar corpus sintético que já passa no teste com eproc que falha, sem marcar a origem.

---

## 6. Tabela “explicitamente não priorizar”

**Decisão: aceitamos a lista inteira como recusa. Alguns itens já estavam rejeitados; nenhum entra pela porta dos fundos.**

| Artefato | O que o relatório diz | Nossa posição | Métrica 1 | Métrica 2 |
|---|---|---|---|---|
| `celiudos/legal-bert-lgpd` | Rótulos DATE e MONEY; F1 alto no que o briefing **proíbe** tokenizar; large 1,33 GB | **Recusamos.** Desligar DATE/MONEY e ainda assim carregar um large jurídico-LGPD não paga o encaixe. Schema errado. | Quebraria datas e `R$` se os rótulos passarem | Não é o gap (pessoa/empresa/logradouro na capa) |
| UlyssesNER / `ronunes/bertimbau-base-ulyssesner_br-bcod-self_learning` | Esquema legislativo (lei, órgão, evento); tokenizaria tese e diploma | **Recusamos.** Schema errado, ainda que MIT. | Alta super-redação de tese/órgão/evento | Recall no lugar errado |
| John Snow Labs `legner_lener_*` | Mesmo schema LeNER, **licença comercial**, Spark/Legal NLP | **Recusamos.** Duplica o item 2 sem ganho de encaixe e com licença incompatível com o app local como está. | A mesma do LeNER, mais lock-in | A mesma do item 2, mais cara |
| OpenMed-PII-Portuguese-* | PII clínico, 54 tipos, amount/currency | **Recusamos.** Over-redaction certo. | Valores e tipos clínicos | Domínio errado |
| Stanza-PT NER, Flair `jneto04/ner-pt`, spaCy `pt_core_news_lg` | NER jornalístico; Flair marca TIME/VALUE; spaCy-lg é irmão do md | **Já mapeado em parte, recusado como motor.** spaCy-lg **já está** na cadeia de reserva se o GLiNER não carregar; não é motor principal. Stanza/Flair: recusa. | Flair TIME/VALUE atinge data e `R$` | Jornal ≠ eproc CAIXA ALTA |
| Albertina PT-BR, Legal-BERTimbau STS, MediAlbertina | Encoders ou NER médico/PT-PT; sem cabeça útil para partes de EF | **Recusamos.** | Neutro (não há cabeça NER útil) | Zero no gap |
| OpenAI Privacy Filter | Já rejeitado no briefing; redige data; CNJ como account | **Já rejeitado.** O relatório confirma. Não reabre. | Alta redação, baixa precisão legal | Amplitude cega, inútil |
| `lucasorrentino/Contractner` | Contratos SEC/inglês | **Recusamos.** Domínio e língua errados. | Irrelevante | Zero |

Nada desta tabela vira “piloto pequeno”. A coluna “não priorizar” do relatório, neste produto, lê-se **não fazer**.

---

## 7. Recomendação de produto do relatório (a ordem que eles pediram)

O relatório fechou com cinco passos. Respondemos cada um, sem resumir demais.

### Passo 1 do relatório — “Dois patches baratos”

> (i) Heurística de cabeçalho eproc/CDA. (ii) Substituir o checkpoint GLiNER pelo item 1, labels `[pessoa, empresa privada, logradouro]` + aliases PII, limiar 0,60, KEEP_TERMS intacto. Medir no lote que hoje dá 0 `[PESSOA]`.

**Posição: aceitamos (i); recusamos (ii) no mesmo passo.**

Não são dois patches baratos. (i) é barato, localizado, mensurável na capa. (ii) é troca de modelo + **queda de limiar de 0,75 para 0,60** + aliases em inglês. Isso é o corpo inteiro da peça, não a capa. KEEP_TERMS intacto não salva limiar 0,60: o filtro só apaga o que reconhece.

Medir no lote que hoje dá zero `[PESSOA]` é o teste certo — **depois de ligar só a heurística**, com GLiNER e 0,75 intocados. Se (ii) for um dia medido, será outro lote de comparação, um fator de cada vez.

### Passo 2 do relatório — “Segundo motor, filtrado”

> Ligar `pierreguillou` base **ou** `dominguesm` só com PESSOA (+ LOCAL se houver pista de via). Presidio soberano nos IDs. Large só em peça única, não no lote.

**Posição: aceitamos com ressalva, como segundo ciclo, não como acompanhante do passo 1.**

O “ou” é obrigatório (um checkpoint). LOCAL com pista de via **já é** a regra do app. Presidio soberano **já é** a regra do overlap. Large: recusamos inclusive em “peça única” neste ciclo; o base basta para saber se CAIXA ALTA no corpo responde a cased LeNER.

### Passo 3 do relatório — “Não ligar DATE / TEMPO / MONEY / LEGISLACAO em nenhum motor”

**Posição: aceitamos. Já é política do produto; reafirmamos.**

Isso corta o `celiudos` (DATE/MONEY), o LeNER sem filtro (TEMPO/LEGISLACAO/JURISPRUDENCIA), o Flair (TIME/VALUE), o Privacy Filter (data, CNJ-as-account) e qualquer alias `dob` do GLiNER PII. Não é um passo futuro: é veto permanente.

### Passo 4 do relatório — “Só então anotar ~30 páginas reais e fine-tunar o GLiNER vencedor”

**Posição: adiamos. Concordamos com o “só então”; discordamos de que já haja GLiNER vencedor a fine-tunar.**

O vencedor da capa, se o piloto da seção 9 passar, é a heurística — que não se fine-tuna. Fine-tune faz sentido para o **corpo**, depois de saber se o LeNER filtrado já basta. Trinta páginas anotadas antes dessa medição são esforço no lugar errado.

### Passo 5 do relatório — “LLM local como opção da varredura residual, span substring”

**Posição: recusamos neste ciclo.**

A varredura residual já roda. LLM local não entra como opção habilitada, nem como “análogo do Gemini”. Reavaliar só depois de capa + (se preciso) LeNER filtrado, com o mesmo veto de span inventado.

### Frase final do relatório

> Fidelidade permanece a métrica de corte: um miss pontual de nome no corpo da peça é preferível a tokenizar “União”, “SISBAJUD” ou “R$ 1.234,56”.

**Aceitamos integralmente.** É a regra do README e do `FIDELITY_PRINCIPLE`. Super-redação é pior que miss pontual. Esta resposta inteira está subordinada a isso.

---

## 8. Sequência que adotamos (em vez da ordem 1–10 do relatório)

| Ordem | O quê | Decisão | Não misturar com |
|---|---|---|---|
| **Agora (1 experimento de amplitude)** | Heurística de cabeçalho / polo / CDA rotulada | Aceitamos | Troca de GLiNER, queda de limiar, LeNER, LLM |
| **Agora, se houver folga e sem contaminar o piloto** | Serialização conservadora: âncora de página/evento; auditor PyMuPDF × Docling. Docling permanece o default | Autorizado na Métrica 1; não é NER | Troca de extrator (Marker/MinerU/`pymupdf4llm`); embelezar CDA no mesmo passo |
| **Depois, se a capa fechar e o corpo ainda vazar nomes** | LeNER-Br **base** *ou* `dominguesm`, só PESSOA no merge; LOCAL só com via; sem TEMPO/LEGISLACAO/JURISPRUDENCIA | Aceitamos com ressalva | Os dois checkpoints juntos; large; Presidio NLP engine |
| **Mais tarde, isolado, só se ainda houver caso** | Checkpoint GLiNER PII PT-BR (`arthrod`), **limiar 0,75**, três rótulos em português, sem `dob`/CPF. Continua o melhor candidato isolado da mesma família — adiar não é “já temos GLiNER” | Adiamos | Queda de limiar; aliases PII documental |
| **Ainda mais tarde** | Fine-tune curto com páginas eproc anotadas no esquema do produto. Adiado com razão; o teto no *corpo* eproc continua sendo esse caminho | Adiamos | Anotar data/valor/lei/órgão |
| **Não entra neste ciclo** | LLM local, GLiNER2, Presidio+transformer/Stanza, `rafola`+`ottema`, E3-JSI/Knowledgator, Privacy Filter, tabela “não priorizar” | Recusamos neste ciclo (`rafola`/`ottema`: reabertura possível, ver §5.5) | — |

O GLiNER atual permanece. O limiar 0,75 permanece. O spaCy continua só reserva de carga. O Gemini continua pausado. O Presidio continua sem NLP.

---

## 9. Critérios de sucesso e aborto do piloto de cabeçalho

O piloto de **amplitude** é o único experimento NER autorizado agora. A serialização (âncora de página/evento, auditor) pode ocupar folga de engenharia **sem** mudar GLiNER, limiar ou a heurística. Mede-se a capa no **mesmo tipo de lote eproc** que hoje produz IDs tokenizados e zero `[PESSOA]` / `[EMPRESA]` / `[ENDERECO]`. Não se mede no corpus sintético (esse já passa). Não se implementa nesta resposta; quando houver implementação, estes critérios valem como aceite.

### 9.1. Recorte do teste

- Material: capa / autuação / tabela de polos / bloco da CDA do lote eproc de execução fiscal.
- Camada nova: só a heurística de âncora, no primeiro bloco e em células rotuladas.
- Camadas intocadas: Presidio, GLiNER `urchade/gliner_multi-v2.1`, limiar 0,75, KEEP_TERMS, filtro de via, Gemini pausado.
- Sem PII real em atas deste piloto: falar em “a executada”, “advogados da capa”, “polo ativo público”, “lote eproc”.

### 9.2. Sucesso (tem de ocorrer junto)

1. A razão social **privada** da capa ou da linha “Nome do Devedor” vira `[EMPRESA_n]`.
2. Os **advogados da capa** da parte privada viram `[PESSOA_n]` (duas ou mais palavras; âncora `ADVOGADO` / `OAB`).
3. Se a linha de endereço da qualificação tiver via e número, vira `[ENDERECO_n]`; cidade, UF, bairro e CEP rotulado **não** viram endereço (CEP continua com o Presidio, se rotulado).
4. **Não** aparece token em: União, Fazenda Nacional, PGFN, INSS, TRF4, data, `R$`, número de evento, SISBAJUD/RENAJUD/CNIB/INFOJUD, tese, artigo de lei, nome de sistema.
5. IDs que já saíam tokenizados (CNPJ da executada, processo, OAB numérica) continuam tokenizados; a heurística não briga com eles no overlap.
6. O restante da peça **pode** continuar com nomes no mérito. Isso não reprova o piloto. O piloto não promete o corpo.

### 9.3. Aborto (qualquer um encerra o desenho, não “ajusta limiar”)

1. Token em órgão público, valor em reais, data, comarca, UF solta, evento eproc ou sistema (SISBAJUD e afins).
2. A regra só funciona se varrer a peça inteira ou se cair para lista de nomes sem âncora (regex faminta).
3. O polo ativo público (União / Fazenda Nacional) vira `[EMPRESA_n]`.
4. Juiz, diretor de secretaria ou procurador da União em **linha de evento** no meio dos autos é tokenizado pela mesma regra — sinal de que a janela saiu da capa.
5. Queda de limiar do GLiNER ou troca de checkpoint “para ajudar” o piloto. Isso é outro experimento; contamina a medida.

### 9.4. Se o piloto passar e o corpo ainda vazar

Aí, e só aí, abre-se o smoke do LeNER **base** filtrado, no mesmo PDF, ainda com limiar alto e KEEP_TERMS intacto. Critério de aborto daquele smoke (quando existir): qualquer `[EMPRESA_n]` em órgão, qualquer token em lei/jurisprudência/tempo, qualquer cidade sem via. Critério de sucesso: nomes de pessoa em CAIXA ALTA no mérito que a capa já não cobriu, sem quebrar a Métrica 1.

### 9.5. Se o piloto falhar

Não se “compensa” com GLiNER PII nem com LLM local. Revisa-se âncora e janela (1–3 linhas, células rotuladas). Se as âncoras do lote não existirem no Markdown extraído (falha de extração/tabela), o problema volta a ser Docling/layout, não NER — e isso é outro diagnóstico, fora do catálogo HuggingFace.

---

## 10. O que recusamos, reunido (e por quê)

Para não deixar item solto: abaixo está o conjunto recusado **neste ciclo**, com a razão curta nas duas métricas. O detalhe está nas seções 5 e 6.

| Item | Por quê (estrutura) | Por quê (amplitude) |
|---|---|---|
| Baixar o limiar 0,75 (para 0,60, 0,55 ou 0,3) | Mais candidatos no corpo inteiro; datas, órgãos e teses entram na fila do filtro | Não prova CAIXA ALTA; só prova que o modelo fica mais guloso |
| Troca GLiNER → `arthrod` no mesmo passo da capa | Mistura duas variáveis; aliases PII/`dob` | Empresa e eproc continuam incertos no próprio relatório |
| E3-JSI / Knowledgator | `dob` e IDs genéricos | Mesma família já em uso, pior em PT-BR jurídico |
| GLiNER2 ottema / Fastino | P legal ~0,35; lib nova | Domínio chat/PII, não execução fiscal |
| Presidio Transformers / Stanza | Mexe no overlap que hoje é estável | Não detecta além do modelo plugado |
| `rafola` + `ottema` | Cidade/UF no endereço; filtro de via já recorta | Sem empresa; fora **deste ciclo**, não da prateleira (reabrir só se o vazamento for só pessoa ou só rua+número) |
| LLM local (Gemma/Llama/NuExtract) como motor ou residual agora | Span alucinado; runtime à parte | Ganho teórico; residual já existe sem LLM |
| LeNER *sem* filtro de rótulo; large; os dois checkpoints juntos | Lei, tempo, jurisprudência, União | Recall inflado no lugar errado |
| Toda a tabela “não priorizar”, inclusive Privacy Filter | Schema DATE/MONEY/lei/evento, ou licença comercial, ou domínio clínico/inglês | Já rejeitado ou irrelevante |

---

## 11. Referências do relatório

Não disputamos os cards citados. Disputamos a **ordem** e o **encaixe**. Os endereços HuggingFace e o artigo de ensembles de LLM locais para NER PT permanecem úteis como catálogo. Nenhum deles, sozinho, fecha a capa do lote eproc. A peça-fonte de decisão deste documento é o comportamento já medido no app 0.3.0: IDs estruturados ok; pessoa, empresa privada e logradouro na capa eproc em CAIXA ALTA, não.

---

## 12. Fecho

O relatório de pesquisa é um bom mapa de prateleira — e era isso que se pediu. É um mau plano de sprint se o ranking por recall virar ordem de implementação. O gap de amplitude é a capa, em CAIXA ALTA. O conteúdo jurídico da Métrica 1 já está protegido; a extração ainda sangra (Markdown lossy, eventos fundidos) e isso **não** se resolve trocando o Docling. A resposta de produto, depois desta avaliação, fica: **heurística de capa agora**; **serialização/âncora/auditor na folga, sem contaminar o piloto**; **LeNER filtrado só se o corpo ainda vazar**; **`arthrod` isolado mais tarde, no 0,75, sem `dob`/CPF**; **fine-tune depois**; **LLM local, GLiNER2 e Presidio-com-transformer fora**. Fidelidade continua a métrica de corte.
