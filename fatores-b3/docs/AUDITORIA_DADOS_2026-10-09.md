# Histórico próprio e backtest correto — 09/10/2026

Continuação de [08/10](AUDITORIA_DADOS_2026-10-08.md). Até aqui o pipeline B3/CVM
calculava só o dia da decisão. Agora ele reconstrói o histórico de jun/2020 a
out/2026 com o mesmo código da carteira ao vivo, e o modelo congelado foi
rodado nessa base e na base Fintz, com o mesmo motor e a mesma grade de datas.

## Resultado

| Base | Período | Modelo | Universo | Volatilidade | Drawdown |
|---|---|---:|---:|---:|---:|
| Pipeline próprio | jun/2020 a nov/2025 | 33,3% a.a. | 5,7% a.a. | 24,5% | -36,6% |
| Fintz | jun/2020 a nov/2025 | 21,9% a.a. | 1,9% a.a. | 23,6% | -32,8% |
| Pipeline próprio | jun/2020 a out/2026 | 32,0% a.a. | 8,0% a.a. | 25,3% | -36,6% |

Continua sendo resultado dentro da amostra: o modelo foi escolhido olhando
esse período. Cerca de 7 p.p. vêm de duas ações em recuperação judicial que
subiram muito em 2020–2021 (FHER3 e ETER3, que a Fintz também tinha).

## Por que a diferença para a Fintz é de 11 p.p.

| Origem | Efeito | Causa |
|---|---:|---|
| Universo | ~4,6 p.p. | 73 empresas do nosso universo não têm nenhum ROIC na Fintz de 2020 a 2025 |
| Preços | ~3 p.p. | Mesmas carteiras: 32,3% com os nossos preços, 29,0% com os da Fintz. SYNE3 (reduções de capital em 2024) é a maior parte |
| Outros valores dos fatores | ~3–4 p.p. | Proventos e bonificações que faltam no momentum da Fintz |

**As 73 empresas sem ROIC.** Das 73, 22 têm o histórico guardado sob outro
código da mesma empresa (a CCR inteira, de 2011 a 2025, está como MOTV3; a
Arezzo como AZZA3). As outras 51 — VALE3, SBSP3, UGPA3, RADL3, ELET3, CMIG4,
CPLE6, USIM5, entre outras — não têm nenhum item contábil nos arquivos, em
nenhum ano desde 2011. A Varos tem o mesmo buraco. Conferi que o erro não é
nosso:

- O EBIT que a Fintz usa por dentro (EV ÷ EV/EBIT) é igual ao nosso: SBSP3
  17,82 bi, UGPA3 5,45 bi, CMIG4 9,27 bi; VALE3 a 2,6%. Ela tem o dado, mas
  não o publica nos arquivos de itens e de ROIC.
- Nenhum ticker do arquivo tem esse EBIT sob outro nome (na PETR4, de
  controle, a busca por valor acha o ticker certo).
- Os balanços dessas empresas na CVM usam os mesmos nomes de conta da PETR4.
- O buraco aparece em dois downloads independentes (Fintz em 26/08, Varos em
  08/10).

O backtest original (28% a.a., 2011–2025) foi feito num universo sem essas
empresas. Não por escolha: o dado sumiu sem aviso.

**Conferência do nosso lado.** As 15 maiores altas da nossa carteira batem com
o Yahoo e com a Fintz (a alta de 160% da ATOM3 em 03/2021 foi real: sem evento,
mesma quantidade de ações). O valor de mercado histórico bate com o da Fintz
até 5% em 85% a 91% das empresas, de 2020 a 2025.

## O que precisei construir

| Problema | Solução | Onde |
|---|---|---|
| A B3 só publica a quantidade de ações atual | Quantidade do ITR/DFP da CVM (só existe desde 2020), com a unidade (ações ou milhares) achada pelo Formulário de Referência | `fontes/fundamentos.py`, `carteira/pipeline_atual.py` |
| A lista de eventos em ações da B3 é incompleta (KEPL3 3:1 em 05/2022, bonificações da ITSA e da CMIG) | Candidatos do FRE (até 2022) e da variação de quantidade no ITR, confirmados pelo número de distribuição do COTAHIST e pelo salto de preço. 212 eventos inferidos; acha 82% dos desdobramentos e grupamentos do gabarito Varos, mais as bonificações que ela não registra | `fontes/eventos_cvm.py` |
| A B3 não acha empresas deslistadas nem as que trocaram de código | Código atual pelo CNPJ (ELET → AXIA); para deslistadas, proventos pelo nome de pregão do COTAHIST. Sem eventos em ações, salto de 1,8x sem explicação anula o momentum | `coleta/construir_historico.py` |
| Empresas em recuperação judicial saíam do COTAHIST lido | Ver abaixo | `carteira/cotahist.py` |

