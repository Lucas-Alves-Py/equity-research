"""Contratos mínimos de seleção atual e agenda fictícia."""

import datetime as dt

import pandas as pd
import pytest

from carteira.agenda import agenda_mensal, patrimonio_com_aportes
from carteira.carteira import (CarteiraSistematica, precos_execucao, selecionar,
                              validar_painel_atual)
from carteira.pipeline_atual import montar_painel_atual, rodar_se_devido


IND = {'EV_EBIT': 'crescente', 'ROIC': 'decrescente',
       'momento_12_meses': 'decrescente'}


def test_ranking_desempata_dez_sem_depender_da_ordem_de_entrada():
    df = pd.DataFrame([{'data': dt.date(2026, 9, 25),
                        'ticker': f'Q{i:03d}3', 'volume': 600_000,
                        'preco_fechamento_ajustado': 10,
                        'EV_EBIT': 5, 'ROIC': .2, 'momento_12_meses': .1}
                       for i in range(12)])
    a = selecionar(df, dt.date(2026, 9, 25), IND, 500_000, 10)
    b = selecionar(df.sample(frac=1, random_state=42),
                   dt.date(2026, 9, 25), IND, 500_000, 10)
    assert len(a) == 10
    assert a['ticker'].tolist() == b['ticker'].tolist()
    assert a['peso'].sum() == pytest.approx(1)


def test_painel_legado_velho_nao_vira_selecao_atual():
    df = pd.DataFrame([{'data': dt.date(2025, 11, 3),
                        'ticker': f'Q{i:03d}3', 'volume': 600_000,
                        'preco_fechamento_ajustado': 10,
                        'EV_EBIT': 5, 'ROIC': .2, 'momento_12_meses': .1,
                        'fonte_preco': 'B3', 'fonte_fundamentos': 'CVM'}
                       for i in range(30)])
    with pytest.raises(ValueError, match='desatualizado'):
        validar_painel_atual(df, IND, 500_000, 10,
                            referencia=dt.date(2026, 9, 28))


def test_uniao_rejeita_capital_aprovado_depois_do_sinal():
    sinal = dt.date(2026, 9, 25)
    p = pd.DataFrame([{'data': sinal, 'ticker': 'ABCD3', 'preco_bruto': 10,
                       'preco_fechamento_ajustado': 10, 'volume': 600_000,
                       'momento_12_meses': .1, 'status_eventos': 'ok',
                       'fonte_preco': 'B3', 'fonte_eventos': 'B3'}])
    f = pd.DataFrame([{'cnpj': '1', 'ebit_ttm': 100, 'ROIC': .2,
                       'caixa': 10, 'divida_bruta': 20,
                       'dt_receb_dre': dt.date(2026, 8, 1),
                       'dt_receb_balanco': dt.date(2026, 8, 1)}])
    m = pd.DataFrame([{'cnpj': '1', 'ticker': 'ABCD3',
                       'inicio': dt.date(2020, 1, 1), 'fim': None}])
    c = pd.DataFrame([{'cnpj': '1', 'valor_mercado': 1_000,
                       'data_aprovacao': dt.date(2026, 9, 26),
                       'cobertura_classes': True, 'fonte_capital': 'B3'}])
    with pytest.raises(ValueError, match='data_aprovacao posterior'):
        montar_painel_atual(p, f, m, c, dt.date(2026, 9, 28))


def test_aporte_dia_cinco_executa_d1_e_nao_altera_indice():
    sessoes = [dt.date(2026, 1, 5), dt.date(2026, 1, 6),
               dt.date(2026, 1, 7)]
    agenda = agenda_mensal(sessoes, '2026-01-01', '2026-01-31')
    assert agenda.iloc[0]['data_decisao'] == sessoes[0]
    assert agenda.iloc[0]['data_execucao'] == sessoes[1]
    indice = pd.DataFrame({'data': sessoes, 'indice': [100, 110, 121]})
    serie = patrimonio_com_aportes(indice, agenda)
    assert serie['indice'].tolist() == [100, 110, 121]
    assert serie['aporte_ficticio'].tolist() == [0, 10_000, 0]
    assert serie['patrimonio_ficticio'].iloc[-1] == pytest.approx(11_000)


