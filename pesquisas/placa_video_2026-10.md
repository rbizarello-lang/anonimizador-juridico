# Parecer de compra: RTX 5060 Ti 16 GB × RTX 3090 24 GB

Data: 17/10/2026. Trabalho só de pesquisa; nenhum código do anonimizador foi alterado.
Preços e números de terceiros mudam rápido: confira-os no dia da compra.

## 1. Conclusão em uma página

**Recomendação: RTX 3090 24 GB usada, comprada no mercado nacional, com teste antes de pagar, junto com uma fonte nova de 850 W.**
Não importe da China: depois dos impostos sai mais cara que a usada daqui e fica sem garantia nenhuma.
Antes de comprar, meça o gabinete (seção 3). Se a 3090 não couber, ou se o Operador não quiser
o risco de uma placa usada sem garantia, compre a RTX 5060 Ti 16 GB. É uma boa compra para o anonimizador,
mas fica no limite para os projetos futuros.

| Pergunta do Operador | 5060 Ti 16 GB | 3090 24 GB | O investimento maior se justifica? |
|---|---|---|---|
| (1) Velocidade | ~448 GB/s; geração ~60 % da 3090 | ~936 GB/s; geração ~1,6–1,7× a 5060 Ti | **Sim**, mas só pesa nos modelos grandes |
| (2) Modelos maiores | Teto prático: ~14 B denso, ou MoE ~20–30 B com especialistas na RAM | Denso de 27–32 B inteiro na placa; Nemotron Bolt 74B-A9B (2 bits) cabe | **Sim, é o motivo principal** |
| (3) Anonimizador atual | 1h38 → **~20 min** (estimado) | 1h38 → **~15 min** (estimado) | **Não**: a 5060 Ti já entrega ~85 % do ganho |
| (4) Usos futuros (OCR por visão, triagem, LoRA, modelos "quase online") | Aperta: OCR pequeno vai bem; LoRA só de modelos ≤ 8–14 B | Folga real; QLoRA até ~30 B; VLMs de 30 B quantizados | **Sim** |

**Custo total estimado:**
- 5060 Ti: **R$ 3.600–4.900**. A fonte atual provavelmente serve.
- 3090 usada: **R$ 4.800–6.000**. Somando a fonte de 850 W (R$ 600–1.000), o total fica em **R$ 5.400–7.000**.

O pulo de 16 para 24 GB custa de R$ 1.500 a 2.500 a mais e é o que separa a placa que só acelera o anonimizador
da placa que roda a geração atual de modelos locais de 30 B.

**Ampliar a RAM:** sim, em qualquer cenário, mas como segunda etapa (seção 7).

## 2. O que foi possível ler deste PC (sem instalar nada)

| Item | Leitura | Consequência |
|---|---|---|
| Placa-mãe | **Gigabyte A520M K V2**, BIOS F1 (12/2022), micro-ATX | Chipset de entrada. Tem um único slot PCIe x16 útil, então **duas placas está fora de cogitação** |
| CPU | Ryzen 5 4500 (Renoir, Zen 2) | **Só PCIe 3.0**: a CPU limita o slot a 3.0, qualquer que seja a placa |
| Link atual da RX 580 | PCIe 3.0 (8 GT/s) ×16 | Slot e CPU confirmados em 3.0 ×16 |
| RAM | **1 × 8 GB Samsung DDR4-3200 + 1 × 16 GB DDR4-3200** (pentes diferentes) | Dual-channel assimétrico: só 16 GB rodam em dual, o resto em single. Isso prejudica o offload de MoE para a RAM |
| SSD | NVMe Kingston NV2 1 TB | Suficiente para guardar os modelos |
| Gabinete | Tipo "Desktop" (SMBIOS); dimensões não legíveis | **Medir à mão** |
| Fonte | **Não legível por software** | **Abrir o gabinete e conferir** |

**Efeito do PCIe 3.0 em cada placa:**
- A **5060 Ti tem só 8 linhas** (PCIe 5.0 ×8). Neste PC vira PCIe 3.0 ×8, cerca de 7,9 GB/s.
- A **3090 tem 16 linhas** (4.0 ×16). Neste PC vira 3.0 ×16, cerca de 15,8 GB/s, o dobro.

