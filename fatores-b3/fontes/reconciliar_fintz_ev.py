"""Auditoria retrospectiva de EV/EBIT; Fintz apenas como gabarito historico.

Uso: python -m fontes.reconciliar_fintz_ev --as-of 2025-11-03
Nenhum valor deste modulo alimenta a selecao atual.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from fontes import cvm
from fontes.fundamentos import fundamentos_cvm


RAIZ = Path(__file__).resolve().parents[1]


def _indicador(nome: str, corte: pd.Timestamp) -> pd.DataFrame:
    caminho = RAIZ / 'dados' / f'{nome}.parquet'
    d = pd.read_parquet(caminho)
    d['data'] = pd.to_datetime(d['data'], errors='coerce')
    d = d[d['data'].le(corte)].sort_values('data').drop_duplicates('ticker', keep='last')
    return d[['ticker', 'data', 'valor']].rename(columns={
        'data': f'data_{nome}', 'valor': f'fintz_{nome}'})


def reconciliar(as_of: str, anos: list[int]) -> pd.DataFrame:
    corte = pd.Timestamp(as_of).normalize()
    master = cvm.security_master(anos)
    master = master[(master['inicio'].isna() | master['inicio'].le(corte)) &
                    (master['fim'].isna() | master['fim'].ge(corte))]
    master = master[['ticker', 'cnpj']].drop_duplicates()
    if master['ticker'].duplicated().any():
        raise ValueError('ticker historico vinculado a mais de um CNPJ')
    base = fundamentos_cvm(anos, corte)[['cnpj', 'ebit_ttm', 'divida_bruta', 'caixa',
                                         'dt_receb_dre', 'dt_receb_balanco']]
    if base['cnpj'].duplicated().any():
        raise ValueError('fundamentos duplicados por CNPJ')
    d = master.merge(base, on='cnpj', how='inner', validate='many_to_one')
    for nome in ('ValorDeMercado', 'EV', 'EV_EBIT'):
        d = d.merge(_indicador(nome, corte), on='ticker', how='inner', validate='one_to_one')
    d['ev_cvm_com_vm_fintz'] = d['fintz_ValorDeMercado'] + d['divida_bruta'] - d['caixa']
    d['ev_ebit_cvm_com_vm_fintz'] = d['ev_cvm_com_vm_fintz'] / d['ebit_ttm']
    d['ev_ebit_fintz_aritmetico'] = d['fintz_EV'] / d['ebit_ttm']
    d['erro_ev_relativo'] = (d['ev_cvm_com_vm_fintz'] - d['fintz_EV']).abs() / d['fintz_EV'].abs()
    d['erro_indicador_absoluto'] = (d['ev_ebit_cvm_com_vm_fintz'] - d['fintz_EV_EBIT']).abs()
    d['erro_indicador_relativo'] = d['erro_indicador_absoluto'] / d['fintz_EV_EBIT'].abs()
    d.insert(0, 'as_of', corte.date().isoformat())
    return d.sort_values('erro_indicador_relativo', ascending=False).reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--as-of', default='2025-11-03')
    parser.add_argument('--anos', default='2023,2024,2025')
    args = parser.parse_args()
    d = reconciliar(args.as_of, [int(a) for a in args.anos.split(',')])
    destino = RAIZ / 'artifacts' / 'reconciliacao' / 'fintz_ev' / f'ev_ebit_{args.as_of}.csv'
    destino.parent.mkdir(parents=True, exist_ok=True)
    d.to_csv(destino, index=False)
    erro = d['erro_indicador_relativo'].replace([float('inf'), -float('inf')], float('nan')).dropna()
    print(f'comparaveis={len(erro)} mediana={erro.median():.2%} ate_5pct={erro.le(.05).mean():.1%}')
    print(destino)


if __name__ == '__main__':
    main()