def test_decisao_no_ultimo_pregao_fica_pendente_sem_aporte():
    agenda = agenda_mensal([dt.date(2026, 10, 5)],
                           '2026-10-01', '2026-10-31')
    assert agenda.iloc[0]['status_execucao'] == 'pendente'
    assert pd.isna(agenda.iloc[0]['data_execucao'])
    indice = pd.DataFrame({'data': [dt.date(2026, 10, 5)], 'indice': [100]})
    serie = patrimonio_com_aportes(indice, agenda)
    assert serie['total_aportado'].iloc[0] == 0


def test_rotina_mensal_so_roda_no_pregao_devido_e_uma_vez(tmp_path):
    chamadas = []

    def processar(instante, anos):
        chamadas.append(instante)
        painel = pd.DataFrame({'ticker': ['ABCD3']})
        alvo = pd.DataFrame({'data': [dt.date(2026, 10, 5)],
                             'ticker': ['ABCD3'], 'peso': [1.0]})
        return painel, alvo

    instante = pd.Timestamp('2026-10-05 19:00', tz='America/Sao_Paulo')
    assert rodar_se_devido(pd.Timestamp('2026-10-02 19:00',
                                       tz='America/Sao_Paulo'),
                          [dt.date(2026, 10, 2)], [2024, 2025, 2026],
                          tmp_path, processar=processar)['status'] == 'nao_devido'
    primeira = rodar_se_devido(instante, [dt.date(2026, 10, 2),
                                         dt.date(2026, 10, 5)],
                              [2024, 2025, 2026], tmp_path, processar=processar)
    segunda = rodar_se_devido(instante, [dt.date(2026, 10, 5)],
                              [2024, 2025, 2026], tmp_path, processar=processar)
    assert primeira['status'] == 'selecao_tecnica_validada_para_treino'
    assert segunda['status'] == 'ja_processado'
    assert len(chamadas) == 1


def test_rotina_mensal_recupera_cotahist_atrasado_sem_retroagir(tmp_path):
    chamadas = []

    def processar(instante, anos):
        chamadas.append(instante.date())
        painel = pd.DataFrame({'ticker': ['ABCD3']})
        alvo = pd.DataFrame({'data': [dt.date(2026, 10, 5)],
                             'ticker': ['ABCD3'], 'peso': [1.0]})
        return painel, alvo

    sexta = pd.Timestamp('2026-10-06 18:00', tz='America/Sao_Paulo')
    resultado = rodar_se_devido(sexta, [dt.date(2026, 10, 5)],
                                [2024, 2025, 2026], tmp_path,
                                processar=processar)
    assert resultado['data_giro_planejada'] == '2026-10-05'
    assert resultado['data_decisao'] == '2026-10-06'
    assert chamadas == [dt.date(2026, 10, 6)]


def test_setembro_nao_faz_catchup_antes_do_inicio_do_mandato(tmp_path):
    resultado = rodar_se_devido(
        pd.Timestamp('2026-09-28 19:00', tz='America/Sao_Paulo'),
        [dt.date(2026, 9, 4), dt.date(2026, 9, 8),
         dt.date(2026, 9, 25)], [2024, 2025, 2026], tmp_path)
    assert resultado['status'] == 'nao_devido_antes_do_mandato'


