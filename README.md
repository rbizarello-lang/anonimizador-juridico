# Anonimizador Juridico

App local para escolher documentos, trocar identificadores por tokens e gravar
o Markdown numa pasta distinta do mapa reversivel.

A peca precisa continuar analisavel. Datas, valores, eventos, teses e orgaos
permanecem. Na duvida, o trecho original fica: um dado que escape pontualmente
e menos grave do que descaracterizar o documento.

O mapa de decisoes, metricas, alternativas descartadas e proximos passos
esta em `GOVERNANCA.md`.

## Como abrir

O app **nao precisa do Chrome**. Dois cliques no atalho **Anonimizador Juridico**
(area de trabalho ou Menu Iniciar), ou em `iniciar_app.vbs`. Abre uma janela
deste computador, com o endereco proprio:

`http://127.0.0.1:8765`

Esse link tambem esta no arquivo `Anonimizador Juridico.url` (pasta do projeto).
Se o app ja estiver aberto, um segundo clique **traz a janela para frente**
(nao pergunta se deve encerrar a copia).

Se preferir o navegador (Chrome/Edge), use `iniciar_navegador.bat`.
`iniciar.bat` abre o mesmo app com a janela preta de mensagens (util se algo falhar).

Se o app ja estava aberto com codigo velho, feche a janela e abra de novo.
O atalho nao deve abrir janelas pretas do System32; se a abertura falhar,
veja `saidas\app.log`.

Tres abas:

- **Lote** — fila de arquivos (escolher, adicionar pasta ou colar o caminho),
  painel de execucao (resumo do caso, destinos, motor local) e resultado
  (indicadores, trechos trocados por tipo, pastas, previa com os tokens
  destacados)
- **Caso** — empresas, pessoas e CPF/CNPJ da carteira; importa a planilha do Nexus
- **Regras** — tags ligadas, regex estreita, termos protegidos, base CNPJ, experimentos

O botao **?** do cabecalho abre "Como funciona" (principio de fidelidade,
tags, extracao de PDF, leitura externa).

## Saidas

Cada lote tem o mesmo identificador (`lote_AAAAMMDD_HHMMSS`) em dois lugares:

- Textos anonimizados: `saidas\exportaveis\lote_...` (arquivos `.md` + `LEIA-ME.txt` + `AUDITORIA.txt`)
- Mapa para reverter: `saidas\privados\lote_...\mapa.json` (+ `LEIA-ME.txt`)
- Configuracao do app (regras, motor local): `saidas\privados\config.json`
- Caches (so nesta maquina; apagar so custa tempo na proxima leitura):
  `saidas\privados\cache_extracao` e `cache_ner` (texto extraido e achados do motor
  local) e, com o motor LLM, `saidas\privados\cache_llm` (respostas do provedor:
  **nomes, papeis e trechos em claro**). Trate essas pastas como o `mapa.json`: nao
  copie, nao envie e apague quando o caso encerrar.

Pode escolher varios PDFs de uma vez (por exemplo PARTES de ~10 MB); o app
processa um apos o outro e reutiliza os mesmos tokens no lote.

Extracao de PDF (desde 26/09/2026, ver `GOVERNANCA.md`, Veredito 3.0-E): o
texto sai folha a folha pelo PyMuPDF, sem reescrita. Cada folha leva a ancora
`<!-- pagina-origem: N -->` da folha fisica do PDF, e os marcadores do eproc
(evento, documento, pagina) viram titulos. Tabela com grade vira tabela
Markdown no lugar dela. OCR (RapidOCR, local) so em folha sem texto legivel:
digitalizada, com o texto desenhado em vetor ou com fonte quebrada. O topo de
cada Markdown traz a cobertura (quantas folhas vieram do texto nativo, do OCR,
em branco); folha que nao pode ser lida fica marcada no texto, aparece na
`AUDITORIA.txt` e bloqueia o envio.

O Docling saiu do caminho padrao (estourava 15 min em autos grandes e
devolvia o documento pela metade; numerava as paginas errado). O laboratorio
Docling (`converter_docling.py`, `entrada`, `saida`) foi preservado.

O motor local (GLiNER, com spaCy em portugues de reserva) detecta nomes
completos, empresas privadas e logradouros nesta maquina.