Com o modelo inteiro na VRAM, o PCIe quase não importa. Já com offload para a RAM, o llama.cpp copia os pesos
para a placa durante o prefill de lotes grandes, e aí a 5060 Ti fica com metade da banda neste PC.
É uma desvantagem específica desta placa-mãe.

## 3. O que precisa ser conferido à mão antes de comprar

1. **Fonte**: abra o gabinete e leia a etiqueta lateral. Anote:
   - potência (W) e certificação (80 Plus Bronze, Gold…);
   - marca e modelo;
   - **quantos conectores PCIe de 8 pinos (6+2)** a fonte tem, e se vêm em **cabos separados**.
2. **Para a 5060 Ti**: consumo de ~180 W, **1 conector de 8 pinos**. A RX 580 de hoje usa um conector parecido e consome ~185 W.
   Se a fonte atual tiver **≥ 500 W de marca razoável**, em geral serve.
3. **Para a 3090**:
   - consumo de **~350 W**, com picos transitórios bem acima disso (problema conhecido da geração Ampere);
   - a NVIDIA recomenda **750 W** no total; com a margem dos picos, prefira **850 W, 80 Plus Gold, ATX 3.x**;
   - precisa de **2 ou 3 conectores de 8 pinos em cabos separados** (não use o cabo em "Y");
   - na prática, **conte com trocar a fonte**.
4. **Espaço no gabinete**:
   - as 3090 de mercado medem **~300–336 mm de comprimento** e ocupam **2,5 a 3 slots**;
   - meça a distância entre o espelho traseiro e a frente do gabinete (ventoinhas e radiador incluídos);
   - a 5060 Ti "Dual" mede ~200–240 mm e cabe em quase tudo.
5. **Refrigeração**: a 3090 joga ~350 W de calor dentro de um gabinete micro-ATX. Ventoinha de entrada e de saída deixam de ser opcionais.
6. **BIOS**: a F1 é de 2022. Atualizar não é obrigatório, mas uma versão mais nova costuma trazer o Resizable BAR e correções.
   Faça isso antes de trocar a placa, ainda com a RX 580 funcionando.

## 4. Verificação das alegações salvas no X

| Alegação | Verificação | Veredito |
|---|---|---|
| Nemotron 3.5 Bolt 74B-A9B: 9 B ativos, 256K de contexto, texto/imagem/vídeo, cabe em 24 GB, GGUF oficial | O card oficial confirma: híbrido Mamba-2/Transformer MoE, 74 B totais e 9 B ativos, até 256K, entrada de texto, imagem e vídeo (até 2 min), pacote de 24 GB, licença OpenMDW-1.1, publicado em 15/10/2026. Existe repositório GGUF oficial. | **Confirmada** |
| "Treinado para 2 bits" | Quantização com treino ciente (QAT). Especialistas em IQ2_XXS (~2,06 bits), camadas Mamba em NVFP4, embeddings em Q8_0 | **Confirmada** (formato misto, não 2 bits puros) |
| Números contra Qwen3.6-35B-A3B (Terminal-Bench 51,0 × 44,4; GPQA 86,4 × 83,4; HLE 25,3 × 19,6; BrowseComp 66,8 × 48,7) | Os números batem com o card (50,98 / 44,38; 86,43 / 83,40; 25,30 / 19,56; 66,75 / 48,74). Há uma ressalva: o Bolt foi medido em 2 bits e o Qwen em BF16, e quem mediu foi a própria NVIDIA | **Confirmada**, mas é benchmark do fabricante |
| Gráfico com 13 benchmarks | Bate com o card. O Bolt **perde** em AA-Omniscience accuracy (16,9 × 19,5) e non-hallucination (61,3 × 65,5) e empata no PinchBench | **Confirmada**. Atenção: as duas derrotas medem alucinação e conhecimento factual, justamente o que importa num uso jurídico |
| "3090 não está na lista de hardware do vLLM" | O card lista o vLLM em Ampere **A100**, Hopper e Blackwell (SM120/121). O único Ampere citado é o A100; **a 3090 não aparece** | **Confirmada** |
| "1 × 3090: Bolt a 80 tok/s, e o contexto de 262K cabe" | Medição da comunidade (r0b0tlab, **ExLlamaV3/EXL3 2,08 bpw**, não llama.cpp): 80,4 tok/s de geração com cache de 8K e 68,7 tok/s depois de um prefixo de 32K. Prefill de ~2.000 tok/s. Contexto de 262.080 tokens cabe em ~22 GB com KV de 8 bits. Mediu ~345 W de consumo médio | **Confirmada para ExLlamaV3.** No llama.cpp e no Ollama não achei medição em 3090. Os "84 tok/s" da NVIDIA vieram de outra máquina e usavam decodificação especulativa |
| "1 × 3090: Qwen3.8 Flash-Next a 70 tok/s" | O modelo tem 125 B totais e 6 B ativos (mais uma tabela de n-gramas de 51 B). Em 2,5 bpw ocupa 41,5 GiB, **não cabe em 24 GB**: parte vai para a RAM. A receita medida numa 3090 deu **~38,6 tok/s** com MTP, e duas 3090 com offload deram 36,8 | **Não confirmada.** Encontrei metade disso, e só com bastante RAM. **Com os 24 GB de RAM deste PC, não roda** |
| MiniCPM-V 4.7: 35B-A3B e 1B; OCRBench 90,4 | A versão 35B-A3B e a de 1B existem, sob Apache 2.0. Agregadores dão 90,4 a 91,2 no OCRBench, mas o upload original saiu **sem model card e sem benchmarks oficiais**. Um teste prático achou o OCR irregular (pulou urdu, errou correções manuscritas) | **Parcial.** Os números não têm fonte primária. "17 tok/s em iPad" e "suporte AMD/Intel/Apple" não foram verificados |

