import pandas as pd

from carteira.painel_precos import painel_precos, series_ajustadas
from fontes import b3


def _cotacoes(volume=1_000_000):
    datas = pd.bdate_range('2025-09-01', periods=253)
    # Desdobramento 3:1 após o 101º pregão, sem retorno econômico.
    return pd.DataFrame({'data': datas, 'ticker': 'DIRR3',
                         'fechamento': [30.0] * 101 + [10.0] * 152,
                         'volume_negociado': volume})


def _eventos(aprovado_em):
    caixa = pd.DataFrame(columns=['ultimo_com', 'aprovado_em', 'classe', 'tipo',
                                  'valor', 'preco_anterior', 'pct'])
    acoes = pd.DataFrame([{'ultimo_com': _cotacoes().iloc[100]['data'],
                           'aprovado_em': aprovado_em, 'isin': 'BRDIRRACNOR0',
                           'tipo': 'DESDOBRAMENTO', 'fator_b3': 200.0}])
    return ({'issuingCompany': 'DIRR', 'tradingName': 'DIRR'}, caixa, acoes)


def test_momentum_252_sessoes_corrige_split_conhecido_antes_do_sinal(tmp_path, monkeypatch):
    from carteira import painel_precos as modulo

    monkeypatch.setattr(modulo, 'EVENTOS_SNAPSHOTS', tmp_path)
    cot = _cotacoes()
    data = cot['data'].max()
    painel = painel_precos(data, cotacoes=cot,
                           eventos_por_emissor={'DIRR': _eventos(data - pd.Timedelta(days=30))})
    assert painel.iloc[0]['status_eventos'] == 'ok'
    assert abs(painel.iloc[0]['momento_12_meses']) < 1e-12
    assert painel.iloc[0]['preco_bruto'] == 10.0
    assert painel.iloc[0]['volume'] == 1_000_000
    assert painel.iloc[0]['eventos_acoes'] == 1
    assert (tmp_path / f"{painel.attrs['eventos_sha256_por_emissor']['DIRR']}.json").exists()


def test_evento_sem_aprovacao_bloqueia_momentum():
    cot = _cotacoes()
    painel = painel_precos(cot['data'].max(), cotacoes=cot,
                           eventos_por_emissor={'DIRR': _eventos(pd.NaT)},
                           guardar_snapshots=False)
    assert painel.iloc[0]['status_eventos'] == 'falha_ajuste'
    assert pd.isna(painel.iloc[0]['momento_12_meses'])


def test_baixa_liquidez_nao_consulta_eventos(monkeypatch):
    monkeypatch.setattr('carteira.painel_precos._eventos_emissor',
                        lambda _: (_ for _ in ()).throw(AssertionError('consulta indevida')))
    painel = painel_precos(_cotacoes()['data'].max(), cotacoes=_cotacoes(volume=100),
                           guardar_snapshots=False)
    assert painel.iloc[0]['status_eventos'] == 'baixa_liquidez'


def test_numero_json_decimal_nao_vira_milhar():
    assert b3._num(pd.Series([0.123, '0,123', '1.234,56', '2.417.135.038'])).tolist() == [0.123, 0.123, 1234.56, 2417135038]


def test_dividendo_e_jcp_mesmo_ex_somam_antes_do_ajuste():
    prov = pd.DataFrame([
        {'ultimo_com': pd.Timestamp('2026-09-24'), 'aprovado_em': pd.Timestamp('2026-09-23'),
         'classe': 'PN', 'tipo': 'DIVIDENDO', 'pct': 2.0},
        {'ultimo_com': pd.Timestamp('2026-09-24'), 'aprovado_em': pd.Timestamp('2026-09-23'),
         'classe': 'PN', 'tipo': 'JRS CAP PROPRIO', 'pct': 3.0},
    ])
    fator = b3.fatores(prov, 'PETR4', as_of='2026-09-25')
    assert fator.iloc[0]['fator'] == 1 - (2.0 + 3.0 * 0.85) / 100


