"""NAV e rebalanceamentos nocionais do fundo de treino mensal.

Cada aporte compra cotas do fundo no fechamento D+1 observado. O indice mede
apenas retorno; o patrimonio incorpora aportes. Entre giros, as posicoes sao
marcadas por precos ajustados para retorno total. No giro, as ordens nocionais
sao expressas em reais e unidades teoricas fracionarias ao preco bruto B3.
Isso NAO e um extrato de corretora nem contabilidade de dividendos em caixa.
"""

from __future__ import annotations

import hashlib
import json
import math

import pandas as pd


def _data(serie: pd.Series) -> pd.Series:
    datas = pd.to_datetime(serie, errors='coerce')
    if datas.isna().any():
        raise ValueError('data ausente ou invalida')
    return datas.dt.date


def simular_fundo_mensal(alvos: pd.DataFrame, precos: pd.DataFrame,
                         agenda: pd.DataFrame, base: float = 100.0) -> dict:
    """Calcula NAV e operacoes teoricas sem gravar ledger.

    `alvos`: data_decisao, data_execucao, ticker, peso (dez pesos somam 1).
    `precos`: data, ticker, preco_bruto, preco_fechamento_ajustado para cada
    sessao entre primeira execucao e ultimo dia observado. Falta em uma
    posicao ativa bloqueia o calculo, nunca e preenchida com preco antigo.
    `agenda`: data_decisao, data_execucao, aporte_ficticio. Uma decisao sem
    D+1 observado pode constar com data_execucao vazia, mas nao vira cota.
    """
    if not math.isfinite(float(base)) or base <= 0:
        raise ValueError('base do indice invalida')
    requisitos = [(alvos, {'data_decisao', 'data_execucao', 'ticker', 'peso'}, 'alvos'),
                  (precos, {'data', 'ticker', 'preco_bruto',
                            'preco_fechamento_ajustado'}, 'precos'),
                  (agenda, {'data_decisao', 'data_execucao', 'aporte_ficticio'}, 'agenda')]
    for tabela, colunas, nome in requisitos:
        if tabela is None or colunas - set(tabela.columns):
            raise ValueError(f'{nome}: colunas obrigatorias ausentes')
    a = alvos.copy()
    g = agenda.copy()
    p = precos.copy()
    if a.empty or p.empty or g.empty:
        raise ValueError('alvos, precos e agenda precisam ter dados')
    for tabela, colunas in ((a, ['data_decisao', 'data_execucao']),
                            (p, ['data']), (g, ['data_decisao'])):
        for coluna in colunas:
            tabela[coluna] = _data(tabela[coluna])
    g['data_execucao'] = pd.to_datetime(g['data_execucao'], errors='coerce').dt.date
    a['ticker'] = a['ticker'].astype(str).str.upper().str.strip()
    p['ticker'] = p['ticker'].astype(str).str.upper().str.strip()
    if a[['data_execucao', 'ticker']].duplicated().any():
        raise ValueError('alvo duplicado por execucao/ticker')
    if p[['data', 'ticker']].duplicated().any():
        raise ValueError('preco duplicado por sessao/ticker')
    if g['data_decisao'].duplicated().any() or g['data_execucao'].dropna().duplicated().any():
        raise ValueError('agenda duplicada por decisao ou execucao')
    for coluna in ('preco_bruto', 'preco_fechamento_ajustado'):
        p[coluna] = pd.to_numeric(p[coluna], errors='coerce')
        if p[coluna].isna().any() or not p[coluna].map(math.isfinite).all() or p[coluna].le(0).any():
            raise ValueError(f'{coluna} ausente, nao finito ou nao positivo')
    a['peso'] = pd.to_numeric(a['peso'], errors='coerce')
    if a['peso'].isna().any() or not a['peso'].map(math.isfinite).all() or a['peso'].le(0).any():
        raise ValueError('peso alvo invalido')
    g['aporte_ficticio'] = pd.to_numeric(g['aporte_ficticio'], errors='coerce')
    if (g['aporte_ficticio'].isna().any() or
            not g['aporte_ficticio'].map(math.isfinite).all() or
            g['aporte_ficticio'].lt(0).any()):
        raise ValueError('aporte ficticio invalido')
    observadas = g.dropna(subset=['data_execucao']).copy()
    if observadas.empty:
        raise ValueError('nenhuma execucao D+1 observada; patrimonio ainda pendente')
    if set(observadas['data_execucao']) != set(a['data_execucao']):
        raise ValueError('cada execucao observada exige exatamente um alvo')
    if a['data_execucao'].isna().any() or (a['data_execucao'] <= a['data_decisao']).any():
        raise ValueError('alvo com D+1 ausente ou nao posterior a decisao')
    ag = observadas.set_index('data_execucao')
    for execucao, grupo in a.groupby('data_execucao'):
        if len(grupo) != 10 or abs(float(grupo['peso'].sum()) - 1) > 1e-9:
            raise ValueError(f'execucao {execucao}: exige dez ativos e pesos somando 1')
        if grupo['data_decisao'].nunique() != 1 or grupo['data_decisao'].iat[0] != ag.loc[execucao, 'data_decisao']:
            raise ValueError(f'execucao {execucao}: decisao divergente da agenda')
    sessoes = sorted(p['data'].unique())
    execucoes = sorted(a['data_execucao'].unique())
    if not set(execucoes).issubset(sessoes):
        raise ValueError('execucao sem fechamento B3 observado')
    primeira = execucoes[0]
    sessoes = [d for d in sessoes if d >= primeira]
    cotacoes = p.set_index(['data', 'ticker'])
    alvos_por_data = {d: grupo.set_index('ticker')['peso'].to_dict()
                      for d, grupo in a.groupby('data_execucao')}
    nivel = float(base)
    cotas = 0.0
    total_aportado = 0.0
    ultimo_giro = None
    nivel_giro = None
    nav_giro = None
    pesos = {}
    diario = []
    ordens = []

    def preco(dia, ticker, coluna):
        try:
            return float(cotacoes.loc[(dia, ticker), coluna])
        except KeyError as exc:
            raise ValueError(f'preco {coluna} ausente para {ticker} em {dia}') from exc

    for dia in sessoes:
        if ultimo_giro is None:
            valores_anteriores = {}
            nav_antes = 0.0
        else:
            valores_anteriores = {
                ticker: nav_giro * peso *
                preco(dia, ticker, 'preco_fechamento_ajustado') /
                preco(ultimo_giro, ticker, 'preco_fechamento_ajustado')
                for ticker, peso in pesos.items()}
            nav_antes = sum(valores_anteriores.values())
            nivel = nivel_giro * nav_antes / nav_giro
        aporte = 0.0
        if dia in alvos_por_data:
            aporte = float(ag.loc[dia, 'aporte_ficticio'])
            if ultimo_giro is None and aporte <= 0:
                raise ValueError('primeiro giro requer aporte ficticio positivo')
            nav_depois = nav_antes + aporte
            if not math.isfinite(nav_depois) or nav_depois <= 0:
                raise ValueError('patrimonio nocional invalido no giro')
            metas = alvos_por_data[dia]
            for ticker in sorted(set(valores_anteriores) | set(metas)):
                anterior = valores_anteriores.get(ticker, 0.0)
                alvo = nav_depois * metas.get(ticker, 0.0)
                financeiro = alvo - anterior
                bruto = preco(dia, ticker, 'preco_bruto')
                ordens.append({'data_decisao': ag.loc[dia, 'data_decisao'],
                               'data_execucao': dia, 'ticker': ticker,
                               'lado': 'COMPRA' if financeiro >= 0 else 'VENDA',
                               'financeiro_nocional': financeiro,
                               'preco_bruto_referencia': bruto,
                               'quantidade_teorica_fracionaria': financeiro / bruto,
                               'valor_anterior_total_return': anterior,
                               'valor_alvo': alvo,
                               'aporte_ficticio_no_giro': aporte})
            cotas += aporte / nivel
            total_aportado += aporte
            ultimo_giro, nivel_giro, nav_giro, pesos = dia, nivel, nav_depois, metas
        patrimonio = cotas * nivel
        if not math.isclose(patrimonio, nav_antes + aporte, rel_tol=1e-9, abs_tol=1e-6):
            raise ValueError('NAV, cotas e aportes nao fecham')
        diario.append({'data': dia, 'indice': nivel, 'aporte_ficticio': aporte,
                       'cotas_fundo': cotas, 'patrimonio_ficticio': patrimonio,
                       'total_aportado': total_aportado,
                       'ganho_ficticio': patrimonio - total_aportado})
    indice = pd.DataFrame(diario)
    indice['drawdown'] = indice['indice'] / indice['indice'].cummax() - 1
    # Carteira em vigor na ultima sessao: retorno de cada acao desde o giro e
    # quanto ela somou ao retorno do fundo no mesmo trecho (peso x retorno).
    ultimo = sessoes[-1]
    posicoes = pd.DataFrame([{
        'ticker': ticker, 'data_giro': ultimo_giro, 'data': ultimo, 'peso_alvo': peso,
        'retorno_desde_giro': preco(ultimo, ticker, 'preco_fechamento_ajustado') /
        preco(ultimo_giro, ticker, 'preco_fechamento_ajustado') - 1}
        for ticker, peso in sorted(pesos.items())])
    posicoes['contribuicao_desde_giro'] = posicoes['peso_alvo'] * posicoes['retorno_desde_giro']
    valores = posicoes['peso_alvo'] * (1 + posicoes['retorno_desde_giro'])
    posicoes['peso_atual'] = valores / valores.sum()
    operacoes = pd.DataFrame(ordens)
    fechamento = operacoes.groupby('data_execucao')['financeiro_nocional'].sum()
    aportes = ag.loc[fechamento.index, 'aporte_ficticio']
    if not all(math.isclose(float(fechamento.loc[d]), float(aportes.loc[d]),
                            rel_tol=1e-9, abs_tol=1e-6) for d in fechamento.index):
        raise ValueError('ordens nocionais nao fecham com os aportes')
    evidencia = json.dumps({'alvos': a.sort_values(['data_execucao', 'ticker']).to_dict('records'),
                           'agenda': observadas.sort_values('data_execucao').to_dict('records'),
                           'precos': p.sort_values(['data', 'ticker']).to_dict('records')},
                          default=str, sort_keys=True, ensure_ascii=False).encode('utf-8')
    return {'indice': indice[['data', 'indice', 'drawdown']],
            'patrimonio': indice.drop(columns='drawdown'),
            'operacoes_nocionais': operacoes,
            'posicoes': posicoes,
            'sha256_entradas': hashlib.sha256(evidencia).hexdigest(),
            'tipo': 'simulacao_nocional_total_return_sem_custos'}
