"""Compara fórmulas explícitas de ROIC CVM com gabarito histórico Fintz.

Este módulo é diagnóstico: nenhuma coluna Fintz entra no sinal atual. As
variantes medem sensibilidade a imposto e denominador; escolher a de menor
erro no passado seria ajustar o modelo ao gabarito e não prova equivalência.

Uso: python -m fontes.reconciliar_fintz_roic --as-of 2025-11-03 --anos 2023,2024,2025
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from fontes import cvm
from fontes.fundamentos import fundamentos_cvm

RAIZ = Path(__file__).resolve().parents[1]


def reconciliar(as_of: str, anos: list[int]) -> pd.DataFrame:
    corte = pd.Timestamp(as_of).normalize()
    d = fundamentos_cvm(anos, corte)
    m = cvm.security_master(anos)
    m = m[(m['inicio'].isna() | m['inicio'].le(corte)) &
          (m['fim'].isna() | m['fim'].ge(corte))]
    f = pd.read_parquet(RAIZ / 'dados' / 'ROIC.parquet')
    f['data'] = pd.to_datetime(f['data'], errors='coerce')
    f = f[f['data'].le(corte)].sort_values('data').drop_duplicates('ticker', keep='last')
    z = (d.merge(m[['cnpj', 'ticker', 'ano_fca']], on='cnpj', how='inner')
         .merge(f[['ticker', 'data', 'valor']], on='ticker', how='inner'))
    z = z.rename(columns={'valor': 'roic_fintz', 'data': 'data_fintz'})
    z['roic_efetivo_medio'] = z['ROIC_liquido_medio_diagnostico']
    denominadores = {
        'atual': z['capital_investido'],
        'medio': z['capital_investido_medio'],
        'bruto_atual': z['patrimonio_liquido'] + z['divida_bruta'],
        'ativo_menos_caixa': z['ativo_total'] - z['caixa'],
    }
    taxas = {'efetivo': z['aliquota_efetiva'], 'estatutario_34pct': 0.34}
    for nome_taxa, taxa in taxas.items():
        numerador = z['ebit_ttm'] * (1 - taxa)
        for nome_den, den in denominadores.items():
            nome = f'roic_{nome_taxa}_{nome_den}'
            z[nome] = numerador.div(den.where(den.gt(0)))
            if nome_taxa == 'efetivo':
                z.loc[~z['lair_ttm'].gt(0) |
                      ~z['aliquota_efetiva'].between(0, 1), nome] = float('nan')
            z[f'erro_pp_{nome_taxa}_{nome_den}'] = (z[nome] - z['roic_fintz']).abs() * 100
    z['quarentena_roic'] = (z['erro_pp_efetivo_bruto_atual'].isna() |
                             z['erro_pp_efetivo_bruto_atual'].gt(5))
    z['as_of'] = corte
    return z.sort_values('erro_pp_efetivo_bruto_atual', ascending=False,
                         na_position='last').reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--as-of', required=True)
    parser.add_argument('--anos', required=True)
    parser.add_argument('--saida')
    args = parser.parse_args()
    anos = [int(a) for a in args.anos.split(',')]
    z = reconciliar(args.as_of, anos)
    destino = (Path(args.saida) if args.saida else RAIZ / 'artifacts' /
               'reconciliacao' / 'fintz_roic' /
               f"roic_{pd.Timestamp(args.as_of).date().isoformat()}.csv")
    destino.parent.mkdir(parents=True, exist_ok=True)
    z.to_csv(destino, index=False)
    for col in [c for c in z if c.startswith('erro_pp_')]:
        erro = z[col].dropna()
        print(f'{col}: n={len(erro)}, mediana={erro.median():.2f}pp, '
              f'até 5pp={erro.le(5).mean():.1%}')
    print(f'CSV: {destino}')


if __name__ == '__main__':
    main()