def test_tipo_de_provento_desconhecido_nao_vira_dividendo():
    prov = pd.DataFrame([{'ultimo_com': pd.Timestamp('2026-09-24'),
                          'aprovado_em': pd.Timestamp('2026-09-23'),
                          'classe': 'PN', 'tipo': '', 'pct': 2.0}])
    import pytest
    with pytest.raises(ValueError, match='tipo de provento B3 não suportado'):
        b3.fatores(prov, 'PETR4', as_of='2026-09-25')


def test_series_diarias_ajustam_split_e_jcp_e_guardam_snapshot(tmp_path, monkeypatch):
    import hashlib
    import json
    from carteira import painel_precos as modulo

    monkeypatch.setattr(modulo, 'EVENTOS_SNAPSHOTS', tmp_path)
    datas = pd.bdate_range('2026-09-21', periods=4)
    cot = pd.DataFrame({'data': datas, 'ticker': 'DIRR3',
                        'fechamento': [30.0, 30.0, 10.0, 10.0],
                        'volume_negociado': 1_000_000})
    caixa = pd.DataFrame([{'ultimo_com': datas[1], 'aprovado_em': datas[0],
                           'classe': 'ON', 'tipo': 'JRS CAP PROPRIO', 'pct': 3.0}])
    acoes = pd.DataFrame([{'ultimo_com': datas[1], 'aprovado_em': datas[0],
                           'isin': 'BRDIRRACNOR0', 'tipo': 'DESDOBRAMENTO',
                           'fator_b3': 200.0}])
    capturas = {'DIRR': ({'issuingCompany': 'DIRR', 'tradingName': 'DIRR'}, caixa, acoes)}
    serie = series_ajustadas(['DIRR3'], datas[0], datas[-1], datas[-1],
                              cotacoes=cot, eventos_por_emissor=capturas)
    assert list(serie.columns) == ['data', 'ticker', 'preco_bruto',
                                   'preco_fechamento_ajustado']
    assert len(serie) == 4
    assert abs(serie.iloc[0]['preco_fechamento_ajustado'] - 30 * (1 - .03 * .85) / 3) < 1e-12
    assert serie.iloc[-1]['preco_fechamento_ajustado'] == 10.0
    digest = serie.attrs['eventos_sha256_por_emissor']['DIRR']
    bruto = (tmp_path / f'{digest}.json').read_bytes()
    assert hashlib.sha256(bruto).hexdigest() == digest
    assert json.loads(bruto)['prefixo'] == 'DIRR'


def test_series_diarias_bloqueiam_sessao_sem_preco():
    import pytest
    datas = pd.bdate_range('2026-09-21', periods=4)
    cot = pd.DataFrame({'data': list(datas) + list(datas[:-1]),
                        'ticker': ['REF3'] * 4 + ['DIRR3'] * 3,
                        'fechamento': [10.] * 7,
                        'volume_negociado': [1000.] * 7})
    with pytest.raises(ValueError, match='pregões sem preço'):
        series_ajustadas(['DIRR3'], datas[0], datas[-1], datas[-1], cotacoes=cot,
                         guardar_snapshots=False)


def test_series_diarias_bloqueiam_evento_aprovado_apos_corte():
    import pytest
    datas = pd.bdate_range('2026-09-21', periods=4)
    cot = pd.DataFrame({'data': datas, 'ticker': 'DIRR3',
                        'fechamento': [30., 30., 10., 10.],
                        'volume_negociado': [1000.] * 4})
    caixa = pd.DataFrame(columns=['ultimo_com', 'aprovado_em', 'classe', 'tipo', 'pct'])
    acoes = pd.DataFrame([{'ultimo_com': datas[1], 'aprovado_em': datas[-1],
                           'isin': 'BRDIRRACNOR0', 'tipo': 'DESDOBRAMENTO',
                           'fator_b3': 200.0}])
    with pytest.raises(ValueError, match='sem aprovação anterior'):
        series_ajustadas(['DIRR3'], datas[0], datas[-1], datas[-1], cotacoes=cot,
                         eventos_por_emissor={'DIRR': ({'issuingCompany': 'DIRR'}, caixa, acoes)},
                         guardar_snapshots=False)
