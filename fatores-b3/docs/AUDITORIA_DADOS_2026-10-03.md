# Auditoria dos dados contra a Fintz — 03/10/2026

> Continuação em [AUDITORIA_DADOS_2026-10-08.md](AUDITORIA_DADOS_2026-10-08.md):
> comparação com Varos e Yahoo em 2026 e mais dois erros corrigidos.

## Por que fiz esta auditoria

O backtest da estratégia foi feito com dados da Fintz. A carteira que acompanho
agora é calculada a partir de fontes públicas: cotações e eventos da B3,
demonstrações da CVM. Antes de confiar no acompanhamento prospectivo, eu
precisava saber se as duas bases produzem os mesmos números.

A API da Fintz não responde mais: todas as rotas retornam 404, inclusive o
exemplo da própria documentação com a chave pública de testes. O único
gabarito disponível é a base local, que termina em 03/11/2025. Por isso
comparei o **código atual** nessa data. O código que processa os dados novos
é o mesmo; o que não consigo conferir externamente são os valores posteriores
a novembro de 2025.

Universo comparado: ações com volume mediano acima de R$ 500 mil em
03/11/2025, o mesmo filtro de liquidez da estratégia (209 tickers).

## Resultado por dado, depois das correções

| Dado | Comparáveis | Concordância com a Fintz | Avaliação |
|---|---:|---|---|
| EBIT TTM | 165 | 89,7% até 1%; 93,9% até 5% | Confiável; resíduo classificado abaixo |
| EV/EBIT | 180 | 87,2% até 5% | Dívida líquida idêntica; diferença vem de EBIT negativo |
| ROIC | 281 | Mediana 0,00 p.p.; 95,4% até 5 p.p. | Confiável; repetido em 01/11/2024 com 94,1% |
| Patrimônio líquido | 151 | 96,7% até 1%; 98,7% até 5% | Confiável |
| Volume mediano | 243 | 99,6% até 1%; 205 de 209 líquidas em comum | Confiável |
| Momentum 12 meses | 180 | Mediana 0,02 p.p.; 80,6% até 0,5 p.p.; 85,6% até 5 p.p. | Confiável, com diferenças de eventos societários |
| Quantidade de ações | 151 | 139 consistentes entre B3 e CVM (92%) | B3 confiável; o arquivo de capital da CVM tem erros de unidade |
| Valor de mercado | 181 | 75% até 5% após ajustar desdobramentos | Não validável em 2025 com o capital de hoje; ver abaixo |

## Erros que encontrei no meu pipeline e corrigi

| # | Erro | Efeito | Commit |
|---|---|---|---|
| 1 | Consolidado entregue todo zerado (TIMS3 em 2024, GEPA3/4) era preferido ao individual | EBIT da TIMS3 de R$ 375 mi em vez de R$ 6,7 bi no sinal de 25/09 | `0f0afd6` |
| 2 | Exercício fora de dezembro (SMTO3, JALL3, AGRO3, CAML3) sem 4º trimestre fiscal | Essas empresas saíam do universo em silêncio | `1c74e0e` |
| 3 | A reconciliação somava quatro trimestres mesmo com lacuna | A auditoria media uma conta diferente da do seletor | `1c74e0e` |
| 4 | ROIC por alíquota efetiva, diferente da fórmula do backtest | 54 ações líquidas fora do universo; 3 das 10 ações do sinal de 02/10 mudam | `1c74e0e` |
| 5 | Bancos no universo ao vivo, ausentes no backtest | Números sem sentido para bancos (3.05 não é EBIT) | `8416df8` |
| 6 | Capital aprovado no dia do sinal abortava a seleção inteira | A RCSL3, sem liquidez, bloqueava a decisão de 02/10 | `8416df8` |
| 7 | Nome de pregão com barra ("KLABIN S/A") devolvia zero proventos | Momentum de ABEV3 e CURY3 cerca de 8 p.p. abaixo; dividendos da Klabin ignorados | este commit |
| 8 | Filtro de ticker sem dígito no prefixo | B3SA3 fora do universo | este commit |
| 9 | Troca de ticker zerava o histórico do momentum | Axia, Embraer e Riachuelo fora do universo | este commit |

