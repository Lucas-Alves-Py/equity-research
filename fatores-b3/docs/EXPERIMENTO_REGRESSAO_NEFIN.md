# Experimento: o retorno do modelo contra os fatores do NEFIN

Protocolo registrado em 14/10/2026, antes de baixar os fatores ou calcular
qualquer resultado.

## Pergunta

O modelo congelado (TRIO_ROIC) rende cerca de 30% a.a. no histórico próprio
de 2020 a 2025, contra 5,6% do universo. Quanto desse retorno é exposição a
fatores já conhecidos na B3 (mercado, tamanho, valor, momentum e liquidez) e
quanto sobra como intercepto (alfa)?

Se o alfa sumir, o modelo continua podendo ser útil, mas a tese passa de
"achei algo novo" para "combino bem fatores conhecidos".

## Hipóteses, escritas antes do resultado

- **WML positivo e o maior coeficiente entre os fatores.** O leave-one-out
  registrado em `carteira/config/modelos.yaml` atribui ao momentum +7,7 p.p.
  do retorno.
- **HML positivo.** EV/EBIT e book-to-market medem valor por caminhos
  diferentes, então a sobreposição é parcial.
- **SMB positivo.** Seis das dez ações da decisão de 06/10/2026 estavam abaixo
  do valor de mercado mediano do universo.
- **IML positivo, mas fraco.** O filtro de R$ 500 mil por dia limita a
  exposição a ações ilíquidas.
- Sem expectativa para o beta de mercado.

Se o WML não sair positivo, isso contradiz o leave-one-out, e o experimento
para para investigar a construção das séries antes de qualquer leitura.

## Dados

- **Modelo e universo:** séries diárias de patrimônio do
  `pesquisa/backtest_pipeline_proprio.py --dia5`, base do pipeline próprio,
  10 ações, pesos iguais, liquidez acima de R$ 500 mil, sem custos. O
  universo é a carteira de pesos iguais com todas as ações que passam no
  filtro, calculada pelo mesmo motor.
- **Fatores:** `nefin_factors.csv` do NEFIN-USP (Rm−Rf, SMB, HML, WML, IML e
  Risk_Free, diários), salvo em `dados/nefin/`. No artefato ficam registrados
  a data do download, o SHA-256 do arquivo e a última data da série.
  Na verificação de 14/10/2026 a série ia de 02/01/2001 a 03/07/2026.
- **Período:** 05/06/2020 até a última data em comum entre as duas séries
  (hoje, 03/07/2026), cerca de 73 meses.
- **Taxa livre de risco:** a `Risk_Free` do próprio NEFIN, para que o
  excesso de retorno do modelo e o fator de mercado usem a mesma taxa.

Citação: NEFIN (2026). Risk Factors. https://nefin.com.br/data/risk-factors/

## Especificação principal

Retornos mensais, por mês calendário, obtidos compondo os retornos diários de
cada série dentro do mês (inclusive os fatores e a taxa livre de risco):

```text
R_modelo − Rf = α + β_m·(Rm−Rf) + β_s·SMB + β_h·HML + β_w·WML + β_i·IML + ε
```

- Mínimos quadrados ordinários, com erro-padrão de Newey-West (3 defasagens).
- Mensal por causa das small caps: no diário, preço que não negociou no dia
  atrasa a correlação com o mercado e puxa os betas para baixo.
- Reportar: α anualizado (α mensal × 12), estatística t, os cinco betas com
  t, R² ajustado e número de meses.
- Reportar também o **alfa mínimo detectável** (2 × erro-padrão do α,
  anualizado). Com seis anos de dados, um alfa real de vários pontos pode não
  ser distinguível de zero, e esse número mostra o tamanho dessa limitação.

## Descritivos e robustez, definidos agora

Não mudam a leitura principal. Servem para mostrar de onde vem o resultado.

1. **CAPM** (só Rm−Rf) e **Carhart** (Rm−Rf, SMB, HML, WML): mostram quanto
   do alfa cada bloco de fatores absorve.
2. **Universo como controle:** a mesma regressão de cinco fatores com o
   universo como variável dependente. O filtro de liquidez já pode carregar
   SMB ou IML, e o que interessa é o alfa do modelo acima do alfa do universo.
3. **Diário:** a mesma especificação com retornos diários, uma defasagem de
   cada fator somada ao coeficiente contemporâneo (Dimson) e Newey-West com
   10 defasagens.
4. **Período de referência:** até 04/11/2025, a janela em que foi medida a
   referência de ~30% a.a. na auditoria de 09/10/2026.

## Critério de leitura

Sobre a especificação principal:

- **α > 0 com t ≥ 2:** sobra retorno que os cinco fatores do NEFIN não
  explicam. Isso não prova habilidade: pode ser o prêmio de qualidade (ver
  limites), sorte dentro da amostra ou o efeito de ter testado vários modelos
  na mesma base.
- **α > 0 com t < 2:** não dá para distinguir de zero. A leitura vem
  acompanhada do alfa mínimo detectável.
- **α ≤ 0:** o retorno do período é explicado pela exposição aos fatores
  conhecidos.

## O que este experimento não faz

- Não altera o modelo congelado nem a carteira fictícia, qualquer que seja o
  resultado.
- Não é validação fora da amostra: usa o mesmo período em que o modelo foi
  medido.
- Não testa custos. Os fatores do NEFIN também são carteiras teóricas
  long-short, sem custo.

## Limites conhecidos antes de rodar

- **O NEFIN não publica fator de rentabilidade ou qualidade** (o RMW do
  Fama-French de cinco fatores). O modelo usa ROIC, então parte do alfa pode
  ser só prêmio de qualidade. "Alfa" aqui quer dizer alfa além de mercado,
  tamanho, valor, momentum e liquidez.
- O universo e os filtros do NEFIN não são os do pipeline. Os fatores são
  medidos sobre a base deles, não sobre as ações que o modelo pode comprar.
- O HML do NEFIN usa book-to-market; o modelo usa EV/EBIT.
- Faltam os meses de julho a outubro de 2026 até a próxima atualização do
  NEFIN.

## Entregas

- Script: `pesquisa/regressao_nefin.py`.
- Saídas: `artifacts/regressao-nefin/` (coeficientes em JSON e a tabela da
  especificação principal e dos descritivos).
- Resultados acrescentados a este arquivo, numa seção datada, sem editar o
  protocolo acima.

Reprodução (na pasta `fatores-b3`):

```powershell
python -m coleta.construir_historico --dia5
.venv/Scripts/python.exe -m pesquisa.regressao_nefin
```