**Ponto prático sobre o Bolt:** ele ocupa a 3090 inteira, sobrando pouco para outro modelo carregado ao mesmo tempo.
Os ~80 tok/s vêm do ExLlamaV3, e não do Ollama que o anonimizador usa hoje.
As camadas em NVFP4 rodam na 3090, mas sem a aceleração de FP4, que só existe no Blackwell.

## 5. Velocidade: o que a largura de banda prevê e o que foi medido

A geração de tokens é limitada pela largura de banda da memória, porque cada token lê todos os pesos ativos.
O prefill (ler o prompt) é limitado pela capacidade de cálculo.

| Placa | Banda | VRAM | Arquitetura |
|---|---|---|---|
| RTX 3090 | ~936 GB/s (GDDR6X, 384 bits) | 24 GB | Ampere (sm_86) |
| RTX 5070 Ti | ~896 GB/s (GDDR7, 256 bits) | 16 GB | Blackwell |
| RTX 5060 Ti 16 GB | ~448 GB/s (GDDR7, 128 bits) | 16 GB | Blackwell (sm_120) |
| RTX 4060 Ti 16 GB | ~288 GB/s | 16 GB | Ada |
| RX 580 (atual) | ~224 GB/s (versão 2048SP) | 8 GB | Polaris, via Vulkan |

**Medições de llama.cpp, mesmo site, mesma metodologia** (hardware-corner.net, contexto de 4K, tok/s):

| Modelo | 3090 prefill / geração | 5060 Ti prefill / geração | 5070 Ti prefill / geração |
|---|---|---|---|
| Qwen3 8B Q4_K | 4.050 / **115** | 2.965 / **69** | 5.557 / **121** |
| Qwen3 14B Q4_K | 2.459 / **70** | 1.743 / **41** | 3.265 / **74** |
| gpt-oss 20B MXFP4 | 4.400 / **148** | 3.585 / **92** | 6.178 / **156** |
| Gemma4 26B-A4B Q4_K | 3.626 / 119 | não cabe | não cabe |
| Qwen3.5 27B Q4_K (denso) | 1.104 / 34 | não cabe | não cabe |
| Gemma4 31B Q4_K (denso) | 1.156 / 35 | não cabe | não cabe |
| Qwen3.5 35B-A3B MXFP4 | 2.622 / 111 | só com offload (~44 tok/s em outro review) | só com offload |