def test_bloqueio_no_dia_de_giro_pode_reprocessar_no_dia_seguinte(tmp_path):
    chamadas = []

    def processar(instante, anos):
        chamadas.append(instante.date())
        if len(chamadas) == 1:
            raise ValueError('fonte indisponivel')
        return (pd.DataFrame({'ticker': ['ABCD3']}),
                pd.DataFrame({'data': [dt.date(2026, 10, 5)],
                              'ticker': ['ABCD3'], 'peso': [1.0]}))

    primeira = rodar_se_devido(
        pd.Timestamp('2026-10-05 19:00', tz='America/Sao_Paulo'),
        [dt.date(2026, 10, 5)], [2024, 2025, 2026], tmp_path,
        processar=processar)
    segunda = rodar_se_devido(
        pd.Timestamp('2026-10-06 19:00', tz='America/Sao_Paulo'),
        [dt.date(2026, 10, 5), dt.date(2026, 10, 6)],
        [2024, 2025, 2026], tmp_path, processar=processar)
    assert primeira['status'] == 'bloqueado'
    assert segunda['status'] == 'selecao_tecnica_validada_para_treino'
    assert chamadas == [dt.date(2026, 10, 5), dt.date(2026, 10, 6)]


def test_execucao_d1_preserva_pendente_e_acrescenta_versao(tmp_path):
    decisao, d1 = dt.date(2025, 1, 2), dt.date(2025, 1, 3)
    tickers = [f'Q{i:03d}3' for i in range(10)]
    cotacoes = pd.DataFrame(
        [{'data': d, 'ticker': ticker, 'fechamento': 10.0}
         for d in (decisao, d1) for ticker in tickers])
    data_exec, precos = precos_execucao(tickers, decisao, cotacoes)
    assert data_exec == d1
    assert len(precos) == 10

    carteira = CarteiraSistematica()
    carteira.ledger_dir = str(tmp_path)
    pendente = pd.DataFrame(
        [{'modelo': carteira.modelo['id'], 'data_decisao': decisao,
          'data_sinal': decisao, 'data_execucao': None,
          'ticker': ticker, 'versao': 1, 'status_execucao': 'PENDENTE',
          'capital_referencia': 10_000.0, 'peso_alvo': .1,
          'posicao_ranking': i + 1, 'preco_sinal': 10.0,
          'quantidade': None, 'preco_execucao': None, 'financeiro': None}
         for i, ticker in enumerate(tickers)])
    pendente.to_parquet(carteira._caminho('carteiras'), index=False)
    executada = carteira.concluir_execucao_pendente(decisao, cotacoes)
    guardado = pd.read_parquet(carteira._caminho('carteiras'))
    assert len(guardado) == 20
    assert guardado['status_execucao'].value_counts().to_dict() == {
        'PENDENTE': 10, 'EXECUTADA': 10}
    assert executada['quantidade'].eq(100).all()
    assert executada['data_execucao'].eq(d1).all()
    with pytest.raises(ValueError, match='ja executada'):
        carteira.concluir_execucao_pendente(decisao, cotacoes)


def test_bancos_saem_do_universo_e_ficam_registrados():
    from carteira.pipeline_atual import excluir_setores
    master = pd.DataFrame({'ticker': ['BBAS3', 'ITSA4', 'WIZC3', 'PETR4'],
                           'cnpj': ['bb', 'itsa', 'wiz', 'petro']})
    cadastro = pd.DataFrame({'cnpj': ['bb', 'itsa', 'wiz', 'petro'],
                             'setor': ['Bancos', 'Emp. Adm. Part. - Intermediação Financeira',
                                       'Seguradoras e Corretoras', 'Petróleo e Gás']})
    restante, excluidos = excluir_setores(master, cadastro)
    assert excluidos == ['BBAS3']
    # Holding e corretora ficam: o backtest os tinha com EV/EBIT e ROIC.
    assert restante['ticker'].tolist() == ['ITSA4', 'WIZC3', 'PETR4']


