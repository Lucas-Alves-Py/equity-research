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
