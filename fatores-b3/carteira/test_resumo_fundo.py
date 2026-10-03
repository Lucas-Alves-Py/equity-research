"""Resumo do fundo fictício lido pelo painel do QuantHub."""

import datetime as dt
import json

import pandas as pd

from carteira.execucao_mensal import atualizar_fundo_mensal
from carteira.resumo_fundo import exportar_resumo, resumir_fundo
from carteira.test_execucao_mensal import _publicar


def _series(nomes, inicio, fim, as_of, cotacoes):
    pedacos = cotacoes[(pd.to_datetime(cotacoes['data']).dt.date >= inicio) &
                       (pd.to_datetime(cotacoes['data']).dt.date <= fim) &
                       cotacoes['ticker'].isin(nomes)]
    return pedacos.rename(columns={'fechamento': 'preco_bruto'}).assign(
        preco_fechamento_ajustado=pedacos['fechamento'])[
            ['data', 'ticker', 'preco_bruto', 'preco_fechamento_ajustado']]


def _cotacoes(tickers, precos_por_dia):
    return pd.DataFrame([{'data': dia, 'ticker': ticker, 'fechamento': preco,
                          'volume_negociado': 1_000_000}
                         for dia, preco in precos_por_dia for ticker in tickers])


def test_sem_agenda_informa_que_nao_ha_decisao(tmp_path):
    resumo = resumir_fundo(tmp_path)
    assert resumo['status'] == 'sem_decisao'
    assert resumo['summary'] is None and resumo['decisions'] == []


def test_decisao_sem_d1_aparece_pendente(tmp_path):
    tickers = _publicar(tmp_path)
    atualizar_fundo_mensal(tmp_path, as_of='2025-01-02',
                           cotacoes=_cotacoes(tickers, [(dt.date(2025, 1, 2), 10.0)]))
    resumo = resumir_fundo(tmp_path)
    assert resumo['status'] == 'pendente_D+1'
    assert resumo['summary'] is None
    assert resumo['decisions'][0]['executionDate'] is None
    assert len(resumo['decisions'][0]['positions']) == 10


def test_nav_calculado_resume_retorno_patrimonio_e_execucao(tmp_path):
    tickers = _publicar(tmp_path)
    # Compra a 10 no D+1 (03/01); todas sobem 10% e depois caem para 10,45.
    cotacoes = _cotacoes(tickers, [(dt.date(2025, 1, 2), 10.0),
                                   (dt.date(2025, 1, 3), 10.0),
                                   (dt.date(2025, 1, 6), 11.0),
                                   (dt.date(2025, 1, 7), 10.45)])
    atualizar_fundo_mensal(tmp_path, as_of='2025-01-07', cotacoes=cotacoes,
                           provedor_series=_series)
    destino = tmp_path / 'fundo-ficticio.json'
    exportar_resumo(tmp_path, destino)
    resumo = json.loads(destino.read_text(encoding='utf-8'))

    assert resumo['status'] == 'nav_nocional_calculado'
    s = resumo['summary']
    assert s['firstExecution'] == '2025-01-03' and s['lastSession'] == '2025-01-07'
    assert abs(s['totalReturn'] - 0.045) < 1e-12
    assert abs(s['maxDrawdown'] - (10.45 / 11 - 1)) < 1e-12
    assert abs(s['equity'] - 10_450) < 1e-6 and s['contributed'] == 10_000
    assert resumo['decisions'][0]['executionDate'] == '2025-01-03'
    assert [p['date'] for p in resumo['series']] == ['2025-01-03', '2025-01-06', '2025-01-07']
