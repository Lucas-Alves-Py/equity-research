# Fatores B3

<!--
Esqueleto. Tudo em comentário é orientação: apague ao preencher. Escreva em primeira pessoa.
Estrutura: pergunta → método → resultado → o que aprendi → limitações (curtas).
-->

## Pergunta

<!-- Uma ou duas frases: o que você quis descobrir e por que isso importa. -->

## Método

<!--
Em suas palavras: universo (sem financeiras), filtro de liquidez, os três fatores
(EV/EBIT, ROIC, momentum 12 meses), número de ações, frequência de rebalanceamento,
execução em D+1 ao preço bruto, benchmark (o universo, não o IBOV) e por quê.
Diga de onde veio a ideia (curso Galáxia 11) e o que é seu.
-->

## Resultado

<!--
Rode o backtest NESTE pipeline (dados públicos, sem financeiras, COM custos) e
reporte contra o universo e contra o CDI: retorno, volatilidade, Sharpe, drawdown,
mediana das janelas de 12 meses. Diga quantos modelos você testou antes deste.

Se houver acompanhamento prospectivo: data de início e link para os commits/tags
com cada seleção publicada ANTES da execução.
-->

## O que aprendi

<!--
Os casos que mostram diligência são seus: por exemplo, a escala MIL/UNIDADE da CVM
que inflava o EBIT da VIVA3 em mil vezes, o desdobramento da DIRR3 que distorcia o
momentum, e a data de recebimento do ITR como data de disponibilidade do dado.
Conte o que você viu, como descobriu e o que mudou no modelo.
-->

## Limitações

<!-- Lista curta. -->

## Código

| Pasta | O que faz |
|---|---|
| `fontes/` | Leitores da CVM (ITR/DFP, cadastro) e da B3 (capital social, proventos, eventos societários) |
| `carteira/` | Seleção point-in-time, painel de preços ajustados, índice base 100 e simulação mensal |
| `carteira/config/` | Definição congelada do modelo e regras de revisão |
| `demo/` | Exemplo com dados sintéticos que confere a conta do índice (100 → 110 → 120 → 108) |

```bash
python demo/run_demo.py
python -m pytest -q
python -m carteira.pipeline_atual        # baixa CVM e B3 e grava a seleção do mês em artifacts/
```

Os scripts `fontes/reconciliar_fintz_*.py` comparam os indicadores calculados com o painel da Fintz e só rodam com essa base local.
