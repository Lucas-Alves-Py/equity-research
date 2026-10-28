"""Retorno do modelo congelado contra os fatores de risco do NEFIN-USP.

Protocolo em `EXPERIMENTO_REGRESSAO_NEFIN.md`, registrado antes de rodar.

Modelo e universo vêm do mesmo motor e da mesma grade de
`pesquisa.backtest_pipeline_proprio --dia5`. Os fatores (Rm-Rf, SMB, HML, WML,
IML e Risk_Free, diários) são baixados uma vez para `dados/nefin/`; o artefato
registra data do download, SHA-256 e última data da série.

Pré-requisito: `python -m coleta.construir_historico --dia5`.
Uso (na pasta `fatores-b3`):
    .venv/Scripts/python.exe -m pesquisa.regressao_nefin
"""

from __future__ import annotations

import hashlib
import json
import urllib.request
from datetime import date
from pathlib import Path

import pandas as pd
import statsmodels.api as sm

from pesquisa import backtest_pipeline_proprio as bpp

RAIZ = Path(__file__).resolve().parents[1]
URL_NEFIN = 'https://nefin.com.br/nefindata/risk-factors/nefin_factors.csv'
ARQUIVO_NEFIN = RAIZ / 'dados' / 'nefin' / 'nefin_factors.csv'
SAIDA = RAIZ / 'artifacts' / 'regressao-nefin'
FIM_REFERENCIA = '2025-11-04'
CINCO = ['Rm_minus_Rf', 'SMB', 'HML', 'WML', 'IML']
CARHART = ['Rm_minus_Rf', 'SMB', 'HML', 'WML']


def fatores_nefin() -> tuple[pd.DataFrame, dict]:
    if not ARQUIVO_NEFIN.exists():
        ARQUIVO_NEFIN.parent.mkdir(parents=True, exist_ok=True)
        urllib.request.urlretrieve(URL_NEFIN, ARQUIVO_NEFIN)
        (ARQUIVO_NEFIN.parent / 'baixado_em.txt').write_text(str(date.today()), encoding='utf-8')
    f = pd.read_csv(ARQUIVO_NEFIN, index_col=0, parse_dates=['Date']).set_index('Date').sort_index()
    meta = {'url': URL_NEFIN,
            'baixado_em': (ARQUIVO_NEFIN.parent / 'baixado_em.txt').read_text(encoding='utf-8').strip(),
            'sha256': hashlib.sha256(ARQUIVO_NEFIN.read_bytes()).hexdigest(),
            'primeira_data': str(f.index[0].date()), 'ultima_data': str(f.index[-1].date())}
    return f, meta


def series_backtest(fim: str) -> pd.DataFrame:
    """Retornos diários do modelo (10 ações) e do universo, grade do dia 5."""
    bpp.HISTORICO = RAIZ / 'dados' / 'historico_pipeline_dia5'
    grade = sorted(p.stem for p in (bpp.HISTORICO / 'paineis').glob('*.parquet'))
    pasta = bpp._preparar('pipeline', grade)
    modelo, _ = bpp._rodar(pasta, grade[0], fim, 10)
    universo, _ = bpp._rodar(pasta, grade[0], fim, 99_999)
    return pd.DataFrame({'modelo': modelo, 'universo': universo}).pct_change().dropna()


def mensal(d: pd.DataFrame) -> pd.DataFrame:
    return (1 + d).groupby(d.index.to_period('M')).prod() - 1


def regressao(y: pd.Series, x: pd.DataFrame, lags: int, periodos_ano: int,
              dimson: bool = False) -> dict:
    """OLS com Newey-West. Em Dimson, cada beta é a soma do coeficiente do dia e da defasagem."""
    if dimson:
        x = pd.concat([x, x.shift(1).add_suffix('_lag')], axis=1)
    dados = pd.concat([y.rename('y'), x], axis=1).dropna()
    r = sm.OLS(dados['y'], sm.add_constant(dados.drop(columns='y'))).fit(
        cov_type='HAC', cov_kwds={'maxlags': lags})
    se_alfa = float(r.bse['const'])
    out = {'alfa_aa': float(r.params['const']) * periodos_ano, 't_alfa': float(r.tvalues['const']),
           'alfa_minimo_detectavel_aa': 2 * se_alfa * periodos_ano,
           'r2_ajustado': float(r.rsquared_adj), 'n': int(r.nobs), 'betas': {}}
    for f in [c for c in x.columns if not c.endswith('_lag')]:
        termo = f'{f} + {f}_lag = 0' if dimson else f'{f} = 0'
        t = r.t_test(termo)
        out['betas'][f] = {'beta': float(t.effect[0]), 't': float(t.tvalue.item())}
    return out


def main() -> None:
    fatores, meta = fatores_nefin()
    diario = series_backtest(meta['ultima_data']).join(fatores, how='inner')
    m = mensal(diario)
    m_ref = mensal(diario[diario.index <= FIM_REFERENCIA])
    excesso = lambda d, s: d[s] - d['Risk_Free']

    resultado = {
        'nefin': meta,
        'periodo': {'inicio': str(diario.index[0].date()), 'fim': str(diario.index[-1].date()),
                    'meses': len(m), 'dias': len(diario)},
        'principal': regressao(excesso(m, 'modelo'), m[CINCO], 3, 12),
        'capm': regressao(excesso(m, 'modelo'), m[['Rm_minus_Rf']], 3, 12),
        'carhart': regressao(excesso(m, 'modelo'), m[CARHART], 3, 12),
        'universo': regressao(excesso(m, 'universo'), m[CINCO], 3, 12),
        'diario_dimson': regressao(excesso(diario, 'modelo'), diario[CINCO], 10, 252, dimson=True),
        'ate_' + FIM_REFERENCIA: regressao(excesso(m_ref, 'modelo'), m_ref[CINCO], 3, 12),
    }
    # Hipótese do protocolo: WML positivo e o maior coeficiente entre os fatores.
    # A parada só vale se o WML não for positivo.
    betas = {f: b['beta'] for f, b in resultado['principal']['betas'].items() if f != 'Rm_minus_Rf'}
    resultado['wml_positivo_e_maior'] = betas['WML'] > 0 and betas['WML'] == max(betas.values())

    SAIDA.mkdir(parents=True, exist_ok=True)
    (SAIDA / 'resultado.json').write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding='utf-8')
    linhas = ['| Especificação | alfa a.a. | t(alfa) | alfa mín. detectável | '
              + ' | '.join(CINCO) + ' | R² aj. | n |', '|---' * (len(CINCO) + 6) + '|']
    for nome, r in resultado.items():
        if not isinstance(r, dict) or 'alfa_aa' not in r:
            continue
        b = [f"{r['betas'][f]['beta']:.2f} ({r['betas'][f]['t']:.1f})" if f in r['betas'] else '' for f in CINCO]
        linhas.append(f"| {nome} | {r['alfa_aa']:.1%} | {r['t_alfa']:.2f} | {r['alfa_minimo_detectavel_aa']:.1%} | "
                      + ' | '.join(b) + f" | {r['r2_ajustado']:.2f} | {r['n']} |")
    (SAIDA / 'tabela.md').write_text('\n'.join(linhas) + '\n', encoding='utf-8')
    print(json.dumps(resultado['periodo'], ensure_ascii=False))
    print('\n'.join(linhas))
    print('WML positivo e maior:', resultado['wml_positivo_e_maior'])


if __name__ == '__main__':
    main()
