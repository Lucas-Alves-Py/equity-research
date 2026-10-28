# Auditoria dos dados: Varos, Fintz e Yahoo — 08/10/2026

> Continuação em [AUDITORIA_DADOS_2026-10-09.md](AUDITORIA_DADOS_2026-10-09.md):
> histórico próprio de 2020 a 2026 e backtest do modelo congelado.

Continuação da [auditoria de 03/10/2026](AUDITORIA_DADOS_2026-10-03.md). Lá o
único gabarito era a base Fintz local, que termina em 03/11/2025. Aqui comparei
o pipeline B3/CVM com três fontes externas, inclusive em datas de 2026.

## Por que fiz esta auditoria

Recebi uma chave nova da Varos. A Fintz continua respondendo 404 em todas as
rotas, e a Varos passou a servir os dados de uma base própria, em
`base-de-dados-api.varos.com.br`. A chave antiga dá 401 ("chave expirada"), e a
nova também vai expirar. A decisão é manter o pipeline próprio como fonte e usar
as bases externas apenas como gabarito.

## As três bases

| Base | O que comparei | Conclusão |
|---|---|---|
| Varos | Preço bruto, volume, momentum, EBIT, ROIC, EV, EV/EBIT e valor de mercado em 03/11/2025 e 07/10/2026 | Só preço bruto e volume servem de gabarito |
| Fintz | Momentum de 12 meses contra o Yahoo em 03/11/2025 | É a referência do backtest, não a verdade; ajusta pior que o pipeline |
| Yahoo | Preço ajustado e momentum de 12 meses | Bom gabarito de preço ajustado e não expira; falha em bonificação de preferencial resgatável |

### Varos

A Varos não continua a série da Fintz: ela recalculou o histórico. Em
03/11/2025, os valores dela iguais aos da Fintz são 97,7% no EBIT, mas só 47,8%
no ROIC, 38,4% no EV, 42,1% no EV/EBIT e 41,4% no valor de mercado.

| Dado | Pipeline × Varos em 03/11/2025 | Pipeline × Varos em 07/10/2026 | Causa |
|---|---|---|---|
| Preço bruto | — | 100% idêntico (170 tickers) | — |
| Volume mediano 21d | — | 91,2% até 1% | — |
| EBIT TTM | 90,0% até 1% | 4,8% até 1% | Série parada entre out/2025 e abr/2026; em GGBR4, EQTL3 e WEGE3 o valor é 2T25 + 3T25 + 1T26 + 2T26, sem o 4T25 |
| ROIC | mediana de 6,14 p.p. | mediana de 6,52 p.p. | Fórmula diferente da Fintz |
| EV | 35,9% até 5% | 30,7% até 5% | Definição diferente |
| Momentum 12m | — | mediana de 7,4 p.p. | `fator_ajuste` parado em dez/2025: sem proventos e sem desdobramentos em 2026 (SBSP3 aparece com -74%) |

Os proventos da Varos também param em dez/2025 em todos os tickers que testei.

### Momentum contra o Yahoo

| Base | Data | Tickers | Erro mediano | Até 0,5 p.p. | Até 1 p.p. | Até 5 p.p. |
|---|---|---:|---:|---:|---:|---:|
| Fintz | 03/11/2025 | 190 | 1,51 p.p. | 22,6% | 38,9% | 84,7% |
| Pipeline (painel salvo, com os bugs abaixo) | 07/10/2026 | 174 | 0,25 p.p. | 59,8% | 74,7% | 91,4% |
| Pipeline corrigido, JCP a 85% | 07/10/2026 | 174 | 0,22 p.p. | 60,9% | 75,9% | 92,5% |
| Pipeline corrigido, JCP a 100% | 07/10/2026 | 174 | 0,00 p.p. | 82,2% | 82,8% | 92,5% |

O JCP a 85% explica a diferença pequena e sistemática contra o Yahoo, que usa o
valor bruto. Mantenho os 85% porque é a convenção do backtest.

Na Fintz, LIGT3, SYNE3, VULC3 e TAEE3/4/11 têm momentum igual ao retorno bruto
(nenhum ajuste), e a VIVT3 aparece com -37,9% contra +28% no Yahoo
(desdobramento não ajustado).

O Yahoo erra em AXIA3 e CYRE3. As duas deram bonificação em ações
preferenciais resgatáveis em dez/2025 (`assetIssued` BRAXIAACNPC9 e
BRCYREACNPR4). A ON caiu 22,5% e 19,1% no dia ex, e a classe nova negociou
praticamente ao preço da ON (AXIA7 a R$ 48,20 contra AXIA3 a R$ 48,65). O
pipeline trata o evento como bonificação e capta esse valor; o Yahoo registra
só a queda.

## Erros que encontrei no meu pipeline e corrigi

| # | Erro | Efeito | Commit |
|---|---|---|---|
| 10 | A emenda de troca de ticker não reordenava a série por data | Quando o código antigo vem depois do novo, a janela de 12 meses pegava pregões fora de ordem: AXIA3 com 94,3% em vez de 48,8% e EMBJ3 com 29,5% em vez de 19,5% | `1710653` |
| 11 | Os finais 5 e 6 procuravam a classe `PN`, mas a B3 publica `PNA` e `PNB` | A UNIP6 ficava sem proventos (R$ 6,48 em dez/2025) | `1710653` |

A numeração continua a da auditoria de 03/10. `fontes/ajuste.py` tem o mesmo
mapeamento de classe, mas é usado só na auditoria histórica e não foi alterado.