**Leitura da tabela:**
- Nos modelos que cabem nas duas, a 3090 gera **~1,6–1,7× mais rápido** e faz o prefill **~1,2–1,4× mais rápido**. Bate com a razão de banda (936/448 ≈ 2,1) descontadas as perdas.
- O Blackwell compensa parte da diferença no prefill.
- A 5070 Ti empata com a 3090 em velocidade e ganha no prefill, mas fica nos mesmos 16 GB da 5060 Ti.
- A diferença decisiva está nas linhas **"não cabe"**: são os modelos de 26–35 B que ficam de fora da 5060 Ti.

## 6. Estimativa para o anonimizador (lote real de 130 folhas)

**Medido hoje:**
- 376 chamadas à Gemma 4 E4B, a ~13 s cada (~82 min);
- GLiNER na CPU, ~11 min;
- total de ~1h38, o que deixa **~5 min de "resto"** (leitura, OCR, montagem, gravação).

**Premissas (não medidas; servem para ordem de grandeza):**
- Cada chamada tem ~1.500 tokens de prompt e ~150 tokens de saída (lista de entidades em JSON), dentro do contexto de 4.096.
- Na RX 580 via Vulkan, isso daria ~7 s de prefill (~200 tok/s) mais ~6 s de geração (~25 tok/s), o que fecha com os 13 s medidos.
- A E4B tem ~4 B parâmetros efetivos. A velocidade se escala a partir do Qwen3 8B da tabela acima: cerca de 1,5–1,8× mais rápida que um 8B.
- Overhead do Ollama por chamada: ~0,3 s.
- GLiNER na GPU via PyTorch CUDA: 5–10× mais rápido que na CPU.

| Cenário | Por chamada | 376 chamadas | GLiNER | Resto | **Total** |
|---|---|---|---|---|---|
| RX 580 (medido) | ~13 s | ~82 min | ~11 min | ~5 min | **~1h38** |
| 5060 Ti, chamadas em série | ~0,3 + 1,5 + 0,3 ≈ 2,1 s | ~13 min | ~1,5 min | ~5 min | **~20 min** |
| 3090, chamadas em série | ~0,2 + 0,9 + 0,3 ≈ 1,4 s | ~9 min | ~1 min | ~5 min | **~15 min** |
| 5060 Ti, 4 em paralelo (`OLLAMA_NUM_PARALLEL=4`) | — | ~5 min | ~1,5 min | ~5 min | **~12 min** |
| 3090, 4 em paralelo | — | ~3,5 min | ~1 min | ~5 min | **~10 min** |

**Conclusões para o curto prazo:**
- A troca de placa derruba o tempo de ~1h38 para 10–20 min **em qualquer uma das duas**.
- A diferença entre elas fica em **~5 min por lote**. Com paralelismo, ~2 min.
- O "resto" fixo de ~5 min passa a dominar: é a lei de Amdahl.
- Para o anonimizador, a 3090 **não se paga**.
- A RX 580 também perde por usar Vulkan; em CUDA, o Ollama e o llama.cpp são mais maduros.
- **Validar as premissas sem gastar nada:** a API do Ollama devolve `prompt_eval_count`, `prompt_eval_duration`,
  `eval_count` e `eval_duration` em cada resposta. Um log desses campos num lote mostra a divisão real entre prefill e geração.
  Se a saída for bem maior que 150 tokens, as duas placas ficam mais lentas na mesma proporção, e a vantagem relativa da 3090 aumenta um pouco.

## 7. O que cabe em 16 GB × 24 GB (e o papel da RAM)