def test_capital_aprovado_no_sinal_vai_para_quarentena_sem_abortar(monkeypatch):
    # 02/10/2026: RCSL3 teve capital aprovado no proprio dia do sinal e,
    # antes, abortava o painel inteiro em vez de ficar so ela de fora.
    import fontes.capital_b3
    from carteira.pipeline_atual import capitalizacao_b3
    capital = pd.DataFrame({
        'prefixo': ['PETR', 'RCSL'], 'acoes_on': [100, 50], 'acoes_pn': [0, 0],
        'data_aprovacao': ['2026-01-10', '2026-10-02'], 'elegivel_pit': [True, True],
        'fonte': ['B3', 'B3'], 'sha256': ['a', 'b'],
        'capturado_em_utc': ['2026-10-02T23:00:00Z'] * 2})
    monkeypatch.setattr(fontes.capital_b3, 'capital_social_b3', lambda instante: capital)
    precos = pd.DataFrame({'ticker': ['PETR3', 'RCSL3'], 'preco_bruto': [30.0, 2.0]})
    master = pd.DataFrame({'cnpj': ['petro', 'rcsl'], 'ticker': ['PETR3', 'RCSL3'],
                           'inicio': [None, None], 'fim': [None, None]})
    r = capitalizacao_b3(precos, master, [2026], dt.date(2026, 10, 2),
                         pd.Timestamp('2026-10-02 21:30', tz='America/Sao_Paulo'))
    assert r['cnpj'].tolist() == ['petro']
    assert {'prefixo': 'RCSL', 'motivo': 'capital_aprovado_no_sinal_ou_depois'} in \
        r.attrs['quarentena_capital']


def test_ticker_liquido_sem_cnpj_vigente_no_fca_e_ligado_pela_b3():
    from carteira.pipeline_atual import completar_master_b3
    # C&A declarou fim de negociacao em 2019; CSN declarou "4030" no FCA.
    master = pd.DataFrame({'cnpj': ['cea', 'petro'], 'ticker': ['CEAB3', 'PETR4'],
                           'inicio': ['2019-10-28', None], 'fim': ['2019-10-28', None]})
    precos = pd.DataFrame({'ticker': ['CEAB3', 'CSNA3', 'PETR4', 'ILIQ3'],
                           'volume': [9e6, 9e7, 9e8, 1e3]})
    precos = pd.concat([precos, pd.DataFrame({'ticker': ['CMIN3'], 'volume': [5e7]})])
    b3 = {'CEAB3': {'cnpj': '45242914000105'}, 'CSNA3': {'cnpj': '33042730000104'},
          'CMIN3': {'cnpj': '8902291000115'}}  # B3 omite o zero à esquerda
    m, ligados = completar_master_b3(master, precos, '2026-10-02', b3.get)
    assert ligados == ['CEAB3', 'CMIN3', 'CSNA3']
    assert '08.902.291/0001-15' in set(m.loc[m['ticker'].eq('CMIN3'), 'cnpj'])
    assert '33.042.730/0001-04' in set(m.loc[m['ticker'].eq('CSNA3'), 'cnpj'])
    assert 'ILIQ3' not in set(m['ticker'])