Deteccao 2.1 (propagacao do lote): o lote roda em duas fases. Primeiro todos
os arquivos sao lidos e detectados; depois, o que foi detectado em qualquer
arquivo vale para todos. A mesma pessoa ou empresa recebe o mesmo token em
todas as grafias ("Fulano Tal" e o nome completo; a razao social com e sem
LTDA), e as ocorrencias que a deteccao nao pegou (nome partido em duas linhas,
tabela, lista) sao trocadas tambem. Placa, chassi, RENAVAM, e-mail e a raiz de
CNPJ ja mapeados seguem pelo valor exato. Juiz e servidor (cargo colado ao
nome), rotulo de tabela e termo juridico ("pessoa juridica") nao se propagam.
A `AUDITORIA.txt` conta, por arquivo, quantas ocorrencias a propagacao
acrescentou.

Deteccao 2.2 (limites do token): o token nao come conteudo vizinho. Palavra
de tabela com 12 letras nao vira mais CNPJ (comia o dia da data ao lado);
numero de protocolo nao vira telefone; endereco para na celula ou na coluna
da tabela e antes de parentese nao fechado, e nao corta numero ao meio. O
CPF na notacao antiga (DV depois da barra) sai inteiro no token.

Deteccao 2.3: rua com nome de pessoa vira [ENDERECO_n] (nao [PESSOA_n]), e banco
ou instituicao de pagamento listado no SISBAJUD fica em claro em todo o lote.

Deteccao 2.4: o rotulo fica no texto, fora do token, e o valor inteiro fica dentro
("Banco/Agencia: [CONTA_n]", "Debcad: [CONTA_n]", "Matricula n [MATRICULA_IMOVEL_n]",
"CEP: [CEP_n]"; lista de matriculas, um token por numero). O papel da parte fica
fora do token ("executada [EMPRESA_n]"). CNPJ com a filial partida pela quebra da
coluna e CEP de 8 digitos em tabela de enderecos passam a ser detectados.