| Classe de modelo | 5060 Ti 16 GB | 3090 24 GB |
|---|---|---|
| Gemma 4 E4B, qwen3:4b (uso atual) | Folga, com vários slots em paralelo | Folga |
| OCR por visão pequeno (GLM-OCR ~1 B, LightOnOCR-2 1 B, MiniCPM-V 1 B) | Folga, mesmo em FP16 (~2–3 GB) | Folga |
| LightOnOCR-3 4 B | Cabe (~10 GB em FP16) | Cabe |
| Densos de 8–14 B, Q4 | Cabe | Cabe e roda ~1,7× mais rápido |
| gpt-oss-20B | Cabe | Cabe |
| Densos de 24 B (Mistral Small etc.), Q4 | No limite, com contexto curto | Cabe com contexto longo |
| Gemma 4 26B-A4B, Q4 | Só com especialistas na RAM | **Cabe: ~119 tok/s** |
| Densos de 27–32 B (Qwen3.5 27B, Gemma4 31B), Q4 | **Não** (offload de modelo denso fica lento demais) | **Cabe: ~34 tok/s** |
| MoE de 35B-A3B (Qwen3.5/3.6, MiniCPM-V 4.7), Q4 | Só com especialistas na RAM (~40 tok/s), exige RAM livre | **Cabe** (MXFP4: ~111 tok/s) |
| Nemotron 3.5 Bolt 74B-A9B (24 GB) | Não, e a RAM de 24 GB não basta para o offload | **Cabe, a ~80 tok/s** (ExLlamaV3) |
| MoE de 100–125 B (Qwen3.8 Flash-Next) | Não | Só com **≥ 64 GB de RAM**, a ~35–40 tok/s |
| LoRA / QLoRA | QLoRA até ~8–14 B | QLoRA até ~30 B; LoRA em BF16 de modelos de 7–8 B |

**RAM e CPU:**
- **24 GB de RAM não sustentam offload sério.** O Windows, o app e o GLiNER já ocupam 6–10 GB, sobrando ~14 GB para especialistas de MoE.
- O par atual é **assimétrico (8 + 16 GB)**, então só 16 GB rodam em dual-channel.
- No offload, a banda da RAM (DDR4-3200 em dual, ~51 GB/s teóricos) e os 6 núcleos Zen 2 viram o gargalo.
- **Recomendação:** quando for rodar MoE grandes, trocar para **2 × 32 GB DDR4-3200 (64 GB, simétrico)**.
  A A520M K V2 tem 2 slots e aceita até 64 GB (confirmar no manual).
- Não é urgente para o anonimizador. A memória encareceu em 2026 (mesma causa do adiamento das RTX 50 Super), então pesquise o preço na hora.
- **Com a 3090, os modelos de até 24 GB rodam inteiros na placa** e a RAM deixa de ser o limite. Com a 5060 Ti, a RAM vira o limite já nos modelos de 30 B.

## 8. Riscos da 3090 usada ou importada

| Risco | Como mitigar |
|---|---|
| **Mineração 24/7** (muitas 3090 saíram de fazendas de mineração) | Comprar de pessoa física com nota ou histórico; desconfiar de lotes; checar o estado das ventoinhas e dos thermal pads |
| **GDDR6X quente**: na 3090 metade dos chips de memória fica **no verso da placa**, e a memória chega a 100–110 °C em carga longa | Testar a "Memory Junction Temperature" no HWiNFO. Se passar de ~100 °C, prever troca de pads (R$ 100–300) ou limitar a potência (ex.: 280 W, perdendo pouco em LLM) |
| **Memória com reballing ou reparo** | Recusar placa "reparada". Rodar um teste de VRAM (OCCT VRAM ou memtest_vulkan) por 20–30 min antes de pagar |
| **Fraude** (placa adulterada, chips removidos, modelo trocado) | Ver a placa funcionando, conferir no GPU-Z a VRAM de 24 GB e o barramento de 384 bits, conferir o número de série |
| **Garantia** | Usada: em geral nenhuma (a EVGA, por exemplo, saiu do mercado). Pagar por meio com proteção ao comprador (Mercado Livre com Compra Garantida) |
| **Consumo** | ~350 W em carga contra ~180 W da 5060 Ti. Lotes de ~15 min dão impacto pequeno na conta; uso contínuo (LoRA, lotes longos) pesa mais |
| **Fonte e gabinete** | Ver a seção 3. Na prática, somar R$ 600–1.000 de fonte |