def test_valor_de_mercado_com_classe_sem_preco_no_dia(monkeypatch):
    import fontes.capital_b3
    from carteira.pipeline_atual import capitalizacao_b3
    capital = pd.DataFrame({
        'prefixo': ['VALE', 'DEXP', 'SHUL', 'CEBR'],
        'acoes_on': [4_439_159_752, 116_481_187, 152_692_764, 35_920_890],
        'acoes_pn': [12, 6_493_280, 204_682_016, 36_161_025],
        'data_aprovacao': ['2026-01-10'] * 4, 'elegivel_pit': [True] * 4,
        'fonte': ['B3'] * 4, 'sha256': list('abcd'),
        'capturado_em_utc': ['2026-10-02T23:00:00Z'] * 4})
    monkeypatch.setattr(fontes.capital_b3, 'capital_social_b3', lambda instante: capital)
    precos = pd.DataFrame({'ticker': ['VALE3', 'DEXP3', 'SHUL4', 'CEBR3', 'CEBR5', 'CEBR6'],
                           'preco_bruto': [72.1, 7.56, 4.70, 23.99, 23.5, 27.5]})
    master = pd.DataFrame({'cnpj': ['vale', 'dexp', 'shul', 'cebr'],
                           'ticker': ['VALE3', 'DEXP3', 'SHUL4', 'CEBR3'],
                           'inicio': [None] * 4, 'fim': [None] * 4})
    recentes = pd.Series({'DEXP4': 7.30})  # DEXP4 negociou em 01/10, nao no sinal
    r = capitalizacao_b3(precos, master, [2026], dt.date(2026, 10, 2),
                         pd.Timestamp('2026-10-02 21:30', tz='America/Sao_Paulo'),
                         fechamentos_recentes=recentes).set_index('cnpj')
    # Golden share (12 acoes PN) vale zero; nao bloqueia a Vale.
    assert r.loc['vale', 'valor_mercado'] == 4_439_159_752 * 72.1
    assert r.loc['dexp', 'valor_mercado'] == 116_481_187 * 7.56 + 6_493_280 * 7.30
    # ON da Schulz nao negocia na bolsa: recebe o preco da PN.
    assert r.loc['shul', 'valor_mercado'] == (152_692_764 + 204_682_016) * 4.70
    assert 'cebr' not in r.index  # duas series PN com precos diferentes
    regras = {x['prefixo']: x['regras'] for x in r.attrs['precificacao_capital']}
    assert regras['VALE'] == ['pn:classe_irrelevante_sem_preco']
    assert regras['SHUL'] == ['on:preco_da_classe_pn']


def test_valor_de_mercado_pela_unit_e_com_duas_series_pn_parecidas(monkeypatch):
    # TAEE só negocia pela unit; a razão PN/unit do último pregão em comum dá
    # o preço da PN. USIM5 e USIM6 valem quase o mesmo: usa a mais líquida.
    import fontes.capital_b3
    import carteira.pipeline_atual as pa
    capital = pd.DataFrame({
        'prefixo': ['TAEE', 'USIM'], 'acoes_on': [100, 500], 'acoes_pn': [200, 300],
        'data_aprovacao': ['2026-01-10'] * 2, 'elegivel_pit': [True] * 2,
        'fonte': ['B3'] * 2, 'sha256': list('ab'), 'capturado_em_utc': ['2026-10-02T23:00:00Z'] * 2})
    monkeypatch.setattr(fontes.capital_b3, 'capital_social_b3', lambda instante: capital)
    monkeypatch.setattr(pa, '_razoes_pela_unit', lambda sinal: pd.Series({'TAEE3': 0.30, 'TAEE4': 0.35}))
    precos = pd.DataFrame({'ticker': ['TAEE11', 'USIM3', 'USIM5', 'USIM6'],
                           'preco_bruto': [36.0, 7.0, 6.0, 6.5], 'volume': [9e7, 1e6, 9e7, 1e6]})
    master = pd.DataFrame({'cnpj': ['taee', 'usim'], 'ticker': ['TAEE11', 'USIM5'],
                           'inicio': [None] * 2, 'fim': [None] * 2})
    r = pa.capitalizacao_b3(precos, master, [2026], dt.date(2026, 10, 2),
                            pd.Timestamp('2026-10-02 21:30', tz='America/Sao_Paulo'),
                            fechamentos_recentes=pd.Series(dtype=float)).set_index('cnpj')
    assert r.loc['taee', 'valor_mercado'] == pytest.approx(100 * 36.0 * 0.30 + 200 * 36.0 * 0.35)
    assert r.loc['usim', 'valor_mercado'] == 500 * 7.0 + 300 * 6.0
    regras = {x['prefixo']: x['regras'] for x in r.attrs['precificacao_capital']}
    assert regras['TAEE'] == ['on:pela_unit', 'pn:pela_unit']
    assert regras['USIM'] == ['pn:serie_mais_liquida_de_2']