Erros do pipeline encontrados e corrigidos nesta etapa:

| # | Erro | Efeito |
|---|---|---|
| 12 | Composição do capital com a mesma versão recebida duas vezes quebrava a leitura | Nenhum no ao vivo (a função não era usada) |
| 13 | Troca de exercício (duas datas-base no mesmo trimestre) abortava a data inteira | Datas de 2021–2022 não montavam |
| 14 | Escala da quantidade de ações recusava empresas depois de desdobramento e as que só declaram capital subscrito | KEPL3 sumia do painel por um ano; TIMS3, MRFG3 e DESK3 fora |
| 15 | Razões do detector de saltos em array de objetos quebravam o arredondamento | Data de 23/05/2025 não montava |

## Mudança de metodologia: recuperação judicial

A B3 troca o código BDI de 02 para 07 (recuperação extrajudicial) ou 08
(judicial) quando a empresa pede recuperação, e o leitor do COTAHIST só lia o
02. Isso tinha dois efeitos: a série de preço parava no pedido (uma posição
aberta ficaria congelada no último preço, escondendo a queda) e a empresa saía
do universo (ETER3, FHER3; BHIA3 desde 2024). Decidi incluir os dois códigos,
nos preços e no universo, como era no backtest da Fintz. Excluir quem já está
em recuperação não seria olhar o futuro, mas seria uma regra que o modelo
congelado não tinha.

Verifiquei o viés de preço congelado: de 55 ações que entraram em recuperação
entre 2020 e 2026, nenhuma estava na carteira nesse momento.

## Robustez (10/10/2026)

O resultado de 33,3% usa uma grade que começa em 01/06/2020 e gira a cada 21
pregões. Testei o quanto ele depende disso e de poucos acertos:

| Teste | Resultado (jun/2020 a nov/2025) |
|---|---|
| Regra da carteira ao vivo: primeiro pregão a partir do dia 5 | 29,9% a.a. (28,5% até out/2026); Fintz 23,5%; universo 5,6% |
| Dia de início da grade, 21 variantes, na base Fintz diária | 20,6% a 27,5% a.a., mediana 24,1%; a grade usada deu 21,9% |
| Sem FHER3 e ETER3 | 25,4% a.a. |
| Sem as 5 maiores contribuições (ATOM3, ETER3, FHER3, PETR4, TASA4) | 21,1% a.a. |

A referência que adoto é a regra do dia 5: cerca de 30% a.a. de 2020 a 2025,
com faixa de 21% a 30% conforme o calendário e a concentração. Em todas as
versões o modelo fica bem acima do universo, mas o número continua dentro da
amostra. Não há custo de corretagem na carteira fictícia, então não testei
custos.

Para reproduzir a regra do dia 5:
`python -m coleta.construir_historico --dia5` e
`.venv/Scripts/python.exe -m pesquisa.backtest_pipeline_proprio --dia5`.
Os resultados estão em `artifacts/auditoria/2026-10-10/`.

## Em aberto

- **GOLL4** no Chapter 11 aparece com código 58, que também tem fundos
  imobiliários; fica fora nesse período.
- **Proventos repetidos na B3** (auditoria de 08/10): a regra ainda descarta as
  repetições.
- **Fundamentos de 2026**: a conferência com os releases
  ([planilha](artifacts/auditoria/2026-10-08/conferencia_releases_2026-10.xlsx))
  continua pendente.
- **Histórico de 2011 a 2019**: precisa de outra fonte para a quantidade de
  ações (FRE anual) e para ligar ticker a CNPJ (o FCA só traz ticker desde
  2018).

## Como reproduzir

Na pasta `fatores-b3`:

```powershell
python -m coleta.construir_historico --inicio 2020-06-01 --fim 2026-10-07
.venv/Scripts/python.exe -m pesquisa.backtest_pipeline_proprio
```

A construção leva cerca de 4 horas e retoma de onde parou;
`--so-precos` refaz só preço e momentum. Os resultados estão em
`artifacts/auditoria/2026-10-09/`: o backtest, os 212 eventos inferidos, as 73
empresas sem ROIC na Fintz e o manifesto da construção.

## Decisões

Decidi construir o histórico próprio em vez de usar
o da Varos, incluir as empresas em recuperação judicial e exigir que cada
diferença para a Fintz fosse explicada antes de aceitar o resultado.
