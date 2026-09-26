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

A versao em uso aparece no cabecalho da janela ("v0.4.1 · codigo ...") e no
topo da `AUDITORIA.txt` e dos `LEIA-ME.txt` de cada lote. Se a janela mostrar
um codigo diferente do esperado, feche o app e abra de novo. Se uma copia
anterior tiver travado ao fechar (viva, sem janela), o proximo clique a
encerra e abre outra; nao e preciso o Gerenciador de Tarefas. O Gemini nao
roda no app: a leitura de original e Markdown tokenizado, se houver,
fica fora. O mapa reversivel permanece so nesta maquina.

## Pipeline com LLM (experimental, por linha de comando)

Proposta 3.0, avaliada em `AVALIACAO-2026-09.md`. O LLM (Gemini via
LangExtract) so aponta quem aparece e com que papel; a troca continua
local e deterministica. CPF, CNPJ e afins sao mascarados **antes** de o
texto sair da maquina. Um segundo passe do LLM le o texto ja tokenizado e
acusa o que sobrou. Pagina que o LLM nao conseguiu ler bloqueia a
exportacao.

```
pip install -e .[llm]
set GEMINI_API_KEY=...        (projeto com faturamento; chave gratuita NAO)
python tools\anonimizar_llm.py autos.pdf
python tools\anonimizar_llm.py autos.pdf --provedor vertex --projeto MEU-PROJETO
python tools\anonimizar_llm.py autos.pdf --elenco saidas\privados\lote_...\elenco.json
```

Saidas extras: `saidas\privados\lote_...\elenco.json` (quem e quem, com
originais — privado; edite `acao` para `manter`/`tokenizar` e rode de novo)
e `saidas\exportaveis\lote_...\ELENCO.md` (token, tipo e papel, sem nome).