Cada correção tem teste. Para o item 2, comparei o código anterior e o novo
sobre os mesmos arquivos da CVM: nenhum valor existente mudou e entraram 27 a
29 trimestres de 10 emissores com exercício fora de dezembro.

A fórmula de ROIC da Fintz não é documentada. Encontrei-a testando hipóteses:
`(EBIT TTM + IR/CSLL da DRE) / (patrimônio líquido + dívida bruta)` tem erro
mediano de 0,00 p.p. em duas datas independentes.

## Divergências em que a Fintz está errada ou usa outra convenção

- **EV/EBIT com EBIT negativo:** 165 empresas tinham EBIT negativo na Fintz e
  só 19 tinham EV/EBIT negativo. O valor publicado não corresponde a EV/EBIT TTM
  (GOLL54 implicaria EBIT de R$ 16,5 bi). No backtest isso afetou 0,5% das
  posições. Mantenho EBIT negativo no universo, como o modelo congelado previa;
  hoje a mais bem colocada está na 31ª posição.
- **P/VP:** o valor de mercado dividido pelo P/VP da Fintz não bate com o
  patrimônio líquido da própria Fintz (VBBR3 dá a metade). O nosso PL bate com o
  item contábil dela. Isso afeta o [experimento das financeiras](EXPERIMENTO_FINANCEIRAS.md),
  cujo backtest usou o P/VP da Fintz.
- **JBSS3:** a Fintz manteve dados depois que a ação deixou de ser negociada na
  B3, em 06/06/2025. O pipeline ao vivo já a exclui.
- **Valor de mercado de UGPA3, VIVT3, SUZB3 e CSAN3:** a B3 e a CVM informam
  exatamente a mesma quantidade de ações; quem diverge é a Fintz.
- **Momentum de VIVT3 e IFCM3:** desdobramento ou grupamento não ajustado pela
  Fintz. Em TIMS3, TAEE3/4 e CPLE3, proventos aprovados na B3 faltam na Fintz.

## Divergências ainda sem explicação

- **EBIT:** IRBR3, BBSE3, CXSE3 e ODPV3 (seguradoras e holdings; provável
  diferença de definição), AMOB3, HBSA3, PGMN3, OIBR3 e HYPE3 (o nosso já
  inclui o 3º trimestre de 2025; provável diferença de data).
- **Dívida líquida:** TOKY3, PFRM3, HYPE3 e ABEV3 (caixa ou aplicações
  financeiras classificadas de outro jeito).
- **Momentum:** SYNE3 (provável redução de capital com devolução de caixa) e
  CPLE6. ABEV3 e CURY3 estavam aqui; a causa era a barra no nome de pregão,
  já corrigida. TAEE3/4/11 e KLBN4 divergem por proventos que faltam na Fintz.
- **Teste instável:** a suíte falhou 2 vezes em cerca de 35 execuções e não
  consegui reproduzir. Fica em aberto.

## Composição do universo ao vivo

No começo da auditoria, das 199 ações líquidas do sinal de 02/10/2026, só 101
entravam no universo. Corrigi as causas e o universo passou a ter **174
ações**:

| Correção | O que resolve | Empresas recuperadas |
|---|---|---|
| ROIC na fórmula da Fintz | 54 ações descartadas por alíquota efetiva fora de 0–100% | HYPE3, ENGI11, ALOS3, HAPV3 e outras |
| Emenda de histórico na troca de ticker (mesmo CNPJ, mesma classe, pregões consecutivos e sem salto de preço) | Momentum zerado pela troca de código | AXIA3 (ex-ELET3), EMBJ3 (ex-EMBR3), RIAA3 (ex-GUAR3), ECOM3 (ex-TRAD3), SAUD3 (ex-ODPV3) |
| Ticker ligado ao CNPJ pela B3 quando o FCA da CVM falha, e filtro que aceita dígito no prefixo | FCA com erro das empresas (CSN declarada como "4030", C&A e Raízen com fim de negociação falso, MBRF e CSN Mineração sem código) e a B3SA3 recusada por um erro meu | B3SA3, CSNA3, CMIN3, CEAB3, MBRF3, RAIZ4, KLBN3, KLBN4 |
| Classe sem preço no dia | Golden share vale zero; classe que não negociou no dia usa o último negócio dos 21 pregões; classe que não negocia na bolsa usa o preço da outra classe | VALE3, CPLE3, DEXP3, SHUL4, TFCO4 |
| Eventos de unit pelo ISIN de certificado (CDA) | Units sem ajuste de proventos | KLBN11, TAEE11, SAPR11, ALUP11, ENGI11, IGTI11 |
| Consulta de proventos sem a barra do nome ("KLABIN S/A") | A B3 devolvia zero proventos e o momentum ignorava os dividendos | ABEV3, CURY3, Klabin |

