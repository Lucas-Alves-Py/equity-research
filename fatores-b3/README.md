# Fatores B3

Carteira de 10 ações brasileiras escolhidas por valor (EV/EBIT), qualidade
(ROIC) e momentum de 12 meses, com dados públicos da CVM e da B3 montados
point-in-time: cada informação entra só na data em que ficou pública. O
modelo foi congelado em 28/08/2026 e, desde outubro de 2026, cada decisão
mensal é registrada neste repositório antes da compra fictícia.

## Resultado

Backtest do modelo congelado, 10 ações com pesos iguais, liquidez acima de
R$ 500 mil por dia, rebalanceamento no primeiro pregão a partir do dia 5 de
cada mês, sem custos de transação.

| Base | Período | Modelo | Universo | Volatilidade | Queda máxima |
|---|---|---:|---:|---:|---:|
| Própria (CVM e B3) | abr/2012 a nov/2025 | 28,0% a.a. | 7,4% a.a. | 23,8% | −47,9% |
| Fintz, mesma grade | abr/2012 a nov/2025 | 25,9% a.a. | 3,5% a.a. | 24,0% | −52,8% |
| Própria (CVM e B3) | abr/2012 a out/2026 | 27,5% a.a. | 8,1% a.a. | 24,2% | −47,9% |

O universo é a carteira de pesos iguais com todas as ações que passam no
filtro de liquidez, calculada pelo mesmo motor. As carteiras das duas bases
têm 67% dos nomes em comum, em média.

São resultados **dentro da amostra**: o modelo foi escolhido depois de testar
várias alternativas no mesmo período. Fonte:
[`artifacts/resultados/backtest_base_propria_2026-10-20.json`](artifacts/resultados/backtest_base_propria_2026-10-20.json).

O backtest original, que congelou o modelo, usou a base Fintz com grade de 21
pregões de dez/2011 a nov/2025: 28,0% a.a., volatilidade de 24,0%, Sharpe de
0,77 e queda máxima de −52,7%.

## Acompanhamento prospectivo

| Item | Regra |
|---|---|
| Decisão | Primeiro pregão a partir do dia 5 de cada mês, depois do fechamento |
| Registro | Commit da decisão antes da compra |
| Execução | Fechamento do pregão seguinte, preço oficial da B3 |
| Aporte | R$ 10 mil fictícios por mês |
| Medidas | Índice base 100 (só o rendimento) e patrimônio com aportes |

Primeira decisão em 06/10/2026, compra em 07/10/2026. Cada mês fica em
[`decisoes/`](decisoes/): a carteira (`carteira.csv`) e o relatório da decisão
(`relatorio.pdf`). A série diária do fundo está em
[`decisoes/fundo.csv`](decisoes/fundo.csv).

## Dados

| Fonte | Conteúdo | Acesso | No repositório |
|---|---|---|---|
| CVM Dados Abertos | ITR, DFP, FRE, FCA e cadastro de companhias | Público, sem cadastro | Não; o código baixa |
| B3, COTAHIST | Preços diários de todo o mercado | Público, sem cadastro | Não; o código baixa |
| B3, API de empresas | Capital social, proventos e eventos em ações | Público, sem cadastro | Não; o código baixa |
| NEFIN (FEA-USP) | Fatores de risco brasileiros, diários | Público, sem cadastro | Não; o código baixa |
| Fintz e Varos | Indicadores e preços ajustados | Licenciado | Não. Só serviram de gabarito nas auditorias; os scripts `fontes/reconciliar_fintz_*.py` precisam da base local |

O histórico próprio começa em abr/2012 porque os ITR abertos da CVM começam
em 2011, e o primeiro EBIT de 12 meses só fecha com o balanço anual de 2011.

## Como reproduzir

Python 3.12. As versões em `requirements.txt` são as que geraram os
resultados acima.

```bash
python -m venv .venv
.venv/Scripts/activate              # Linux/macOS: source .venv/bin/activate
pip install -r ../requirements.txt

python -m pytest -q                 # testes, sem rede
python demo/run_demo.py             # índice com dados sintéticos: 100 -> 110 -> 120 -> 108

python -m coleta.construir_historico --dia5 --inicio 2012-03-01 --fim 2026-10-07
python -m pesquisa.backtest_pipeline_proprio --dia5
```

| Etapa | Tempo | Disco |
|---|---|---|
| `construir_historico` (baixa CVM e B3 e monta 175 datas) | cerca de 5 horas; uma rodada interrompida retoma de onde parou | cerca de 2 GB de arquivos brutos e 64 MB de painéis |
| `backtest_pipeline_proprio` | alguns minutos | — |

O resultado sai em `artifacts/auditoria/<data>/backtest_pipeline_proprio.json`.
Sem a base Fintz em `dados/`, o script roda só a base própria.

A decisão do mês, com os dados do dia:

```bash
python -m carteira.pipeline_atual --somente-agenda
```

## Resultado → script

| Resultado | Script | Documento |
|---|---|---|
| Tabela do backtest | `pesquisa/backtest_pipeline_proprio.py --dia5` | [Auditoria de 15/10](docs/AUDITORIA_DADOS_2026-10-15.md) |
| Histórico point-in-time | `coleta/construir_historico.py` | [Auditoria de 09/10](docs/AUDITORIA_DADOS_2026-10-09.md) |
| Decisões mensais e fundo | `carteira/pipeline_atual.py`, `carteira/fundo_ficticio.py` | [`decisoes/`](decisoes/) |
| Erros corrigidos no pipeline | `fontes/`, `carteira/` | [Auditoria de 03/10](docs/AUDITORIA_DADOS_2026-10-03.md), [de 08/10](docs/AUDITORIA_DADOS_2026-10-08.md) |
| Regressão contra os fatores NEFIN | `pesquisa/regressao_nefin.py` | [Protocolo](docs/EXPERIMENTO_REGRESSAO_NEFIN.md), registrado antes do resultado |

## Código

| Pasta ou arquivo | O que faz |
|---|---|
| `fontes/` | Leitores da CVM (ITR, DFP, FRE, FCA, cadastro) e da B3 (capital social, proventos, eventos em ações) |
| `carteira/` | Seleção point-in-time, preços ajustados, decisão mensal, fundo fictício e relatório em PDF |
| `carteira/config/` | Definição congelada do modelo e metodologia de cada versão do pipeline |
| `coleta/` | Reconstrução do histórico nas datas de rebalanceamento |
| `pesquisa/` | Backtest na base própria e regressão contra os fatores NEFIN |
| `calculadora_factor.py`, `resultados_factor.py` | Motor de backtest do curso Código.py (Varos), publicado com autorização; a mudança feita nele está no cabeçalho do arquivo |
| `demo/` | Exemplo com dados sintéticos que confere a conta do índice |

## Limitações

- Sem custos de transação, impacto de mercado ou impostos.
- Bancos fora do universo, como no backtest original: a Fintz não publica
  EV/EBIT para eles.
- Antes de abr/2012 não há histórico próprio.
- Não é recomendação de investimento. Ver [`DISCLAIMER.md`](../DISCLAIMER.md).
