# Grupo SBF (SBFG3)

<!--
Este é o esqueleto da sua initiation. Tudo em comentário é orientação: apague ao preencher.
Escreva em primeira pessoa.
-->

| | |
|---|---|
| Recomendação | ___ |
| Preço-alvo (12 meses) | R$ ___ |
| Preço de referência | R$ ___ em dd/mm/aaaa |
| Upside | ___% |
| Data | dd/mm/aaaa |

## Tese em três pontos

<!--
Três frases, cada uma com mecanismo + evidência + implicação para o valor.
Termine com: "O que me faria mudar de ideia: ___".
-->

1. ___
2. ___
3. ___

## O que o preço atual já embute

<!--
Rode `python valuation.py` e leia `reverse_dcf` em valuation.json: ele inverte o
preço e mostra o ROIC e o crescimento que o mercado precisa para justificar a ação.
Escreva se você acha isso fácil, difícil ou impossível de entregar, e por quê.
-->

## Premissas

<!--
Preencha premissas.json e justifique aqui cada número com uma fonte ou um raciocínio seu.
O DCF só roda quando não houver null.
-->

| Premissa | Valor | Por que esse número |
|---|---|---|
| Crescimento de receita | | |
| Margem EBIT | | |
| Capex / receita | | |
| Capital de giro / receita | | |
| Alíquota normalizada | | |
| WACC | | |
| Crescimento na perpetuidade | | |

## Valuation

<!--
Cole a saída do DCF e a tabela de sensibilidade WACC × g (valuation.json ou valuation.xlsx).
Inclua cenários bull/base/bear com o preço de cada um.
-->

## Comparáveis

<!--
Múltiplos dos pares NA MESMA DATA do preço de referência.
Explique por que SBFG3 merece prêmio ou desconto.
-->

## Riscos e catalisadores

<!-- Riscos que derrubam a tese; eventos com data que podem destravar ou destruir valor. -->

## Antes de publicar

- [ ] Preço, balanço e comparáveis na mesma data de corte (hoje há preço de 03/06/2026, contábil de 30/06/2026 e comparáveis de 31/10/2025).
- [ ] Nenhum `null` em `premissas.json`.
- [ ] Planilha gerada com `python valuation.py --xlsx` e conferida à mão.
- [ ] Versão em PDF de 8 a 15 páginas e um resumo de uma página.

## Dados e código

| Arquivo | O que faz |
|---|---|
| `data/fluxo_caixa_cvm.py` | DFC, DRE e balanço direto dos arquivos da CVM (gera `data/fluxo_caixa.json`) |
| `data/coletar_sbfg3.py` | Cotações, indicadores e pares pela API da Fintz (exige chave; saída não versionada) |
| `data/analisar_sbfg3.py` | Consolida margens, retornos, múltiplos e percentis em `data/analise.json` |
| `valuation.py` | Reverse DCF (sempre) e DCF (quando as premissas estiverem preenchidas) |
| `exportar_xlsx.py` | Exporta a valuation para Excel |
