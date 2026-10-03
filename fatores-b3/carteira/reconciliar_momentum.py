"""Compara variantes de momentum B3 com o gabarito Fintz histórico local.

Esta rotina de auditoria não alimenta a seleção atual. Ela mede qual conceito
de ajuste o painel histórico usado na calibração realmente representa.
"""

from __future__ import annotations

import pandas as pd

from fontes import b3


def reconciliar(cotacoes: pd.DataFrame, gabarito: pd.DataFrame,
               data_sinal, tickers: list[str]) -> pd.DataFrame:
    corte = pd.Timestamp(data_sinal).normalize()
    dados = cotacoes.copy()
    dados['data'] = pd.to_datetime(dados['data']).dt.normalize()
    dados = dados[dados['data'].le(corte)].sort_values(['ticker', 'data'])
    fintz = gabarito.copy()
    fintz['data'] = pd.to_datetime(fintz['data']).dt.normalize()
    fintz = fintz[fintz['data'].eq(corte)].set_index('ticker')['valor']
    capturas = {}
    linhas = []
    for ticker in tickers:
        hist = dados[dados['ticker'].eq(ticker)].tail(253).set_index('data')['fechamento']
        if len(hist) < 253 or hist.index.max() != corte:
            linhas.append({'ticker': ticker, 'status': 'histórico insuficiente'})
            continue
        try:
            prefixo = ticker[:4]
            if prefixo not in capturas:
                empresa = b3.empresa(ticker)
                if not empresa:
                    raise ValueError('emissor ausente na B3')
                capturas[prefixo] = (empresa, b3.proventos(empresa['tradingName'], pausa=0.05),
                                     b3.eventos_ativos_empresa(empresa))
            empresa, caixa, acoes = capturas[prefixo]
            caixa = caixa[caixa['ultimo_com'].between(hist.index.min(), corte)]
            acoes = b3.proventos_ativos(ticker, empresa, acoes)
            acoes = acoes[acoes['ultimo_com'].between(hist.index.min(), corte)]
            if caixa['aprovado_em'].isna().any() or acoes['aprovado_em'].isna().any():
                raise ValueError('evento sem aprovação')
            caixa = caixa[caixa['aprovado_em'].lt(corte)]
            acoes = acoes[acoes['aprovado_em'].lt(corte)]
            fa = b3.fatores_ativos(acoes, as_of=corte)
            fc = b3.fatores(caixa, ticker, as_of=corte)
            juntos = [f for f in (fa, fc) if not f.empty]
            ft = (pd.concat(juntos).groupby('ultimo_com', as_index=False)['fator'].prod()
                  if juntos else pd.DataFrame(columns=['ultimo_com', 'fator']))
            bruto = hist.iloc[-1] / hist.iloc[0] - 1
            acoes_ret = b3.ajustar(hist, fa)
            total_ret = b3.ajustar(hist, ft)
            referencia = fintz.get(ticker, float('nan'))
            linhas.append({'ticker': ticker, 'status': 'ok', 'data_base': hist.index.min().date(),
                           'gabarito_fintz': referencia, 'bruto': bruto,
                           'acoes_apenas': acoes_ret.iloc[-1] / acoes_ret.iloc[0] - 1,
                           'retorno_total': total_ret.iloc[-1] / total_ret.iloc[0] - 1,
                           'eventos_caixa': len(caixa), 'eventos_acoes': len(acoes)})
        except Exception as exc:
            linhas.append({'ticker': ticker, 'status': str(exc)[:160]})
    saida = pd.DataFrame(linhas)
    for coluna in ('bruto', 'acoes_apenas', 'retorno_total'):
        saida[f'erro_{coluna}_pp'] = (saida[coluna] - saida['gabarito_fintz']).abs() * 100
    return saida
