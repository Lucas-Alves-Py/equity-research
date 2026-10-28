"""Resumo do fundo fictício lido pelo painel."""

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


def test_resumo_traz_comentario_ranking_universo_e_posicoes(tmp_path, monkeypatch):
    from carteira import resumo_fundo
    tickers = _publicar(tmp_path)
    registro = sorted(tmp_path.glob('agenda-????-??-*.json'))[0]
    linhas = [{'ticker': t, 'sector': 'Setor', 'evEbit': 5.0, 'roic': 0.2, 'momentum': 0.3,
               'rankEvEbit': i, 'rankRoic': i, 'rankMomentum': i, 'score': 3 * i,
               'position': i, 'selected': i <= 10}
              for i, t in enumerate(tickers + ['FILA3', 'FILA4', 'FILA5', 'FILA6', 'FILA7', 'FORA3'], 1)]
    registro.with_name(f'{registro.stem}-universo.json').write_text(
        json.dumps({'signalDate': '2025-01-02', 'assets': 10, 'eligible': len(linhas), 'rows': linhas}),
        encoding='utf-8')
    data = json.loads(registro.read_text(encoding='utf-8'))['data_decisao']
    comentarios = tmp_path / 'comentarios.yaml'
    comentarios.write_text(f'{data}: Primeira decisao.\n', encoding='utf-8')
    monkeypatch.setattr(resumo_fundo, 'COMENTARIOS', comentarios)
    cotacoes = _cotacoes(tickers, [(dt.date(2025, 1, 2), 10.0), (dt.date(2025, 1, 3), 10.0),
                                   (dt.date(2025, 1, 6), 11.0)])
    atualizar_fundo_mensal(tmp_path, as_of='2025-01-06', cotacoes=cotacoes, provedor_series=_series)

    resumo = resumir_fundo(tmp_path)
    decisao = resumo['decisions'][0]
    assert decisao['comment'] == 'Primeira decisao.'
    assert len(decisao['ranking']) == 15 and decisao['coverage']['eligibleCompanies'] == 16
    assert len(resumo['universe']['rows']) == 16
    assert len(resumo['holdings']) == 10
    assert abs(sum(h['contribution'] for h in resumo['holdings']) - 0.10) < 1e-12
