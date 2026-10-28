"""EV/EBIT da base própria contra Fintz e Varos, componente por componente.

Para cada ação em que o nosso EV/EBIT difere do da Fintz em mais de 5%, separa
a causa (valor de mercado, dívida líquida ou EBIT) e usa a Varos para
desempatar: se a Varos fica perto do nosso número, quem diverge é a Fintz; se
fica perto do da Fintz, quem diverge somos nós. EBIT e dívida líquida dos
fornecedores são implícitos (EV / EV_EBIT e EV − valor de mercado).

Uso (na pasta `fatores-b3`):
    python -m fontes.conferir_ev_ebit 2013-06-05 2016-12-05 2019-12-05
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
PAINEIS = RAIZ / 'dados' / 'historico_pipeline_dia5' / 'paineis'
FONTES = {'fintz': RAIZ / 'dados', 'varos': RAIZ / 'dados' / 'gabaritos' / 'varos_2026-10-08' / 'dados'}
TOLERANCIA = 0.05


def _fornecedor(nome: str, data: pd.Timestamp) -> pd.DataFrame:
    partes = {}
    for ind in ('EV', 'EV_EBIT', 'ValorDeMercado'):
        d = pd.read_parquet(FONTES[nome] / f'{ind}.parquet', columns=['data', 'ticker', 'valor'])
        d = d[pd.to_datetime(d['data']).eq(data)]
        partes[ind] = d.set_index('ticker')['valor'].astype(float)
    d = pd.DataFrame(partes)
    return pd.DataFrame({'ev_ebit': d['EV_EBIT'], 'vm': d['ValorDeMercado'],
                         'ebit': d['EV'] / d['EV_EBIT'], 'dl': d['EV'] - d['ValorDeMercado'],
                         'ev': d['EV']}).add_prefix(f'{nome}_')


def _perto(a, b, escala=None):
    escala = b.abs() if escala is None else escala.abs()
    return (a - b).abs() <= TOLERANCIA * escala


def conferir(data: str) -> pd.DataFrame:
    data = pd.Timestamp(data)
    p = pd.read_parquet(PAINEIS / f'{data.date()}.parquet').set_index('ticker')
    nosso = pd.DataFrame({'nosso_ev_ebit': p['EV_EBIT'], 'nosso_vm': p['valor_mercado'],
                          'nosso_ebit': p['ebit_ttm'], 'nosso_dl': p['divida_bruta'] - p['caixa']}).astype(float)
    d = nosso.join(_fornecedor('fintz', data), how='inner').join(_fornecedor('varos', data), how='left')
    d = d.dropna(subset=['nosso_ev_ebit', 'fintz_ev_ebit'])
    d = d[~_perto(d['nosso_ev_ebit'], d['fintz_ev_ebit'])].copy()
    causas, veredictos = [], []
    for _, r in d.iterrows():
        diverge, quem = [], set()
        for comp in ('vm', 'dl', 'ebit'):
            escala = pd.Series([r['fintz_ev']]) if comp == 'dl' else None
            n, f, v = (pd.Series([r[f'{x}_{comp}']]) for x in ('nosso', 'fintz', 'varos'))
            if _perto(n, f, escala).iloc[0]:
                continue
            diverge.append(comp)
            if pd.isna(v.iloc[0]):
                quem.add('sem Varos')
            elif _perto(v, n, escala if comp == 'dl' else n).iloc[0]:
                quem.add('Fintz')
            elif _perto(v, f, escala).iloc[0]:
                quem.add('nosso')
            else:
                quem.add('sem consenso')
        causas.append('+'.join(diverge) or 'arredondamento')
        veredictos.append('nosso' if 'nosso' in quem else 'Fintz' if quem == {'Fintz'}
                          else 'indefinido' if quem else 'arredondamento')
    d['causa'], d['quem_diverge'] = causas, veredictos
    d.insert(0, 'data', data.date())
    return d


if __name__ == '__main__':
    pd.set_option('display.width', 250)
    datas = sys.argv[1:] or ['2013-06-05', '2015-06-05', '2016-12-05', '2018-06-05', '2019-12-05',
                             '2021-06-07', '2024-06-05']
    tudo = pd.concat([conferir(d) for d in datas])
    destino = RAIZ / 'artifacts' / 'auditoria' / '2026-10-15' / 'ev_ebit_tres_bases.csv'
    destino.parent.mkdir(parents=True, exist_ok=True)
    tudo.to_csv(destino)
    print(pd.crosstab(tudo['data'], tudo['quem_diverge'], margins=True))
    print(pd.crosstab(tudo['causa'], tudo['quem_diverge'], margins=True))
    print(destino)
