"""Descobre quais proventos a Fintz deixou de aplicar, e corrige a serie.

O problema medido
-----------------
A serie `preco_fechamento_ajustado` da Fintz ajusta desdobramento e grupamento
corretamente -- conferido na MGLU3, que desdobrou em 2020 (fator 300) e agrupou
em 2024: nenhum salto artificial em nenhum dos dois. Mas dividendo ela nao
aplica em todos os papeis.

Consequencia: o momento -- o fator que mais contribui no modelo -- subestima o
retorno de parte dos pagadores, de forma INCONSISTENTE. Vies uniforme sairia no
ranking transversal; este nao sai, porque atinge uns papeis e outros nao.

Como saber, sem inferir
-----------------------
Tentamos primeiro deduzir pelo retorno na data-ex, sem preco bruto. O detector
acertava todos os nao ajustados mas errava um terco dos ajustados: com 2-3
eventos no ano, o ruido diario (2-3%) e do tamanho do proprio dividendo.
Detector de 64% de precisao nao serve para decidir nada -- foi descartado.

Com o COTAHIST em maos a pergunta vira aritmetica. O retroajuste multiplica o
passado por `f < 1` e deixa o presente intacto, entao a razao
`ajustado / bruto` da um degrau de exatamente `1/f` na data-ex de cada evento
aplicado -- e degrau nenhum nos que ficaram de fora. Nao ha limiar a escolher:

    WIZC3, seis eventos seguidos: salto 1,00000  -> nunca aplicado
    JHSF3, seis eventos seguidos: salto ~ 1/f    -> sempre aplicado

Limite de cobertura
-------------------
A API de eventos da B3 so lista empresa ATUALMENTE listada. Das 259 empresas do
universo, 61 nao foram encontradas -- AMBV, AMIL, ALSC, BIDI e outras
deslistadas. Justamente as que importam para nao ter vies de sobrevivencia.
Nesses papeis da para DETECTAR que falta ajuste, mas nao para corrigir.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

TOLERANCIA = 0.0015   # 0,15% -- folga para arredondamento do preco de fechamento
CLASSE = {'3': 'ON', '4': 'PN', '5': 'PN', '6': 'PN', '11': 'UNT'}


def eventos_do_ticker(proventos: pd.DataFrame, ticker: str) -> pd.DataFrame:
    """Eventos em dinheiro da classe correspondente ao sufixo do ticker."""

    ev = proventos[proventos['prefixo'].eq(ticker[:4])]
    classe = CLASSE.get(ticker[4:])
    if classe:
        ev = ev[ev['classe'].eq(classe)]
    ev = ev[ev['pct'].between(0, 50, inclusive='neither')]
    return ev.sort_values('ultimo_com')


def auditar(ajustado: pd.Series, bruto: pd.Series, eventos: pd.DataFrame) -> pd.DataFrame:
    """Evento a evento: o ajuste foi aplicado na serie da Fintz?

    Devolve uma linha por evento com o salto observado na razao
    `ajustado/bruto` e o veredito. Sem estatistica -- e comparacao direta.
    """

    razao = (ajustado / bruto.reindex(ajustado.index)).dropna()
    if len(razao) < 30:
        return pd.DataFrame()

    linhas = []
    for _, e in eventos.iterrows():
        i = razao.index.searchsorted(e['ultimo_com'], side='right')
        if i == 0 or i >= len(razao):
            continue
        salto = float(razao.iloc[i] / razao.iloc[i - 1])
        esperado = 1 / (1 - e['pct'] / 100)
        linhas.append({
            'ultimo_com': e['ultimo_com'], 'pct': e['pct'],
            'salto': salto, 'esperado': esperado,
            'aplicado': abs(salto - esperado) < abs(salto - 1.0),
            'conclusivo': min(abs(salto - esperado), abs(salto - 1.0)) < TOLERANCIA,
        })
    return pd.DataFrame(linhas)


def corrigir(ajustado: pd.Series, faltantes: pd.DataFrame) -> pd.Series:
    """Aplica na serie os proventos que a Fintz deixou passar.

    O preco mais recente nao muda; o passado desce, de modo que a variacao entre
    dois dias passe a ser retorno total. Um preco na data t leva o produto dos
    fatores de todo evento faltante cujo ultimo dia COM direito seja t ou
    posterior -- inclusive t, porque naquele dia o papel ainda embutia o
    provento que cairia no pregao seguinte.
    """

    if faltantes.empty:
        return ajustado.copy()

    fator = (faltantes.assign(f=1 - faltantes['pct'] / 100)
                      .groupby('ultimo_com', as_index=False)['f'].prod()
                      .sort_values('ultimo_com'))

    acumulado = pd.Series(1.0, index=ajustado.index)
    for _, linha in fator.iterrows():
        acumulado.loc[ajustado.index <= linha['ultimo_com']] *= linha['f']
    return ajustado * acumulado


def auditar_universo(painel_ajustado: pd.DataFrame, painel_bruto: pd.DataFrame,
                     proventos: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Roda a auditoria em todos os tickers. Devolve (resumo, eventos)."""

    resumo, todos = [], []
    for ticker in painel_ajustado.columns:
        aj = painel_ajustado[ticker].dropna()
        if ticker not in painel_bruto.columns or len(aj) < 120:
            continue
        ev = eventos_do_ticker(proventos, ticker)
        if ev.empty:
            continue

        aud = auditar(aj, painel_bruto[ticker].dropna(), ev)
        if aud.empty:
            continue
        aud['ticker'] = ticker
        todos.append(aud)

        conc = aud[aud['conclusivo']]
        resumo.append({
            'ticker': ticker,
            'eventos': len(aud),
            'conclusivos': len(conc),
            'aplicados': int(conc['aplicado'].sum()),
            'faltantes': int((~conc['aplicado']).sum()),
            'pct_faltante': float(conc.loc[~conc['aplicado'], 'pct'].sum()),
        })

    r = pd.DataFrame(resumo).set_index('ticker') if resumo else pd.DataFrame()
    e = pd.concat(todos, ignore_index=True) if todos else pd.DataFrame()
    return r, e
