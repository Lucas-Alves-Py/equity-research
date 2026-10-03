"""Reconcilia EBIT TTM calculado da CVM com o arquivo Fintz histórico.

Fintz funciona aqui como gabarito retrospectivo. Este módulo nunca fornece
valores ao seletor nem substitui uma captura atual da CVM.

Uso (na pasta `fatores-b3`):
    python -m fontes.reconciliar_fintz_ebit --as-of 2025-11-03 --anos 2023,2024,2025
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from fontes import cvm

RAIZ = Path(__file__).resolve().parents[1]


def reconciliar(as_of: str, anos: list[int]) -> pd.DataFrame:
    corte = pd.Timestamp(as_of).normalize()
    trimestres = cvm.ebit_trimestral(anos, as_of=corte)
    trimestres = trimestres.sort_values(['cnpj', 'dt_refer', 'dt_receb'])
    # Uma série PIT deve ter no máximo um EBIT trimestral disponível por
    # emissor/data-base depois da seleção de versão feita em fontes.cvm.
    dup = trimestres.duplicated(['cnpj', 'dt_refer'], keep=False)
    if dup.any():
        raise ValueError(
            'EBIT CVM ainda tem mais de uma observação por emissor/data: '
            f"{trimestres.loc[dup, ['cnpj','dt_refer','doc','versao']].head(10).to_dict('records')}"
        )

    ttm = (trimestres.groupby('cnpj', sort=False)
           .tail(4).groupby('cnpj', as_index=False)
           .agg(ebit_ttm_cvm=('ebit', 'sum'), trimestres=('dt_refer', 'nunique'),
                protocolo_mais_recente=('dt_receb', 'max'),
                periodo_mais_recente=('dt_refer', 'max')))
    ttm = ttm[ttm['trimestres'].eq(4)]

    cadastro = cvm.security_master(anos)
    inicio_ok = cadastro['inicio'].isna() | cadastro['inicio'].le(corte)
    fim_ok = cadastro['fim'].isna() | cadastro['fim'].ge(corte)
    cadastro = cadastro[inicio_ok & fim_ok].drop_duplicates(['cnpj', 'ticker'])

    fintz_path = RAIZ / 'dados' / 'EBIT.parquet'
    if not fintz_path.exists():
        raise FileNotFoundError(f'gabarito Fintz local não encontrado: {fintz_path}')
    fintz = pd.read_parquet(fintz_path)
    fintz['data'] = pd.to_datetime(fintz['data'], errors='coerce')
    fintz = fintz[(fintz['data'] <= corte) & fintz['item'].eq('EBIT')]
    fintz = (fintz.sort_values('data')
             .drop_duplicates('ticker', keep='last')
             .rename(columns={'data': 'data_fintz', 'valor': 'ebit_ttm_fintz'}))

    comparacao = (cadastro.merge(ttm, on='cnpj', how='inner')
                  .merge(fintz[['ticker', 'data_fintz', 'ebit_ttm_fintz']],
                         on='ticker', how='inner'))
    comparacao['diferenca_abs'] = (comparacao['ebit_ttm_cvm'] -
                                   comparacao['ebit_ttm_fintz']).abs()
    denominador = comparacao['ebit_ttm_fintz'].abs()
    comparacao['diferenca_relativa'] = comparacao['diferenca_abs'].div(
        denominador.where(denominador.ne(0)))
    comparacao['gabarito_1pct'] = comparacao['diferenca_relativa'].le(0.01)
    comparacao['gabarito_5pct'] = comparacao['diferenca_relativa'].le(0.05)
    # Checagem de materialidade absoluta evita quocientes enormes para EBIT
    # próximo de zero, sem permitir desvio grande em valor monetário.
    comparacao['limiar_material_reais'] = (denominador * 0.05).clip(lower=100_000)
    comparacao['quarentena_ebit'] = (
        comparacao['diferenca_abs'] > comparacao['limiar_material_reais'])
    comparacao.insert(0, 'as_of', corte.date().isoformat())
    return comparacao.sort_values('diferenca_relativa', ascending=False,
                                  na_position='first').reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--as-of', required=True, help='data do sinal (YYYY-MM-DD)')
    parser.add_argument('--anos', required=True,
                        help='anos dos arquivos CVM usados, separados por vírgula')
    parser.add_argument('--saida', help='CSV de saída; por padrão, artifacts/reconciliacao/fintz_ebit/')
    args = parser.parse_args()
    anos = [int(a) for a in args.anos.split(',')]
    resultado = reconciliar(args.as_of, anos)
    destino = (Path(args.saida) if args.saida else
               RAIZ / 'artifacts' / 'reconciliacao' / 'fintz_ebit' /
               f"ebit_ttm_{pd.Timestamp(args.as_of).date().isoformat()}.csv")
    destino.parent.mkdir(parents=True, exist_ok=True)
    resultado.to_csv(destino, index=False)
    validos = resultado['diferenca_relativa'].dropna()
    linhas_validas = resultado.loc[validos.index]
    print(f'linhas comparáveis: {len(resultado)}')
    print(f'linhas com denominador válido: {len(validos)}')
    print(f'mediana diferença relativa: {validos.median():.2%}')
    print(f'até 1%: {linhas_validas.gabarito_1pct.mean():.1%}')
    print(f'até 5%: {linhas_validas.gabarito_5pct.mean():.1%}')
    print(f'CSV: {destino}')


if __name__ == '__main__':
    main()
