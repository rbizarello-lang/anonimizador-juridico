# Nexus → Anonimizador: operação de cada linha (seletor de operação, H9-l)

**Para:** condutor do projeto Nexus · **De:** Anonimizador Jurídico (app 0.4.6) · **Data:** 19/10/2026
**Contrato:** `saidas\privados\regras\formato_exportacao_nexus.txt`. Este texto é a seção 2C a acrescentar lá. Exemplos inventados; nenhum dado de caso.

## Por que

Na avaliação do lote de 19/10 (2.492 folhas), o Anonimizador recebeu a **carteira inteira** do Nexus como lista do caso: 177 pessoas, 233 empresas, 1.653 processos e 5.375 CDAs. Com uma lista tão ampla, prenomes e sobrenomes de pessoas de outras operações casavam com palavras de qualquer processo. Exemplos: "Paulo" dentro de "São Paulo" (89 vezes) e palavras comuns em minúscula (129 vezes).

A solução decidida pelo Operador é que o Anonimizador use **só a lista da operação** que está sendo anonimizada. Dentro dessa lista, nome inteiro ou parcial, prenome ou sobrenome, junto ou isolado, é **sempre** trocado.

O Anonimizador já tem o seletor ("Operação deste lote", na aba Caso). Para ele funcionar, cada linha do arquivo "Para o Anonimizador" precisa dizer a que operação pertence.

## O que muda no arquivo exportado

1. **Coluna nova `operacoes` em todas as abas:** Pessoas, Empresas, Processos, CDAs, Veiculos e, quando existirem, Contatos e Vinculos.
   - **Valor:** o `id` da operação no Nexus (`operation.id`). Quando a linha pertence a várias operações, os ids vêm separados por `; `. Exemplo: `op-7f3a; op-91c2`.
   - **Use o id, não o nome:** o id é estável, e o nome pode mudar. O id não pode conter `;`, `,` nem `|`.
   - **Toda linha precisa da coluna preenchida.** No Anonimizador, uma linha sem operação vale em **todas** as operações (é o lado seguro), e isso reabre o falso positivo que motivou a mudança.
2. **Aba nova `Operacoes`**, com as colunas `id` e `nome`, uma linha por operação exportada. Serve para o seletor mostrar o nome legível, por exemplo "Operação Fictícia Norte (op-7f3a)".
3. **Mesmo CPF, CNPJ, nome, processo ou CDA em várias operações:** continua saindo **uma linha só**, com todas as operações na coluna. O Anonimizador também aceita linhas repetidas, uma por operação, e soma as operações sem contar como descarte. Mas uma linha só é o formato preferido.
4. **Recorte "carteira inteira":** passa a ser o modo recomendado para o Anonimizador. Importa-se uma vez e escolhe-se a operação a cada lote. O recorte "operação atual" continua válido e sai com a coluna preenchida com a própria operação.

## Como cada linha herda a operação (no Nexus)

| Aba | De onde vem `operacoes` |
|---|---|
| Pessoas | `operationId` de cada registro em `people` que gerou a linha. O mesmo CPF em vários registros soma as operações. |
| Empresas | `operationId` dos registros em `people` (PJ) **e** das execuções/vínculos que deram origem à empresa (CNPJ juntado por `addCnpjEntry`). |
| Processos | `operationId` do registro em `executions` e, para o processo que vem de `debts`, o `operationId` da CDA. |
| CDAs | `operationId` do registro em `debts`. |
| Veiculos | `operationId` do bem (`assets`); se faltar, as operações do titular (`holderId`). |
| Contatos / Vinculos | As operações do titular ou da pessoa da linha. |

## Coordenadas no código do Nexus

Pasta `C:\Users\User\Documents\NEXUS CURSOR\src\`. As linhas são as de 19/10/2026.

1. **`lib/export-anonimizador.js`, constante `ANON_HEADERS` (linhas 13–19):** acrescentar `'operacoes'` ao fim de cada aba (Pessoas, Empresas, Processos, CDAs, Veiculos), e criar `Operacoes: ['id', 'nome']`.
2. **`lib/export-anonimizador.js`, `buildAnonimizadorWorkbook` (linha 187 em diante):**
   - Hoje cada linha é montada direto num array (`empRows.push` na linha 251, `pesRows.push` na 295, `procs.set` nas 310 e 315, `cdas.set` na 324, `veic.set` na 336). As junções por documento ou número (`procs`, `cdas`, `veic` são `Map`) passam a guardar também um `Set` de `operationId`. Ao final, o `Set` é escrito como `[...ops].join('; ')` na coluna `operacoes`.
   - O mesmo vale para a junção de pessoas (perto das linhas 280–300, onde estão `joinUnique(x.vinculo)` e a contagem de homônimos) e para a de empresas: somar o `operationId` de cada registro juntado.
   - Montar a aba `Operacoes` a partir de `src.operations` (já filtrado por `filterDataForExport` quando o recorte é uma operação): uma linha `[o.id, o.name || o.id]` por operação.
   - Incluir `{ name: 'Operacoes', rows: [ANON_HEADERS.Operacoes, ...] }` na lista `sheets` (linha 339 em diante), **antes** de Pessoas.
3. **`lib/export.js`, `filterDataForExport` (linha 118):** não precisa mudar. No recorte de operação ela já reduz `operations` e as coleções com escopo (`SCOPED_COLS`, linha 22) à operação.
4. **`app.jsx`, `runAnonimizadorExport` e `renderAnonimizadorBlock` (linhas ~6617–6632):** nada obrigatório.
   - Sugestão: no bloco de exportação, um aviso curto: "O Anonimizador escolhe a operação na aba Caso; exporte a carteira inteira uma vez".
   - Sugestão: mostrar no resumo (`counts`) quantas linhas ficaram sem operação. O ideal é 0.
5. **Diagnóstico (lista `diagnostics`):** nova verificação `nivel: 'aviso'`, `codigo: 'sem_operacao'`, para a linha cujo registro não tem `operationId`. O arquivo não é bloqueado, mas a linha sai listada.
6. **Testes do Nexus:**
   - Para a mesma pessoa em duas operações, esperar uma linha com `op-a; op-b`.
   - Para processo vindo de CDA, esperar a operação da CDA.
   - A aba `Operacoes` deve ter uma linha por operação.
   - Nenhuma linha pode sair com `operacoes` vazio quando todos os registros têm `operationId`.

## O que o Anonimizador já faz (0.4.6)

- **Importação:** lê a coluna `operacoes` (também aceita `operacao`, `operacao_id`) em qualquer aba e a aba `Operacoes`. O resumo antes de salvar mostra "Operações: N (nomes)".
- **Seletor:** a aba Caso tem o campo "Operação deste lote", com a opção "Todas as operações (lista inteira)" e cada operação pelo nome. A escolha fica gravada no `config.json` da pasta privada.
- **Rodada:** usa só os nomes, documentos, processos, CDAs, grafias e contatos da operação escolhida. O que foi digitado à mão na aba Caso, sem operação, vale sempre.
- **AUDITORIA:** registra "Operacao escolhida na aba Caso: nome (id)", com as contagens da lista daquela operação.
- **Linha de comando:** `tools/anonimizar_llm.py --operacao <id>`, ou `--operacao todas`.
- **Planilha antiga, sem a coluna:** continua aceita. Sem operações, o seletor só oferece "Todas".
