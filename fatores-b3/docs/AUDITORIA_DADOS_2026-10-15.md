# Auditoria de dados — base própria alongada até 2012 (15/10/2026)

O histórico próprio B3/CVM na regra do dia 5 começava em jun/2020. Agora vai
de abr/2012 a out/2026 (174 datas), sem depender da Fintz ou da Varos.

## O que faltava antes de 2020 e como foi resolvido

| Lacuna | Solução | Onde |
|---|---|---|
| O FCA só traz código de negociação desde 2018 | Ticker → CNPJ pelas cotações máxima e mínima de cada trimestre que o FRE informa (`volume_valor_mobiliario`), comparadas com o COTAHIST. Regra: até 2% de diferença em pelo menos 3 trimestres, vencedor 3 vezes à frente do segundo. Contra o FCA, acertou 289 de 290 tickers presentes nas duas fontes; o FCA tem prioridade | `fontes/tickers_fre.py` |
| O ITR só traz a composição do capital desde o 1T20 | Ações do capital social do FRE: FRE mais recente já recebido no corte; desdobramento, grupamento e bonificação corrigidos pela tabela de desdobramentos, decidindo pela quantidade guardada, não pela data da versão | `fundamentos.quantidade_acoes_fre` |
| Dados abertos de ITR começam em 2011 | Anos sem arquivo saem da lista (davam 404) | `construir_historico.PRIMEIRO_ANO_ITR` |
| Eventos do FRE antigos com quantidade zerada | Descartados (davam divisão por zero) | `eventos_cvm.candidatos_fre` |
| Nome de pregão de empresas que saíram antes de 2019 | COTAHIST lido desde 2010 | `construir_historico._nome_pregao` |

As regras novas só valem para datas anteriores a jun/2020; os painéis de 2020
em diante foram reaproveitados sem mudança.

**Erro corrigido no caminho:** a primeira versão desfazia um evento quando a
versão do FRE tinha sido recebida depois dele. Muitas empresas reenviam o FRE
sem atualizar o capital: a Tecnisa (TCSA3) saiu 10 vezes maior e a TOTVS
(TOTS3), 3 vezes menor. Os painéis dessa rodada estão em
`dados/historico_pipeline_dia5_pre2020_v1/` e foram refeitos. Teste:
`test_capital_do_fre_decide_desdobramento_pela_quantidade`.

## Cobertura

- Começa em abr/2012: de jun/2011 a jan/2012 não há EBIT de 12 meses (o
  primeiro TTM fecha com a DFP 2011); fev e mar/2012 têm menos de 30 empresas
  com dados.
- Jun/2012 ficou de fora: um prefixo ligado a dois CNPJs vigentes trava o
  pipeline por segurança. Em aberto.
- De 96 a 182 empresas válidas por data antes de 2020 (Fintz: 125 em média).
- Ficam de fora 5 a 7 papéis líquidos por data cujo FRE não informa cotações
  (AEDU3, GETI3/4, CTIP3...), e units sem preço das classes (TAEE11, ALUP11),
  que já ficavam de fora de 2020 em diante.

## Concordância com a Fintz

Proporção das ações em comum dentro da tolerância:

| Data | Empresas | EV/EBIT (5%) | ROIC (1 p.p.) | Momentum 12m (5 p.p.) | Valor de mercado (5%) |
|---|---:|---:|---:|---:|---:|
| 2013-06-05 | 126 | 77% | 96% | 94% | 94% |
| 2015-06-05 | 118 | 81% | 96% | 93% | 91% |
| 2016-12-05 | 116 | 66% | 92% | 94% | 89% |
| 2018-06-05 | 141 | 76% | 96% | 95% | 92% |
| 2019-12-05 | 161 | 76% | 92% | 90% | 86% |
| 2021-06-07 (ITR) | 175 | 76% | 95% | 91% | 92% |
| 2024-06-05 (ITR) | 204 | 82% | 93% | 92% | 89% |

Antes de 2020 a concordância é a mesma de depois, quando o capital vem do ITR.
Parte das divergências de valor de mercado é da Fintz (Sinqia e Ultrapar em
dez/2019 com quantidade de ações diferente da declarada à CVM).

