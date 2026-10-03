"""Agenda do laboratorio: sinal mensal e aportes ficticios separados do retorno.

O dia 5, ou o primeiro pregao seguinte, e a data da decisao. A carteira e
executada no pregao seguinte. O valor de R$ 10 mil e uma hipotese de treino;
nao representa dinheiro, ordem ou aconselhamento pessoal.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

import pandas as pd
import yaml


CONFIG_MENSAL = Path(__file__).resolve().parent / 'config' / 'simulacao_mensal.yaml'


def mandato_mensal() -> dict:
    with CONFIG_MENSAL.open(encoding='utf-8') as arquivo:
        return yaml.safe_load(arquivo)


def agenda_mensal(sessoes, inicio, fim, aporte_ficticio=None) -> pd.DataFrame:
    """Uma decisao por mes; execucao somente apos D+1 observado.

    Se o dia de decisao ja apareceu no calendario observado, mas o pregao
    seguinte ainda nao, a linha fica pendente e o aporte nao e aplicado.
    """
    regra = mandato_mensal()
    valor = float(regra['aporte_ficticio_brl'] if aporte_ficticio is None
                  else aporte_ficticio)
    if valor < 0 or not pd.notna(valor) or valor == float('inf'):
        raise ValueError('aporte ficticio invalido')
    datas = pd.to_datetime(pd.Series(list(sessoes)), errors='coerce')
    if datas.isna().any() or datas.duplicated().any():
        raise ValueError('calendario B3 contem data invalida/duplicada')
    datas = sorted(datas.dt.date)
    inicio = pd.Timestamp(inicio).date()
    fim = pd.Timestamp(fim).date()
    if inicio > fim:
        raise ValueError('inicio posterior ao fim')
    linhas = []
    meses = pd.period_range(inicio, fim, freq='M')
    for mes in meses:
        alvo = dt.date(mes.year, mes.month, int(regra['dia_decisao']))
        if alvo < inicio or alvo > fim:
            continue
        mes_sessoes = [d for d in datas if d.year == mes.year and
                       d.month == mes.month and d >= alvo]
        if not mes_sessoes:
            raise ValueError(f'sem pregao B3 apos dia 5 em {mes}')
        decisao = mes_sessoes[0]
        posteriores = [d for d in datas if d > decisao]
        execucao = posteriores[0] if posteriores else None
        linhas.append({'data_decisao': decisao,
                       'data_execucao': execucao,
                       'aporte_ficticio': valor,
                       'status_execucao': 'observada' if execucao else 'pendente'})
    return pd.DataFrame(linhas, columns=['data_decisao', 'data_execucao',
                                         'aporte_ficticio', 'status_execucao'])


def patrimonio_com_aportes(indice: pd.DataFrame, agenda: pd.DataFrame,
                           capital_inicial_ficticio=0.0) -> pd.DataFrame:
    """Unitiza aportes em D+1 para separar ganho de dinheiro adicionado.

    `indice` mede somente retorno. `patrimonio_ficticio` soma as cotas
    adquiridas com cada aporte; aportes nao entram no retorno do indice.
    """
    if {'data', 'indice'} - set(indice.columns):
        raise ValueError('indice precisa de data e indice')
    if {'data_execucao', 'aporte_ficticio'} - set(agenda.columns):
        raise ValueError('agenda incompleta')
    serie = indice[['data', 'indice']].copy()
    serie['data'] = pd.to_datetime(serie['data'], errors='coerce').dt.date
    serie['indice'] = pd.to_numeric(serie['indice'], errors='coerce')
    if (serie['data'].isna().any() or serie['data'].duplicated().any() or
            serie['indice'].isna().any() or serie['indice'].le(0).any()):
        raise ValueError('indice com datas/precos invalidos')
    aportes = agenda[['data_execucao', 'aporte_ficticio']].copy()
    aportes['data_execucao'] = pd.to_datetime(aportes['data_execucao'], errors='coerce').dt.date
    # Uma decisao pendente ainda nao comprou cotas e nao soma patrimonio.
    aportes = aportes[aportes['data_execucao'].notna()].copy()
    aportes['aporte_ficticio'] = pd.to_numeric(aportes['aporte_ficticio'], errors='coerce')
    if (aportes['data_execucao'].isna().any() or
            aportes['data_execucao'].duplicated().any() or
            aportes['aporte_ficticio'].isna().any() or
            aportes['aporte_ficticio'].lt(0).any()):
        raise ValueError('agenda de aportes invalida')
    if not set(aportes['data_execucao']).issubset(set(serie['data'])):
        raise ValueError('aporte com D+1 ausente do indice')
    if capital_inicial_ficticio < 0:
        raise ValueError('capital inicial negativo')
    serie = serie.sort_values('data').reset_index(drop=True)
    valores = aportes.set_index('data_execucao')['aporte_ficticio']
    serie['aporte_ficticio'] = serie['data'].map(valores).fillna(0.0)
    serie.loc[0, 'aporte_ficticio'] += float(capital_inicial_ficticio)
    serie['cotas_compradas'] = serie['aporte_ficticio'] / serie['indice']
    serie['cotas_acumuladas'] = serie['cotas_compradas'].cumsum()
    serie['patrimonio_ficticio'] = serie['cotas_acumuladas'] * serie['indice']
    serie['total_aportado'] = serie['aporte_ficticio'].cumsum()
    serie['ganho_ficticio'] = serie['patrimonio_ficticio'] - serie['total_aportado']
    return serie
