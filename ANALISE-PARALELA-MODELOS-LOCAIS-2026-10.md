# Análise paralela: modelos offline para manter a estrutura e impedir vazamento

**Data:** 06 e 07/10/2026. **Natureza:** parecer de planejamento. Nenhum código foi alterado.
**Base lida:** `GOVERNANCA.md`, `AVALIACAO-2026-09.md`, `PANORAMA-2026-10.md`, `local_ner.py`, `llm_engine.py`.

## Estado do projeto que ancora a análise

- NER local: GLiNER `urchade/gliner_multi-v2.1`, limiar 0,75, em CPU no Windows. Presidio cobre IDs estruturados.
- Motor LLM opcional: Gemini 3.1 Flash Lite (canais `gemini-pago` e `vertex-sp`), com piso local e juízo do lote.
- A governança já pôs "LLM local como motor" fora do ciclo. Condição de reabertura: só como árbitro residual, temperatura 0, schema fechado, span igual a substring.
- O canal `ollama-local` foi retirado no H2 (I7) por falhas de rede e de chave, não por qualidade do modelo.
- A avaliação de setembro conclui que a maior parte do vazamento vem das regras do app e que **não existe medida de recall**.

## Achados

1. **Estrutura e vazamento são problemas distintos.** A perda de estrutura vem da extração e das regras (corte de frases em `## Evento N`, descartes por prefixo e termos genéricos). Resolve-se com testes determinísticos, não com modelo.
2. **Vazamento é problema de recall e tem duas camadas de modelo.**
   - Camada 1, encoders de NER (span é sempre substring): GLiNER large, GLiNER2 PT-BR, BERT treinado no LeNER-Br. Rodam em CPU. Fraqueza conhecida em CAIXA ALTA exige a passada-sombra de caixa.
   - Camada 2, LLM pequeno como árbitro residual, só em parágrafos já suspeitos.
3. **Gemma 4** (abril de 2026, Apache 2.0): E2B, E4B, 26B MoE (3,8B ativos) e 31B denso. Candidato inicial para a camada 2: **E4B**, em processo via llama-cpp-python, sem servidor, porta, proxy ou chave. O 26B MoE merece teste por custar cálculo de 4B com qualidade maior.
4. **EmbeddingGemma 2** (anunciado em 06/10/2026, 740M, multimodal) é modelo de embedding. Não detecta nomes. Único encaixe possível é ligar grafias da mesma entidade no lote, hoje feito por regra e registrado como fora do ciclo. Não encontrei a ficha técnica da versão 2 na busca; só a primeira geração (308M). Tratar o anúncio como não verificado.
5. **Não se trata de trocar a chave da API pelo Gemma nem de "ajustar" o GLiNER.** O Gemma entra como canal adicional no pipeline existente. O GLiNER atual vira linha de base na comparação. O Gemini só sai se a medição mostrar recall equivalente.

## Computação (estimativas, não medidas)

| Modelo | Memória Q4 | Onde roda |
|---|---|---|
| BERT LeNER-Br, GLiNER base e large | 1 a 3 GB | CPU |
| Gemma 4 E2B | ~2 GB | CPU, árbitro residual |
| Gemma 4 E4B | ~3 a 4 GB | CPU para residual; GPU de 8 GB para o lote inteiro |
| Gemma 4 26B MoE | ~16 a 18 GB | GPU de 24 GB, ou CPU com 32 GB de RAM |
| Gemma 4 31B | ~18 a 20 GB | GPU de 24 GB |

Os tempos por lote citados na conversa são ordens de grandeza sem benchmark. O gargalo em CPU é o prefill.

## Próximos testes, em ordem

1. **Gabarito e medida de recall.** Marcar à mão nomes e empresas de um lote real e escrever o comparador. Bloqueia todos os demais.
2. **Motores de NER em CPU** contra o gabarito: GLiNER atual, GLiNER large e BERT LeNER-Br, com e sem passada-sombra de caixa. Medir recall, precisão e termos protegidos intactos.
3. **Gemma 4 E4B como árbitro residual**, em processo, gramática JSON, temperatura 0, substring conferida, resultado passando pelo juízo do lote.
4. **Gemma 4 no documento inteiro**, só se o passo 3 ganhar e houver GPU de 16 GB ou mais. Comparar com o Gemini no mesmo lote e decidir sobre a chave.
5. **Correções determinísticas de estrutura**, em paralelo e sem modelo.

Fora da lista por ora: EmbeddingGemma 2, GLiNER2 e fine-tune próprio.

## Risco a vigiar

Cada novo motor deve manter as regras do H2 e do H3: o modelo nunca vai à rede sozinho, `cache_llm` e mapa tratados como material sigiloso, e o LLM só acrescenta proteção, nunca tira.