**Preço em reais (outubro de 2026, anúncios de datas variadas; conferir na hora):**
- **3090 usada no Mercado Livre:** modelos comuns (Palit, Gainward, Zotac) entre **R$ 4.800 e 6.000**; topo de linha (ROG Strix) até ~R$ 12.600; anúncios abaixo de ~R$ 4.000 são suspeitos.
- **3090 importada da China:** a placa sai por ~US$ 800–1.100 lá fora. No Remessa Conforme, acima de US$ 50 incidem imposto de importação de 60% (com abatimento de US$ 20) e ICMS de ~17–20% "por dentro", o que quase **dobra** o preço: ~US$ 1.600–2.200, ou **~R$ 8.500–12.000**. Fica mais cara que a usada nacional, sem garantia e sem como devolver. **Não recomendo.**
- **5060 Ti 16 GB nova:** **R$ 3.600–4.900** no PIX (Pichau e Kabum; a Gigabyte Gaming OC foi a mais barata encontrada). Garantia de fábrica.
- **5070 Ti 16 GB nova:** **R$ 6.500–9.100** (mediana de 12 meses de ~R$ 7.780; menor preço em outubro de 2026 em torno de R$ 9.100).

## 9. Suporte de software: Blackwell × Ampere

- **3090 (Ampere, sm_86):** suporte maduro e amplo. CUDA 12 e 13 ainda a cobrem (o CUDA 13 cortou só Maxwell, Pascal e Volta). Funciona em llama.cpp, Ollama, ExLlamaV3, vLLM (a comunidade roda; só não consta na lista oficial do Bolt), PyTorch e bitsandbytes/Unsloth para QLoRA. Não tem FP8 nem FP4 em hardware: modelos em NVFP4/FP8 rodam por emulação ou conversão, economizando memória mas sem o ganho de velocidade.
- **5060 Ti (Blackwell, sm_120):** suporte já estável em Ollama, llama.cpp e PyTorch (precisa de CUDA ≥ 12.8). Tem **FP4 nativo**: o llama.cpp ganhou caminho NVFP4 nativo para sm_120 em 2026, com prefill 40–70 % mais rápido nos testes da 5090. O card do Bolt lista SM120 como alvo, mas 16 GB não comportam o Bolt. Tende a durar mais em suporte, por ser a geração atual.
- **Para o uso do Operador (Ollama, GGUF, PyTorch, GLiNER):** as duas funcionam sem atrito. A vantagem do Blackwell em FP4 é real, mas não substitui 8 GB a mais de VRAM.

## 10. Alternativas e o "esperar"

- **RTX 5070 Ti 16 GB:** tem a velocidade da 3090 e a garantia de uma placa nova, mas **os mesmos 16 GB** da 5060 Ti, por ~R$ 7.000–9.000. Para o Operador, a VRAM pesa mais que a velocidade. **Não compensa.**
- **RTX 4060 Ti 16 GB:** banda de 288 GB/s, mais lenta que a 5060 Ti e quase pelo mesmo preço. **Descartada.**
- **Duas placas (ex.: 2 × 5060 Ti):** a A520M K V2 não tem um segundo slot x16 útil, e a fonte e o gabinete não comportam. **Inviável sem trocar a placa-mãe.**
- **Esperar as RTX 50 Super (5070 Ti Super / 5080 Super de 24 GB):** em outubro de 2026 seguem **sem data**, adiadas pela falta de memória. Fala-se em 2027, e o preço deve ser alto pelo custo do GDDR7. Esperar significa ficar meses com a RX 580 (1h38 por lote) por um preço que deve ficar acima do da 3090 + fonte. **Não recomendo esperar.**
- **RTX 4090 / 5090:** fora da faixa de orçamento.

## 11. Recomendação final

**Curto prazo (anonimizador):**
- Qualquer das duas resolve o gargalo: de 1h38 para ~15–20 min, e para ~10–12 min com chamadas em paralelo.
- A 3090 ganha só ~5 min por lote.
- **Isoladamente, o anonimizador não justifica a 3090.**

**Longo prazo (OCR por visão, triagem de fatos, relatórios, LoRA, modelos novos):**
- O que separa as placas é a VRAM:
  - **24 GB** rodam inteiros os modelos de 26–35 B da geração atual (Gemma 4 26B, Qwen3.5/3.6 35B, MiniCPM-V 4.7 35B quantizado) e o Bolt 74B-A9B, além de permitir QLoRA de modelos de ~30 B;
  - **16 GB** obrigam a mandar especialistas para uma RAM que este PC ainda não tem.