**EV/EBIT, componente por componente** (`python -m fontes.conferir_ev_ebit`,
saída em `artifacts/auditoria/2026-10-15/ev_ebit_tres_bases.csv`). Das 216
divergências nas 7 datas, 148 envolvem o EBIT e 107 delas são de sinal: o nosso
EBIT é negativo e o EV/EBIT da Fintz é positivo. O próprio item EBIT da Fintz é
negativo em 93 de 94 desses casos e bate com o nosso (razão mediana 1,00): a
Fintz publica EV/EBIT positivo para quem tem prejuízo operacional (já visto em
03/10). Só com EBIT positivo nas duas bases, a concordância do EV/EBIT a 5% vai
para 86%, 88%, 86%, 89%, 81%, 87% e 90% nas mesmas datas, o nível medido em
03/10 (87%). Exceção semântica: até 2012 a Itaúsa consolidava o Itaú, e num
balanço com banco a conta 3.05 da CVM não é EBIT.

**A Varos não serve de desempate no histórico:** em 2016 e 2024, o EV/EBIT da
Varos é idêntico ao da Fintz (até 0,1%) em 98,8% das ações. A Varos herdou o
histórico da Fintz quando migrou de base; só os dados novos são dela.

## TRIO_ROIC na base longa

Regra do dia 5, mesmo motor e mesma grade nas duas bases
(`python -m pesquisa.backtest_pipeline_proprio --dia5`):

| abr/2012 a nov/2025 | TRIO | Universo |
|---|---:|---:|
| Base própria: retorno a.a. | 25,4% | 7,3% |
| Base própria: volatilidade | 23,8% | 22,8% |
| Base própria: queda máxima | −48,2% | −52,8% |
| Fintz: retorno a.a. | 25,7% | 3,5% |
| Fintz: queda máxima | −52,8% | −54,0% |
| Base própria até out/2026: retorno a.a. | 25,1% | 8,0% |

As carteiras das duas bases têm 70% dos nomes em comum, em média. A TRIO dá o
mesmo resultado em duas bases construídas de forma independente (a própria e a
Fintz; a Varos repete a Fintz no histórico): cerca de 25% a.a. de 2012 a 2025,
dentro da amostra.

## Correções de capital (20/10/2026)

Base inteira refeita (175 datas, abr/2012 a out/2026) com quatro correções em
`carteira/pipeline_atual.py`, que valem também para a carteira ao vivo:

| Problema | Correção | Uso na base |
|---|---|---|
| Duas séries de PN negociando (UNIP, CRPG, CEBR, CESP, USIM, CPLE): a CVM só informa o total de PN | Com preços a até 15% um do outro, todas as PN levam o preço da série mais líquida; acima disso, quarentena (CEBR5/6 a 17%) | Cerca de 340 empresas-data saíram da quarentena |
| ON da Azul vale 1/75 da PN pelo estatuto, e recebia o preço da PN | `PESO_ECONOMICO_ON` | 85 datas; valor de mercado 3,7 vezes menor |
| Unit com classes de negócio esparso | Preço da classe = razão classe/unit do último pregão em comum (até 252 pregões) vezes a unit | Só KROT; TAEE, ALUP, SULA, BRBI, RNEW não têm pregão das classes no COTAHIST e seguem em quarentena |
| Prefixo com dois CNPJs derrubava a data inteira (jun/2012) | Só o prefixo vai para a quarentena | 1 caso |
| Eventos de códigos que saíram antes de 2018 (TBLE, DROG, TLPP, HRTP) | Código atual achado pelo CNPJ do FRE | Sem eventos em 2012-2014: de 16 para 13 por data |

Válidas por data: +2 a +5. Quarentena de capital: de 5-12 para 2-11 por data.
Concordância com a Fintz (EV/EBIT só com EBIT positivo): 81% a 90%, sem piora.

| abr/2012 a nov/2025, regra do dia 5 | TRIO | Universo |
|---|---:|---:|
| Base própria corrigida | 28,0% a.a., queda −47,9% | 7,4% |
| Base própria antes da correção | 25,4% a.a., queda −48,2% | 7,3% |
| Fintz | 25,9% a.a., queda −52,8% | 3,5% |

O ganho vem de empresas que antes ficavam de fora por erro de capital e que o
modelo passou a escolher (UNIP6, CRPG5, USIM5 em 2021). Continua dentro da
amostra. Base anterior guardada em `dados/historico_pipeline_dia5_v2012_antes_units/`.
