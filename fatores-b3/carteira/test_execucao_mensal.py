"""Conclusão nocional de um alvo mensal publicado com D+1 observado."""

import datetime as dt
import hashlib
import json

import pandas as pd

from carteira.execucao_mensal import calcular_fundo_mensal, atualizar_fundo_mensal


def _publicar(tmp_path):
    caminho = tmp_path / 'agenda-2025-01-20250102T180000-exemplo.json'
    tickers = [f'Q{i:03d}3' for i in range(10)]
    bruto = pd.DataFrame({'data_decisao': [dt.date(2025, 1, 2)] * 10,
                          'data_execucao': [None] * 10,
                          'ticker': tickers, 'peso': [.1] * 10}).to_csv(index=False).encode()
    caminho.with_name(caminho.stem + '-alvo_pendente.csv').write_bytes(bruto)
    caminho.write_text(json.dumps({
        'status': 'selecao_tecnica_validada_para_treino',
        'data_decisao': '2025-01-02', 'aporte_ficticio': 10_000.0,
        'alvo_sha256': hashlib.sha256(bruto).hexdigest(),
    }), encoding='utf-8')
    return tickers


def test_d1_observado_gera_nav_e_dez_operacoes_nocionais(tmp_path):
    tickers = _publicar(tmp_path)
    dias = [dt.date(2025, 1, 2), dt.date(2025, 1, 3),
            dt.date(2025, 1, 6)]
    bruto = pd.DataFrame([{'data': dia, 'ticker': ticker,
                           'fechamento': 10.0, 'volume_negociado': 1_000_000}
                          for dia in dias for ticker in tickers])

    def series(nomes, inicio, fim, as_of, cotacoes):
        pedacos = cotacoes[(pd.to_datetime(cotacoes['data']).dt.date >= inicio) &
                           (pd.to_datetime(cotacoes['data']).dt.date <= fim) &
                           cotacoes['ticker'].isin(nomes)]
        return pedacos.rename(columns={'fechamento': 'preco_bruto'}).assign(
            preco_fechamento_ajustado=10.0)[
                ['data', 'ticker', 'preco_bruto', 'preco_fechamento_ajustado']]

    fundo = calcular_fundo_mensal(tmp_path, as_of='2025-01-06',
                                  cotacoes=bruto, provedor_series=series)
    assert fundo['status'] == 'nav_nocional_calculado'
    assert fundo['primeira_execucao'] == '2025-01-03'
    assert fundo['patrimonio']['patrimonio_ficticio'].tolist() == [10_000, 10_000]
    assert len(fundo['operacoes_nocionais']) == 10
    assert fundo['operacoes_nocionais']['financeiro_nocional'].sum() == 10_000


def test_sem_d1_mantem_alvo_pendente_e_nao_grava_nav(tmp_path):
    tickers = _publicar(tmp_path)
    bruto = pd.DataFrame([{'data': dt.date(2025, 1, 2), 'ticker': ticker,
                           'fechamento': 10.0, 'volume_negociado': 1_000_000}
                          for ticker in tickers])
    estado = atualizar_fundo_mensal(tmp_path, as_of='2025-01-02', cotacoes=bruto)
    assert estado['status'] == 'pendente_D+1'
    assert not list(tmp_path.glob('fundo-*-indice.parquet'))


def test_alvo_alterado_apos_publicacao_bloqueia_nav(tmp_path):
    _publicar(tmp_path)
    csv = next(tmp_path.glob('agenda-*-alvo_pendente.csv'))
    csv.write_bytes(csv.read_bytes().replace(b'0.1', b'0.2', 1))
    estado = atualizar_fundo_mensal(tmp_path, as_of='2025-01-03',
                                     cotacoes=pd.DataFrame([{
                                         'data': dt.date(2025, 1, 3),
                                         'ticker': 'Q0003', 'fechamento': 10.0,
                                         'volume_negociado': 1_000_000}]))
    assert estado['status'] == 'nav_bloqueado'
    assert 'hash valido' in estado['erro']
    assert not list(tmp_path.glob('fundo-*-indice.parquet'))