Ficam fora, com motivo:

| Motivo | Tickers |
|---|---|
| Bancos (setor da CVM), como no backtest | ITUB4, BBAS3, BBDC4, SANB11, BPAC11 e outros |
| Menos de 12 meses de negociação | AUAU3 (holding nova da fusão Petz + Cobasi, outro CNPJ), PASS3, e as classes novas AXIA7, CYRE4 e RENT4, cujas empresas estão no universo por outro ticker |
| Azul | Troca de classe depois de quase um ano sem negociar, na reestruturação |
| OPCT3 | Cisão com redução de capital; o ajuste exige o valor das ações recebidas |
| BRBI11 | Só a unit negocia; sem a composição da unit não há valor de mercado |
| ETFs | IBOV11, OBTC3 |

Cada decisão registra os tickers ligados pela B3, as trocas emendadas e a
regra de preço usada em cada empresa.

Com as correções, refiz o confronto do momentum com a Fintz em 03/11/2025:
188 ações comparáveis (eram 180), mediana de 0,02 p.p. e 86,7% até 5 p.p.
ABEV3, CURY3 e as units KLBN11, SAPR11, ALUP11, ENGI11, IGTI11 e BPAC11 batem
com diferença de até 0,02 p.p.

## Efeito na carteira

| Sinal | Regra | Universo | Carteira |
|---|---|---:|---|
| 25/09/2026 | v2 | 109 | JHSF3, SEER3, PETR4, LEVE3, TEND3, VBBR3, WIZC3, UGPA3, MLAS3, MILS3 |
| 02/10/2026 | v3 com todas as correções | 174 | JHSF3, SEER3, PETR4, LEVE3, RIAA3, VTRU3, CSED3, EUCA4, VBBR3, UGPA3 |

A decisão de 05/10/2026 roda com este código e com os preços daquele dia.

## Posso confiar nos dados?

Nos fatores, sim, com as ressalvas acima: EBIT, ROIC, patrimônio, liquidez e
momentum batem com o gabarito na grande maioria das ações, e as diferenças
restantes estão classificadas. No universo, sim: das 199 ações
líquidas, 174 entram, e as que ficam fora têm motivo documentado. Para valores posteriores a novembro de 2025 não existe gabarito
externo; o que garanto é que o código foi validado na última data em que havia
um.

## Como reproduzir

Na pasta `fatores-b3`, com a base Fintz local em `dados/`:

```powershell
.venv/Scripts/python.exe -m fontes.reconciliar_fintz_ebit --as-of 2025-11-03 --anos 2023,2024,2025
.venv/Scripts/python.exe -m fontes.reconciliar_fintz_ev --as-of 2025-11-03
.venv/Scripts/python.exe -m fontes.reconciliar_fintz_roic --as-of 2025-11-03 --anos 2023,2024,2025
.venv/Scripts/python.exe -m fontes.reconciliar_fintz_roic --as-of 2024-11-01 --anos 2022,2023,2024
```

As saídas ficam em `artifacts/reconciliacao/`. As comparações de momentum,
valor de mercado e composição do universo foram feitas em análises
interativas; os resultados estão em `artifacts/auditoria/2026-10-03/`.

## Decisões

Exigi o confronto com a Fintz antes de seguir com novas funcionalidades,
escolhi alinhar o pipeline ao backtest em vez de criar regras novas e decidi
medir as financeiras por P/VP em vez de excluí-las do estudo. Empresas com EBIT negativo seguem no universo porque o
modelo congelado as inclui.
