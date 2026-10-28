"""Modelo congelado (TRIO_ROIC) no histórico do pipeline próprio e na base Fintz.

Sem a base Fintz (licenciada) em `dados/`, roda só o pipeline próprio.

As duas bases passam pelo mesmo motor (`calculadora_factor`) e pela mesma
grade de rebalanceamento, a das datas em `dados/historico_pipeline/paineis/`.
Assim a diferença entre as curvas vem só dos dados. A Fintz termina em
03/11/2025; o pipeline segue até a última data construída.

Pré-requisito: `python -m coleta.construir_historico`.
Uso (na pasta `fatores-b3`):
    python -m pesquisa.backtest_pipeline_proprio
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd

RAIZ = Path(__file__).resolve().parents[1]
HISTORICO = RAIZ / 'dados' / 'historico_pipeline'
FIM_FINTZ = '2025-11-04'
TRIO = {'carteira1': {'indicadores': {'EV_EBIT': {'caracteristica': 'crescente'},
                                      'ROIC': {'caracteristica': 'decrescente'},
                                      'momento_12_meses': {'caracteristica': 'decrescente'}},
                      'peso': 1}}
ARQUIVOS = ['volume_mediano', 'EV_EBIT', 'ROIC', 'momento_12_meses']


def _preparar(base: str, grade: list) -> Path:
    """Pasta com `dados/` no formato do motor, indicadores só nas datas da grade."""
    pasta = HISTORICO / f'backtest_{base}' / 'dados'
    pasta.mkdir(parents=True, exist_ok=True)
    origem = HISTORICO if base == 'pipeline' else RAIZ / 'dados'
    datas = set(pd.to_datetime(grade))
    for nome in ARQUIVOS:
        d = pd.read_parquet(origem / f'{nome}.parquet', columns=['data', 'ticker', 'valor'])
        d['data'] = pd.to_datetime(d['data'])
        d[d['data'].isin(datas)].to_parquet(pasta / f'{nome}.parquet', index=False)
    c = pd.read_parquet(origem / 'cotacoes.parquet', columns=['data', 'ticker', 'preco_fechamento_ajustado'])
    c['data'] = pd.to_datetime(c['data'])
    c[c['data'].ge(min(datas) - pd.Timedelta(days=10))].to_parquet(pasta / 'cotacoes.parquet', index=False)
    return pasta.parent


def _rodar(pasta: Path, inicio, fim, numero_ativos: int):
    sys.path.insert(0, str(RAIZ))
    from calculadora_factor import backtest_indicators
    anterior = os.getcwd()
    os.chdir(pasta)
    try:
        b = backtest_indicators(data_final=str(fim), data_inicial=str(inicio), filtro_liquidez=500_000,
                                balanceamento=1, numero_ativos=numero_ativos, corretagem=0,
                                nome_arquivo='x.pdf', **TRIO)
        b.pegando_dados(); b.filtrando_datas(); b.criando_carteiras(); b.calculando_retorno_diario()
    finally:
        os.chdir(anterior)
    nivel = b.df_retornos.dropna(subset=['data']).set_index('data')['dinheiro'].astype(float)
    nivel.index = pd.to_datetime(nivel.index)
    return nivel, b.carteira_por_periodo


def _metricas(nivel: pd.Series) -> dict:
    r = nivel.pct_change().dropna()
    anos = len(r) / 252
    return {'retorno_aa': float(nivel.iat[-1] / nivel.iat[0]) ** (1 / anos) - 1,
            'vol_aa': float(r.std() * np.sqrt(252)),
            'max_drawdown': float((nivel / nivel.cummax() - 1).min()),
            'inicio': str(nivel.index[0].date()), 'fim': str(nivel.index[-1].date())}


def main() -> None:
    global HISTORICO
    if '--dia5' in sys.argv:  # grade da carteira ao vivo (coleta.construir_historico --dia5)
        HISTORICO = RAIZ / 'dados' / 'historico_pipeline_dia5'
    grade = sorted(p.stem for p in (HISTORICO / 'paineis').glob('*.parquet'))
    inicio, fim_pipeline = grade[0], pd.Timestamp(grade[-1]) + pd.Timedelta(days=30)
    # A base Fintz é licenciada: sem ela em `dados/`, roda só o pipeline próprio.
    bases = ['pipeline', 'fintz'] if (RAIZ / 'dados' / 'EV_EBIT.parquet').exists() else ['pipeline']
    pastas = {base: _preparar(base, grade) for base in bases}
    resultado, carteiras = {}, {}
    rodadas = [('pipeline', FIM_FINTZ), ('fintz', FIM_FINTZ), ('pipeline_ate_hoje', fim_pipeline.date())]
    for base, fim in [(b, f) for b, f in rodadas if b in bases or b == 'pipeline_ate_hoje']:
        pasta = pastas['pipeline' if base.startswith('pipeline') else 'fintz']
        modelo, cart = _rodar(pasta, inicio, fim, 10)
        universo, _ = _rodar(pasta, inicio, fim, 99_999)
        resultado[base] = {'modelo': _metricas(modelo), 'universo': _metricas(universo)}
        carteiras[base] = cart
    if 'fintz' in carteiras:
        a, b = carteiras['pipeline'], carteiras['fintz']
        comum = a.merge(b, on=['data', 'ticker']).groupby('data').size()
        resultado['sobreposicao_media_carteiras'] = float((comum / a.groupby('data').size()).fillna(0).mean())
    destino = RAIZ / 'artifacts' / 'auditoria' / str(pd.Timestamp.today().date()) / 'backtest_pipeline_proprio.json'
    destino.parent.mkdir(parents=True, exist_ok=True)
    destino.write_text(json.dumps(resultado, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(resultado, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