- **A 3090 se justifica pelos itens (2) e (4).**

**Decisão sugerida:**
1. **RTX 3090 usada, nacional, testada** (orçamento de R$ 4.800–6.000), mais **fonte de 850 W Gold** (R$ 600–1.000), condicionada a caber no gabinete. Nesse caso, **atualizar a BIOS antes** da troca.
2. Se não couber, se a fonte nova estiver fora do plano ou se o Operador quiser garantia: **RTX 5060 Ti 16 GB**. Resolve o anonimizador por ~R$ 4.000, e os modelos de 30 B ficam para quando a RAM for de 64 GB, ou para a próxima placa.
3. **Depois (com qualquer placa):** RAM de 2 × 32 GB DDR4-3200 quando os projetos com MoE grandes começarem. Não é necessária para o anonimizador.

**Teste a fazer com a placa nova:**
- `ollama run gemma4:e4b --verbose` num trecho real, e um lote de 130 folhas com e sem `OLLAMA_NUM_PARALLEL`.
- Isso confirma ou corrige a estimativa da seção 6.

## Fontes

- Card oficial do Bolt: https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB
- GGUF oficial do Bolt: https://huggingface.co/nvidia/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-24GB-GGUF
- Medição do Bolt em 3090 (EXL3): https://huggingface.co/r0b0tlab/NVIDIA-Nemotron-3.5-Bolt-74B-A9B-EXL3-2.08bpw
- Guia Unsloth do Bolt: https://unsloth.ai/docs/models/nemotron-3.5-bolt
- Blog da NVIDIA: https://blogs.nvidia.com/blog/nemotron-3-5-super-vl/
- Qwen3.8 Flash-Next por nível de hardware: https://www.contextstudios.ai/blog/one-model-six-hardware-tiers-what-we-measured
- Receita do Qwen3.8 numa 3090: https://www.contextstudios.ai/local-ai/r0b0tlab--qwen38-flash-next-exl3-1x3090
- Benchmarks de llama.cpp por placa: https://www.hardware-corner.net/gpu-llm-benchmarks/ (páginas rtx-3090, rtx-5060-ti-16gb, rtx-5070-ti)
- MiniCPM-V 4.7: https://www.datalearner.com/en/ai-models/pretrained-models/minicpm-v-4-7-35b-a3b/analysis · https://www.orcarouter.ai/de/blog/minicpm-v-4-7-shipped-quietly · https://www.mindstudio.ai/blog/minicpm-v-4-7-local-test
- NVFP4 no llama.cpp: https://insiderllm.com/guides/fp4-inference-llamacpp-nvfp4-mxfp4/
- OCR (GLM-OCR, LightOnOCR): https://docs.z.ai/guides/vlm/glm-ocr · https://www.spheron.network/tools/gpu-recommender/zai-org/GLM-OCR/ · https://www.spheron.network/tools/gpu-recommender/lightonai/LightOnOCR-2-1B/
- Preços da 5060 Ti: https://www.pichau.com.br/placa-de-video-gigabyte-geforce-rtx-5060-ti-gaming-oc-16gb-gddr7-128-bit-gv-n506tgaming-oc-16gd · https://www.kabum.com.br/busca/5060-TI · https://www.hardwarebarato.com/produtos/placas-de-video/rtx-5060-ti-16gb
- Preços da 3090: https://lista.mercadolivre.com.br/rtx-3090-usada · https://bestvaluegpu.com/history/new-and-used-rtx-3090-price-history-and-specs/
- Preços da 5070 Ti: https://gpuprix.com/pt/brazil/gpus/geforce-rtx-5070-ti
- Riscos da 3090 usada: https://techenclave.com/t/fair-price-to-pay-for-dead-rtx-3090/422046 · https://www.igorslab.de/en/?p=292628
- Adiamento das RTX 50 Super: https://hardvance.co.uk/rtx-50-super-release-date/ · https://tech-insider.org/nvidia-rtx-50-super-delay-2026/