Folha de separacao e capa do eproc saem com cada rotulo junto do seu valor,
numa linha ("Tipo documento: ... — Evento: ... — Data: ... — Usuario: ..."),
inclusive a de evento sem documento (intimacao, prazo, ciencia). Cada lote traz
o INDICE.md: por arquivo, eventos e documentos com a faixa de folhas, a data,
as folhas lidas por OCR e as tabelas; e a linha do tempo do processo.
E traz o ELENCO.md: quem e quem (requerido, executado, procurador...) pelos
tokens, sem nomes, tirado da capa de TODO processo do lote (a de agravo tambem), com o
papel que o cabecalho das pecas da a quem nao esta em capa ("EXECUTADO: [EMPRESA_2]") e
os vinculos entre tokens que o proprio texto diz (mesmo CNPJ nas capas; "nome inicial ...
alterado para ..."). Os tokens nao sao unificados: o vinculo e dito, nao decidido.
Cada folha comeca com a linha de citacao, por exemplo
"[fl. 84 · Evento 1 · CONTRSOCIAL4 · p. 1]" (folha do arquivo, evento, documento e
pagina do carimbo do eproc).
Arquivo sem nenhum carimbo do eproc (contrato social avulso, extrato) leva o codigo do
nome do arquivo na linha de citacao ("[CONTRSOCIAL13 · fl. 4]"), porque "fl. 4" sozinho
seria ambiguo entre dezenas de arquivos; o INDICE.md traz, para o contrato avulso, a
empresa (token), o ato (constituicao, alteracao, consolidacao) e a data do registro na
Junta Comercial, e o ELENCO.md lista, so por token, os socios e administradores que
cada contrato diz ter.

PDF salvo no PDF-XChange Editor sem licenca traz em cada folha um selo no canto
("Click to BUY NOW!" e "PDF-XChange Editor / www.tracker-software.com" letra a letra
em circulo). O app o reconhece pelo texto "Click to BUY NOW!" e o tira antes da
tabela e do OCR: some o ruido de uma letra por linha, as tabelas falsas e o selo
lido junto com o texto da folha escaneada. So saem as letras do proprio selo; o
texto de verdade encostado nele fica. Folha sem esse texto nao muda. Como a
extracao mudou (versao |13), o primeiro lote depois da atualizacao refaz a leitura
(e o OCR) de cada PDF, o que leva alguns minutos a mais.

Extrato do SISBAJUD, lista de veiculos do RENAJUD e capa do processo saem com cada
rotulo junto do seu valor: "Numero do processo: ...", "Repeticao programada? Sim",
"Nivel de Sigilo do Processo: Sem Sigilo (Nivel 0)"; as respostas de cada instituicao
no SISBAJUD viram uma tabela (valor da ordem e valor bloqueado cada um na sua coluna,
com a instituicao acima) e a lista de veiculos do RENAJUD tambem. So em layout
reconhecido pelos rotulos fixos, e so se nenhuma palavra da folha se perder: se
perder, a folha sai como antes. Como a extracao mudou (versao |14) mas so no texto
nativo, o primeiro lote depois da atualizacao aproveita o cache da |13: as folhas
lidas por OCR nao sao lidas de novo (C1 inteiro leva uns 3 minutos nessa etapa,
nao 90).

Deteccao 2.5: um token por entidade, sem juntar homonimos. Hifen de quebra de linha
("ME-TAIS" = "METAIS"), pontuacao solta do OCR ("DE}") e palavra partida pelo OCR deixam de
criar token novo; a forma curta de um nome com varias capturas ("Fulana X Y" e "Fulana X Y Z")
passa a seguir a forma completa quando esta domina o lote com folga larga. Nao se junta:
prenome diferente por uma letra ("Adriano"/"Adriana"), "Filho"/"Junior"/"Neto", ramo de
atividade de empresa irma ("Comercio"/"Comercial", "Industria"/"Industrial"), filial ("Filial 1"/
"Filial 2"), nome curto que cabe em duas pessoas, duas pessoas colhidas juntas numa linha de
assinaturas. A marca de uma palavra que o lote usa sozinha (so em maiuscula/Titulo, nunca em
minuscula no meio da frase, pelo menos 20 mencoes do nome completo) passa a ser trocada em
todo o lote: com o token da empresa quando so uma empresa do lote comeca por ela, com token
proprio quando sao varias (irmas do grupo, nome antigo e novo). A capa do agravo (2o grau, sem
"Chave Processo:") passa a sair com as tabelas de grade, e a "Partes e Representantes" dela
alimenta o ELENCO.md (versao de extracao |15, so a capa mudou: o cache |14 serve de ponto
de partida, sem OCR).

Deteccao 2.6: nome de cidade ou de estado nao vira empresa. A palavra que e nome de
municipio ou de estado (lista do IBGE, guardada no pacote) nunca e "marca de uma palavra",
e a ocorrencia da marca em contexto de foro ou de endereco ("Vara ... de X", "Comarca de
X", "X/SC", linha de endereco, cabecalho em CAIXA ALTA) fica como estava. Cada lote traz
`AUDITORIA_PRIVADA.txt` na pasta privada, com as palavras-marca aprovadas e as recusadas
para voce conferir; a `AUDITORIA.txt` dos textos so tem tokens e contagens.

Deteccao 2.7: o token da matricula distingue o cartorio. A matricula 12.345 do 1o Registro
de Imoveis e a do 2o Registro de Imoveis sao tokens diferentes; vale a mencao do cartorio
mais proxima (numero de ordem e lugar: "2o Oficio", "da Comarca de X"). Sem cartorio por
perto, o token continua sendo por numero. O nome do cartorio, oficio ou tabelionato fica
em claro (nao vira [EMPRESA_n]).

Deteccao 2.8: empresas ligadas levam o mesmo grupo. Os tokens de empresa que o `ELENCO.md`
liga em "Vinculos" (mudanca de nome declarada em documento, ou o mesmo CNPJ nas capas) ganham
no proprio texto o sufixo do grupo: `[EMPRESA_3·G1]`, `[EMPRESA_7·G1]`. Os tokens continuam
separados (cada um com o nome da epoca do ato); o `ELENCO.md` explica cada grupo ("G1: cadeia
de mudancas de nome", "G2: mesmo CNPJ [CNPJ_2]") e lista os membros. Semelhanca de nome
nunca forma grupo. O mapa privado tem `[EMPRESA_3]`: ao reverter, ignore o sufixo.

O modelo de reconhecimento de nomes (GLiNER) nunca vai a rede sozinho: o app roda
com `HF_HUB_OFFLINE=1` por padrao e usa a revisao do modelo fixada no codigo. Em uma
maquina nova o modelo ainda nao esta no cache local; baixe-o uma vez, de proposito,
com `python tools\instalar_modelo_ner.py --baixar` (sem a opcao, so mostra o que
baixaria). Sem o modelo o app cai no spaCy PT, como antes, e avisa no log.

A versao em uso aparece no cabecalho da janela ("v0.4.1 · codigo ...") e no
topo da `AUDITORIA.txt` e dos `LEIA-ME.txt` de cada lote. Se a janela mostrar
um codigo diferente do esperado, feche o app e abra de novo. Se uma copia
anterior tiver travado ao fechar (viva, sem janela), o proximo clique a
encerra e abre outra; nao e preciso o Gerenciador de Tarefas. O padrao e o
motor local: nada sai deste computador. O motor LLM e opcional (secao abaixo) e
a linha de versao diz qual motor gerou cada lote ("motor local" ou "motor LLM:
gemini-pago"). O mapa reversivel permanece so nesta maquina.

## Gabarito e comparacao de detectores (H7)

A API esta suspensa por decisao do Operador (07/10/2026): o caminho local e medido primeiro.

1. **Gabarito do processo**, a partir da uniao dos mapas de varios lotes (um por motor), pre-classificado
   para revisao:
   ```
   python tools\montar_gabarito.py saidas\privados\lote_A saidas\privados\lote_B --pdfs <pasta dos PDFs>
   ```
   Grava `saidas\privados\gabaritos\gabarito_<pasta>.json` e `.REVISAO.md` (com nomes: nao enviar). Leia o
   REVISAO, corrija `acao` (tokenizar/manter) no JSON e apague o que nao for entidade.
2. **Detectores em CPU contra o gabarito** (baixe antes: `tools\instalar_modelo_ner.py --baixar --extra gliner-large lener`):
   ```
   python tools\comparar_motores.py saidas\privados\gabaritos\gabarito_<pasta>.json --pdfs <pasta dos PDFs> --motor gliner-atual --motor gliner-large --motor lener --motor app-local --sombra
   ```
   Na tela, so numeros: recall do que deve ser trocado, o que marcou de quem deve ficar em claro, o que
   marcou fora do gabarito e termos protegidos atingidos. O detalhe por entidade fica em JSON privado.
3. **LeNER dentro do app** (H8, em avaliacao): `--motor app-local+lener` mede o motor local inteiro com o
   BERT do LeNER-Br somado ao GLiNER, pelos mesmos filtros. Para usar no app, `"extra_ner": "lener"` no
   `config.json` (desligado por padrao) e `pip install transformers`.

## Pipeline unico (aceite pendente)

O motor local e o motor LLM estao passando a usar o mesmo pipeline, para que a
deteccao seja a unica diferenca entre eles (`GOVERNANCA.md`, H5). A tela ainda usa o
motor local de antes. Para conferir, no lote de 25/09, que o pipeline unico gera o
mesmo texto:

```
python tools\comparar_pipelines.py <pasta com os PDFs do lote>
```

A ferramenta roda os dois caminhos com o mesmo motor local e o mesmo cache e diz
"iguais" ou grava as diferencas em `saidas\privados\comparacao_...\DIFERENCAS.txt`
(pode ter nome em claro: nao enviar).

## Motor LLM (opcional)

**Como escolher o motor.** O app abre **sempre no motor local**; a escolha nao
e gravada. Na aba **Lote**, em "Motor de descoberta", marque **LLM**: abre o
dialogo do canal (chave ou projeto, regiao, avisos). O botao "Usar este motor" so
se habilita com a configuracao em ordem; qualquer falta aparece com o motivo.
**Escolher o motor nao autoriza envio nenhum**: a escolha vale ate fechar o app e a
cada rodada o app pede a sua confirmacao (abaixo). O botao de executar diz o
motor: "Anonimizar lote · motor local" ou "Anonimizar lote com LLM · canal…".

Proposta 3.0, avaliada em `AVALIACAO-2026-09.md`. O LLM (Gemini via
LangExtract) so aponta quem aparece e com que papel; a troca continua
local e deterministica. CPF, CNPJ e afins sao mascarados **antes** de o
texto sair da maquina, mas **nomes, enderecos, fatos e valores seguem em
claro** para o provedor: por isso o canal importa. Um segundo passe do LLM
le o texto ja tokenizado e acusa o que sobrou. A saida tem a mesma estrutura do
motor local (ancora por folha, `INDICE.md`, `ELENCO.md`, sufixo de grupo
`[EMPRESA_n·Gk]`, trava de municipio/estado para empresa de uma palavra, matricula
por cartorio), mais uma secao "Papeis atribuidos pelo LLM" no `ELENCO.md`.

**Quem decide enviar e o Operador, e a cada rodada.** O app **avisa e nao
bloqueia por politica**. Antes de qualquer envio mostra: o **canal** (e a
**regiao**, no Vertex AI), a **lista de arquivos** que seguem para o provedor e o
**total de caracteres** (o texto ja com os IDs mascarados; quanto disso ja esta no
cache local e nao sera reenviado), com os avisos como informacao: sigilo fiscal
(CTN, art. 198: nomes e fatos vao em claro), residencia dos dados (a Gemini API
nao garante residencia no Brasil; no Vertex a regiao aparece) e faturamento (chave
de projeto sem faturamento cai nos termos do nivel gratuito). Na tela e um dialogo
com **Enviar** e **Cancelar (nada e enviado)**; na linha de comando a lista sai e
**so envia a rodada que trouxer `--confirmo-envio`**. A confirmacao vale para essa
rodada e expira no fim dela; a proxima pede de novo. A AUDITORIA registra a
confirmacao (so contagens), o canal, a regiao e o endpoint conferido.

**Canais.** Os dois sao cadastraveis (tela e linha de comando): `gemini-pago`
(Gemini API, chave de projeto do Google Cloud **com faturamento**; sem garantia de
residencia de dados) e `vertex-sp` (Vertex AI, projeto institucional). O canal
gratuito, o canal vazio e qualquer outro sao recusados; o antigo `ollama-local` foi
retirado. A rota de teste e de producao e a mesma: o Operador escolhe os arquivos,
testa com a API paga e troca para a institucional quando ela estiver disponivel.

**Chave (gemini-pago).** Nunca em arquivo do projeto, `config.json`, log ou
AUDITORIA. Fica no Gerenciador de Credenciais do Windows (servico
`AnonimizadorJuridico/gemini-pago`) ou na variavel
`ANONIMIZADOR_GEMINI_PAGO_CHAVE`. `GEMINI_API_KEY` e `GOOGLE_API_KEY` **nao
sao lidas** de proposito. O app mostra so a fonte ("variavel de ambiente" ou
"Gerenciador de Credenciais"). Guardar e apagar: pela tela (campo de senha) ou
por `tools\anonimizar_llm.py --guardar-chave` / `--apagar-chave`.

**Projeto e credencial (vertex-sp).** Projeto do Google Cloud e credencial: o
**arquivo de conta de servico** do projeto institucional (o app guarda so o
caminho, nunca o conteudo) ou, sem arquivo, a credencial do `gcloud auth
application-default login`. Cadastro: pela tela (projeto, arquivo, regiao) ou por
`tools\anonimizar_llm.py --guardar-vertex` / `--apagar-vertex`; ou pelas variaveis
`ANONIMIZADOR_VERTEX_PROJETO` e `ANONIMIZADOR_VERTEX_CREDENCIAL`. A regiao
(`southamerica-east1` por padrao) aparece na confirmacao e na AUDITORIA; o app nao
a forca.

**Custo e raciocinio.** A AUDITORIA de cada lote LLM traz o uso do provedor: chamadas, erros,
tokens de entrada, saida e raciocinio (cobrado como saida) e respostas cortadas pelo limite.
`--raciocinio minimo|baixo|padrao` (padrao `baixo`) limita quanto o modelo pensa; modelo que pensa
muito no nivel padrao pode devolver a resposta cortada ("fora do formato JSON"). `--verificacao 0`
desliga a segunda leitura (metade das chamadas). Refazer um lote reaproveita do cache os trechos ja
lidos.

**Piso local (ligado por padrao).** Com o motor LLM, o motor local (NER mais reconhecedores) roda em **todas** as
folhas e o resultado e a **uniao** do que o LLM e o motor local apontaram: o LLM so acrescenta protecao, nunca
tira (omissao parcial do LLM e mencao sem ancora no texto nao deixam nome em claro). Interruptor na aba de regras
(`llm_local_floor`) e, na linha de comando, `--piso-local` (padrao) e `--sem-piso-local`. A AUDITORIA e o LEIA-ME
dizem se o piso estava ligado. Custa o tempo do NER em todas as folhas.

**Folha que o LLM nao leu.** Falha, bloqueio, limite de taxa, trecho fora do cache
sem rede e resposta **vazia** do LLM nunca deixam nome em claro: a folha passa pelo
motor local (NER mais reconhecedores). O resultado, a AUDITORIA e o LEIA-ME dizem
"folhas tratadas pelo motor local por falha do LLM: arquivo: folhas". Resposta
vazia ou falha **nao vai para o cache**. Se nem o motor local puder tratar, o arquivo
fica em `quarentena\` na pasta privada e **nao** vai para a exportavel. A linha de
comando sai com **codigo 3** quando houve folha tratada pelo motor local (o texto e
gravado); `--aceitar-fallback` aceita o resultado (codigo 0). Outros codigos: 1 =
lote nao liberado, 2 = configuracao, portao ou rodada sem `--confirmo-envio`.

**O que o portao confere onde o texto sai.** O canal e um dos dois cadastrados; o
host e o oficial da Google para o canal e a regiao
(`generativelanguage.googleapis.com`, `<regiao>-aiplatform.googleapis.com`); as
variaveis `GOOGLE_GEMINI_BASE_URL` e `GOOGLE_VERTEX_BASE_URL` (desvio de endereco) e
`GOOGLE_GENAI_CLIENT_MODE`, `GOOGLE_GENAI_REPLAYS_DIRECTORY` e
`GOOGLE_GENAI_REPLAY_ID` (gravacao das chamadas em disco) **recusam** o envio, dizendo
so o nome da variavel; proxy e certificado proprio no ambiente so avisam. A chave nao
aparece em `repr` do modelo do LangExtract, e a excecao crua do provedor nunca vai
para a AUDITORIA (so tipo, codigo HTTP e status).

```
pip install -e .[llm]
python tools\anonimizar_llm.py --guardar-chave
python tools\anonimizar_llm.py --verificar --canal gemini-pago
python tools\anonimizar_llm.py autos.pdf --canal gemini-pago
python tools\anonimizar_llm.py autos.pdf --canal gemini-pago --confirmo-envio
python tools\anonimizar_llm.py autos.pdf --canal vertex-sp --projeto MEU-PROJETO --confirmo-envio
```

O segundo `autos.pdf` mostra o que seria enviado e **nao envia nada**; o terceiro
envia. O paralelismo padrao e **4** chamadas simultaneas (`--paralelo N` muda; numa
chave nova, valores altos geram erro 429). Antes de qualquer envio real, passe o ensaio sem rede (abaixo).

**Roteiro do L4 (primeiro uso real).** 1. Guardar a chave (ou cadastrar o Vertex) e
conferir com `--verificar`. 2. Rodar `python tools\ensaio_llm.py` (todas as
conferencias passam). 3. Um documento sintetico minusculo com `--paralelo 2` e
`--confirmo-envio`: o modelo existe e responde, o JSON volta no formato, 0 falhas por
trecho na AUDITORIA, `cache_llm` gravado; repetir com `--sem-rede`. 4. Um documento
real curto, que o Operador escolhe, lendo a lista de arquivos e caracteres antes de
confirmar; conferir o elenco na tela e regravar sem rede. 5. O lote do caso de
referencia (C3) pelo mesmo caminho, com as duas reguas
(`tools\medir_estrutura.py` e `tools\medir_vazamento.py`). Cada passo so depois do
anterior; cada rodada pede a sua confirmacao.

**Revisao do elenco.** Depois da rodada, "Revisar elenco" mostra uma linha por
entidade (tipo, nome, papel, mencoes, token) com **Tokenizar / Manter**. A
decisao fica fixa (`fixo: true`) e **regrava sem rede**, so com o cache:

```
python tools\anonimizar_llm.py autos.pdf --canal gemini-pago --sem-rede --elenco saidas\privados\lote_...\elenco.json
```

Privados: `saidas\privados\lote_...\elenco.json` (quem e quem, com originais,
token e numero de mencoes; edite `acao`), `elenco_revisado.json`,
`AUDITORIA_PRIVADA.txt` (palavras de marca recusadas) e `quarentena\` (arquivo retido).
Exportavel: `saidas\exportaveis\lote_...\ELENCO.md` (token, tipo, papel e mencoes,
sem nome).

**Ensaio sem rede.** `python tools\ensaio_llm.py` roda o caminho inteiro (portao,
confirmacao por rodada, rodada, revisao, regravacao, as duas reguas, **falha forcada
do LLM com o motor local e quarentena**, recusa de variaveis de desvio e de gravacao
do SDK) com um PDF sintetico, um LLM falso, chave e cofre falsos e os sockets
bloqueados, e confere que a chave falsa nao aparece em nenhum arquivo. Passa-se esse
ensaio antes de qualquer chamada real.