def test_on_da_azul_vale_um_75_avos_da_pn(monkeypatch):
    import fontes.capital_b3
    from carteira.pipeline_atual import capitalizacao_b3
    capital = pd.DataFrame({'prefixo': ['AZUL'], 'acoes_on': [928_965_058], 'acoes_pn': [327_175_146],
                            'data_aprovacao': ['2019-01-10'], 'elegivel_pit': [True], 'fonte': ['CVM'],
                            'sha256': ['a'], 'capturado_em_utc': ['2019-12-05T23:00:00Z']})
    monkeypatch.setattr(fontes.capital_b3, 'capital_social_b3', lambda instante: capital)
    precos = pd.DataFrame({'ticker': ['AZUL4'], 'preco_bruto': [53.41]})
    master = pd.DataFrame({'cnpj': ['azul'], 'ticker': ['AZUL4'], 'inicio': [None], 'fim': [None]})
    r = capitalizacao_b3(precos, master, [2019], dt.date(2019, 12, 5),
                         pd.Timestamp('2019-12-05 21:30', tz='America/Sao_Paulo'),
                         fechamentos_recentes=pd.Series(dtype=float)).set_index('cnpj')
    assert r.loc['azul', 'valor_mercado'] == pytest.approx((928_965_058 / 75 + 327_175_146) * 53.41)


def test_pdf_da_decisao_nao_bloqueia_registro(monkeypatch, tmp_path):
    import carteira.relatorio_carteira as relatorio
    from carteira.pipeline_atual import relatorio_da_decisao
    assert relatorio_da_decisao({'status': 'bloqueado'}) is None

    def falha(registro, saida):
        raise RuntimeError('sem cadastro da CVM')
    monkeypatch.setattr(relatorio, 'gerar', falha)
    r = relatorio_da_decisao({'status': 'selecao_tecnica_validada_para_treino',
                              'arquivo': str(tmp_path / 'agenda-2026-10-x.json')})
    assert r == 'falhou: RuntimeError: sem cadastro da CVM'


def test_painel_diario_grava_um_arquivo_por_pregao(tmp_path):
    import json
    from carteira.pipeline_atual import registrar_painel_diario
    chamadas = []

    def processar(instante, anos):
        chamadas.append(instante)
        painel = pd.DataFrame({'data': [dt.date(2026, 10, 5)] * 2, 'ticker': ['PETR4', 'VALE3'],
                               'EV_EBIT': [5.0, 9.0]})
        painel.attrs['versao_metodologia'] = 'TRIO_ROIC_PIT_V3_2026-10-03'
        return painel, painel.head(1)

    r = registrar_painel_diario(tmp_path, '2026-10-05 21:30', processar, dt.date(2026, 10, 5))
    assert r['status'] == 'registrado'
    assert pd.read_parquet(tmp_path / 'painel-2026-10-05.parquet')['ticker'].tolist() == ['PETR4', 'VALE3']
    meta = json.loads((tmp_path / 'painel-2026-10-05.json').read_text(encoding='utf-8'))
    assert meta['carteira_do_dia'] == ['PETR4'] and meta['versao_metodologia'].startswith('TRIO')
    # Segunda rodada no mesmo pregao (ex.: computador religado) nao recalcula.
    assert registrar_painel_diario(tmp_path, '2026-10-06 07:00', processar,
                                   dt.date(2026, 10, 5))['status'] == 'ja_registrado'
    assert len(chamadas) == 1


def test_painel_diario_bloqueado_fica_registrado_e_sem_parquet(tmp_path):
    from carteira.pipeline_atual import registrar_painel_diario

    def falha(instante, anos):
        raise ValueError('CVM fora do ar')
    r = registrar_painel_diario(tmp_path, '2026-10-05 21:30', falha, dt.date(2026, 10, 5))
    assert r['status'] == 'bloqueado' and 'CVM fora do ar' in r['erro']
    assert not (tmp_path / 'painel-2026-10-05.parquet').exists()
