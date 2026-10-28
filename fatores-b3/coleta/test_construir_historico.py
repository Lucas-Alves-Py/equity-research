import pandas as pd

from coleta import construir_historico as ch


def test_deslistada_com_salto_sem_evento_perde_o_momentum(monkeypatch):
    # Para quem saiu da bolsa a B3 só devolve proventos em dinheiro. Um
    # desdobramento não registrado viraria retorno falso; o momentum sai.
    monkeypatch.setattr(ch, 'SEM_EVENTOS_EM_ACOES', {'LAME', 'HGTX', 'KEPL'})
    datas = pd.bdate_range('2020-01-01', periods=253)
    # KEPL3 tem o mesmo salto, mas explicado por um desdobramento inferido da CVM.
    monkeypatch.setattr(ch, '_inferidos', lambda: pd.DataFrame({'ticker': ['KEPL3'], 'ex': [datas[100]]}))
    cot = pd.concat([
        pd.DataFrame({'data': datas, 'ticker': 'LAME4', 'fechamento': [30.0] * 100 + [10.0] * 153,
                      'volume_negociado': 1e7}),
        pd.DataFrame({'data': datas, 'ticker': 'HGTX3', 'fechamento': 20.0, 'volume_negociado': 1e7}),
        pd.DataFrame({'data': datas, 'ticker': 'PETR4', 'fechamento': [30.0] * 100 + [10.0] * 153,
                      'volume_negociado': 1e7}),
        pd.DataFrame({'data': datas, 'ticker': 'KEPL3', 'fechamento': [30.0] * 100 + [10.0] * 153,
                      'volume_negociado': 1e7}),
    ])
    precos = pd.DataFrame({'ticker': ['LAME4', 'HGTX3', 'PETR4', 'KEPL3'], 'momento_12_meses': [-0.6, 0.0, 0.0, 0.0],
                           'status_eventos': 'ok'})
    saida = ch._bloquear_saltos_sem_evento(precos, cot).set_index('ticker')
    assert pd.isna(saida.loc['LAME4', 'momento_12_meses'])
    assert saida.loc['LAME4', 'status_eventos'] == 'salto_sem_evento_em_acoes'
    assert saida.loc['HGTX3', 'momento_12_meses'] == 0.0
    assert saida.loc['PETR4', 'momento_12_meses'] == 0.0  # tem eventos da B3: não é bloqueada
    assert saida.loc['KEPL3', 'momento_12_meses'] == 0.0


def test_evento_inferido_completa_a_b3_sem_duplicar(monkeypatch):
    # KEPL3: a B3 tem o desdobramento de 04/2023, não o de 05/2022.
    vazio = pd.DataFrame(columns=['ultimo_com', 'aprovado_em', 'isin', 'tipo', 'fator_b3'])
    b3 = pd.DataFrame([{'ultimo_com': pd.Timestamp('2023-04-03'), 'aprovado_em': pd.Timestamp('2023-04-03'),
                        'isin': 'BRKEPLACNOR1', 'tipo': 'DESDOBRAMENTO', 'fator_b3': 100.0}])
    inferidos = pd.DataFrame({'ticker': 'KEPL3', 'ultimo_com': pd.to_datetime(['2022-05-05', '2023-04-03']),
                              'aprovado_em': pd.to_datetime(['2022-05-05', '2023-04-03']),
                              'tipo': 'DESDOBRAMENTO', 'fator_b3': [200.0, 100.0]})
    monkeypatch.setattr(ch, '_inferidos', lambda: inferidos)
    _, _, acoes = ch._completar('KEPL', ({}, None, b3))
    assert acoes['ultimo_com'].tolist() == [pd.Timestamp('2022-05-05'), pd.Timestamp('2023-04-03')]
    _, _, acoes = ch._completar('KEPL', ({}, None, vazio))  # deslistada: a B3 não devolve nada
    assert len(acoes) == 2