**Efeito na carteira.** Recalculei a decisão de 06/10/2026 com as mesmas fontes
fixadas: COTAHIST, snapshots de eventos e capital. Com o código original, ela se
reproduz exatamente. Com o código corrigido, a carteira é a mesma (mesmas 10
ações, na mesma ordem); o `rank_final` melhora um ponto em CSED3, PETR4, RIAA3,
SEER3, VBBR3 e VTRU3, porque AXIA3 e EMBJ3 caem no ranking de momentum.

## Em aberto

**Proventos repetidos na B3.** A CompanyCall às vezes devolve linhas idênticas
em todos os campos. Desde 2024, isso acontece em 83 de 1.198 eventos, em 26
tickers. Hoje o pipeline descarta as repetições (`drop_duplicates` em
`b3.proventos`), por causa de uma VLID3 que veio quatro vezes. A queda do preço
no dia ex mostra que às vezes a repetição é real: na MDNE3, seis parcelas iguais
de R$ 0,59 mais uma de R$ 0,61 dão 15,2%, e a ação caiu 16,2%. Em outras vezes,
uma linha só explica melhor (KLBN, SHUL4, CLSC4 em dez/2024). No total, a soma
fica mais perto da queda em 58% dos casos, o que não basta para escolher uma
regra. O Yahoo não desempata: ele soma as mesmas linhas da B3 (SEER3 exatamente
2×, VLID3 exatamente 4×). Com a regra de somar, a carteira de 06/10 seria a
mesma; os maiores efeitos no momentum seriam MDNE3 (+14,1 p.p.), FIQE3, CLSC4 e
SEER3 (+5,3 p.p.). A fonte que resolve é o aviso aos acionistas de cada empresa.

**Provento fora da B3.** O Yahoo registra um dividendo de 11,4% da ALLD3 com ex
em 06/10/2026, e a ação caiu 8,7% nesse dia. A B3 não tinha o evento no
snapshot de 07/10 e continua sem ele em 08/10.

**Momentum ainda sem explicação.** MILS3: a B3 tem uma linha de 4,55%, o Yahoo
9,62%, e a queda no dia ex foi de 7,3%. GRND3: a B3 dá 15,3%, o Yahoo 19,2%, e a
queda foi de 20,1%.

**Fundamentos de 2026 sem gabarito externo.** A Fintz termina em 2025, e a Varos
está errada justamente em EBIT, ROIC e EV. Para fechar isso, a planilha
[conferencia_releases_2026-10.xlsx](artifacts/auditoria/2026-10-08/conferencia_releases_2026-10.xlsx)
traz o EBIT trimestral, o EBIT TTM e a dívida líquida das 10 ações da carteira,
para conferir com os releases. Dois casos têm prioridade: RIAA3 (4T25 de
R$ 1,85 bi, contra R$ 70 a 256 mi nos outros trimestres, e é ele que leva o
EV/EBIT a 2,4) e JHSF3 (4T25 de R$ 1,13 bi).

## Gabarito congelado da Varos

Antes de a chave expirar, guardei em `dados/gabaritos/varos_2026-10-08/` (fora
do git, dados licenciados) as cotações, 28 itens contábeis point-in-time, 36
indicadores point-in-time, IBOV, CDI e os proventos por ticker. O manifesto, com
endpoint, hash, linhas e cobertura de cada arquivo, está em
[varos_manifesto.json](artifacts/auditoria/2026-10-08/varos_manifesto.json). O
script é [coleta/capturar_gabarito_varos.py](coleta/capturar_gabarito_varos.py).

São 68 arquivos e 455 MB. Os proventos cobrem, na prática, só de set/2024 a
dez/2025 (1.227 eventos em 291 tickers); 81 tickers devolveram erro 500 da API.

Como gabarito, os arquivos servem para preço bruto e volume até 07/10/2026,
para EBIT até 2025 e para proventos de set/2024 a dez/2025. O resto fica
registrado como divergência do fornecedor.

## Como confiro daqui para frente

1. Regras internas sobre as fontes primárias, rodando sempre: trimestres que
   somam o anual da DFP, quantidade de ações igual na B3 e na CVM, e salto de
   preço no COTAHIST correspondendo a evento declarado na B3.
2. Momentum contra o Yahoo, sabendo onde ele erra (bonificação de preferencial
   resgatável) e que ele não é independente da B3 nos proventos.
3. A cada trimestre, as ações da carteira contra os releases das empresas.

## Arquivos

Os resultados estão em `artifacts/auditoria/2026-10-08/`:

- `pipeline_vs_varos_{ebit,roic,ev}_{2025-11-03,2026-10-07}.csv` — saídas de
  `fontes/reconciliar_fintz_*` com a Varos no lugar da Fintz;
- `pipeline_vs_varos_momentum_2026-10-07.csv`;
- `pipeline_vs_yahoo_momentum_2026-10-07.csv` e
  `pipeline_vs_yahoo_jcp85_jcp100_2026-10-07.csv`;
- `fintz_vs_yahoo_momentum_2025-11-03.csv`;
- `b3_proventos_repetidos_2024-2026.csv` — cada evento com a quantidade de
  repetições, o percentual com e sem a soma, e o retorno no dia ex;
- `recalculo_decisao_2026-10-06.csv` — carteira com o código original, com o
  corrigido e com as duas regras de proventos repetidos.

As comparações foram feitas em análises interativas.

## Decisões

Decidi manter o pipeline B3/CVM como fonte e usar
as bases externas só como gabarito, porque o acesso à Varos vai expirar. A
pergunta que levou aos dois bugs foi minha: se o nosso preço ajustado estava
mesmo correto.
