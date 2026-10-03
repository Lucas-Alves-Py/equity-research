import math

import pandas as pd
import pytest

from carteira.fundo_ficticio import simular_fundo_mensal


TICKERS = [f'AB{chr(65 + i)}C3' for i in range(10)]


def _entradas():
    execucoes = [('2026-10-05', '2026-10-06'), ('2026-11-05', '2026-11-06')]
    alvos = pd.DataFrame([{'data_decisao': decisao, 'data_execucao': execucao,
                           'ticker': ticker, 'peso': 0.1}
                          for decisao, execucao in execucoes for ticker in TICKERS])
    agenda = pd.DataFrame([{'data_decisao': decisao, 'data_execucao': execucao,
                            'aporte_ficticio': 10000.0}
                           for decisao, execucao in execucoes])
    precos = pd.DataFrame([{'data': dia, 'ticker': ticker,
                            'preco_bruto': preco,
                            'preco_fechamento_ajustado': preco}
                           for dia, preco in [('2026-10-06', 10.0),
                                              ('2026-10-07', 11.0),
                                              ('2026-11-06', 12.0)]
                           for ticker in TICKERS])
    return alvos, precos, agenda


def test_aporte_nao_entra_no_retorno_e_ordens_fecham():
    resultado = simular_fundo_mensal(*_entradas())
    nav = resultado['patrimonio']
    assert nav['indice'].tolist() == pytest.approx([100, 110, 120])
    assert nav['patrimonio_ficticio'].tolist() == pytest.approx([10000, 11000, 22000])
    assert nav['total_aportado'].tolist() == pytest.approx([10000, 10000, 20000])
    operacoes = resultado['operacoes_nocionais']
    assert operacoes.groupby('data_execucao')['financeiro_nocional'].sum().tolist() == pytest.approx([10000, 10000])
    assert all(math.isfinite(v) for v in operacoes['quantidade_teorica_fracionaria'])


def test_sem_preco_de_posicao_ativa_bloqueia_nav():
    alvos, precos, agenda = _entradas()
    precos = precos[~(precos['data'].eq('2026-10-07') &
                      precos['ticker'].eq(TICKERS[0]))]
    with pytest.raises(ValueError, match='preco_fechamento_ajustado ausente'):
        simular_fundo_mensal(alvos, precos, agenda)


def test_rebalanceamento_vende_ativo_removido_e_compra_novo():
    alvos, precos, agenda = _entradas()
    troca = (alvos['data_execucao'].eq('2026-11-06') &
             alvos['ticker'].eq(TICKERS[0]))
    alvos.loc[troca, 'ticker'] = 'NOVO3'
    precos = pd.concat([precos, pd.DataFrame([{
        'data': '2026-11-06', 'ticker': 'NOVO3',
        'preco_bruto': 20.0, 'preco_fechamento_ajustado': 20.0}])],
        ignore_index=True)
    resultado = simular_fundo_mensal(alvos, precos, agenda)
    ordens = resultado['operacoes_nocionais']
    ultima = ordens[ordens['data_execucao'].eq(pd.Timestamp('2026-11-06').date())]
    assert ultima.loc[ultima['ticker'].eq(TICKERS[0]), 'financeiro_nocional'].iat[0] == pytest.approx(-1200)
    assert ultima.loc[ultima['ticker'].eq('NOVO3'), 'financeiro_nocional'].iat[0] == pytest.approx(2200)
    assert ultima['financeiro_nocional'].sum() == pytest.approx(10000)


def test_sem_d1_nao_inventa_aporte():
    alvos, precos, agenda = _entradas()
    agenda['data_execucao'] = None
    with pytest.raises(ValueError, match='nenhuma execucao D\\+1 observada'):
        simular_fundo_mensal(alvos, precos, agenda)
