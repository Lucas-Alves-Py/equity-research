"""Testa a matematica do valuation contra casos com resposta conhecida.

O DCF nasce bloqueado porque as premissas reais estao em aberto, entao sem estes
testes ele iria para o repositorio sem nunca ter executado. Cada caso aqui usa
premissas inventadas de proposito -- servem para checar a conta, nao a tese.
"""

from __future__ import annotations

import valuation as v


def ancoras_simples() -> dict:
    """Ancoras redondas, para a conta poder ser conferida a mao."""
    return {
        'data_corte': '2026-06-30',
        'receita': 1_000.0,
        'ebit': 100.0,
        'capex_ltm': -50.0,
        'arrendamento_pago_ltm': -20.0,
        'capital_investido': 1_000.0,
        'divida_liquida': 200.0,
        'acoes': 100.0,
        'arrendamento_passivo': 80.0,
    }


def premissas_neutras(**troca) -> dict:
    """Sem crescimento, sem capex e sem giro: o FCF vira NOPAT puro."""
    p = {
        'preco_por_acao': 10.0,
        'aliquota_normalizada': 0.0,
        'wacc': 0.10,
        'crescimento_terminal': 0.0,
        'anos_projecao': 5,
        'crescimento_receita': 0.0,
        'margem_ebit': 0.10,
        'capex_sobre_receita': 0.0,
        'variacao_capital_giro_sobre_receita': 0.0,
        'convencao_arrendamento': 'passivo_na_divida',
    }
    p.update(troca)
    return p


def test_perpetuidade_sem_crescimento():
    """Sem crescimento e sem investimento, EV tem de dar NOPAT / WACC."""
    a, p = ancoras_simples(), premissas_neutras()
    r = v.dcf(a, p)
    assert abs(r['enterprise_value'] - 100.0 / 0.10) < 1e-6, r['enterprise_value']
    assert abs(r['equity_value'] - (1000.0 - 200.0)) < 1e-6
    assert abs(r['valor_por_acao'] - 8.0) < 1e-9


def test_crescimento_terminal_aumenta_o_valor():
    a = ancoras_simples()
    base = v.dcf(a, premissas_neutras())['enterprise_value']
    com_g = v.dcf(a, premissas_neutras(crescimento_terminal=0.03))['enterprise_value']
    assert com_g > base


def test_wacc_maior_reduz_o_valor():
    a = ancoras_simples()
    barato = v.dcf(a, premissas_neutras(wacc=0.08))['enterprise_value']
    caro = v.dcf(a, premissas_neutras(wacc=0.14))['enterprise_value']
    assert caro < barato


def test_aliquota_reduz_o_valor_na_proporcao_do_nopat():
    """Com tudo mais igual, tributar 34% tira 34% do EV."""
    a = ancoras_simples()
    sem = v.dcf(a, premissas_neutras())['enterprise_value']
    com = v.dcf(a, premissas_neutras(aliquota_normalizada=0.34))['enterprise_value']
    assert abs(com / sem - 0.66) < 1e-9


def test_convencao_de_arrendamento_muda_fluxo_e_divida_juntos():
    """Trocar a convencao tem de mexer nos dois lados, nunca em um so."""
    a = ancoras_simples()
    na_divida = v.dcf(a, premissas_neutras())
    no_fluxo = v.dcf(a, premissas_neutras(convencao_arrendamento='aluguel_no_fluxo'))
    assert no_fluxo['divida_liquida'] == na_divida['divida_liquida'] - a['arrendamento_passivo']
    assert no_fluxo['enterprise_value'] < na_divida['enterprise_value']


def test_dcf_bloqueia_com_premissa_em_aberto():
    a = ancoras_simples()
    r = v.dcf(a, premissas_neutras(wacc=None))
    assert r == {'bloqueado_por': ['wacc']}


def test_crescimento_terminal_acima_do_wacc_falha():
    a = ancoras_simples()
    try:
        v.dcf(a, premissas_neutras(crescimento_terminal=0.12, wacc=0.10))
    except ValueError:
        return
    raise AssertionError('devia recusar crescimento terminal acima do WACC')


def test_reverse_dcf_devolve_wacc_igual_a_nopat_sobre_ev():
    """Com g = 0 a formula colapsa: WACC implicito = NOPAT / EV."""
    a = ancoras_simples()
    r = v.reverse_dcf(a, preco=10.0, convencao='passivo_na_divida')
    ev = 10.0 * 100.0 + 200.0
    assert abs(r['enterprise_value'] - ev) < 1e-9
    linha = next(x for x in r['por_aliquota'] if x['aliquota'] == 0.0)
    assert abs(linha['wacc_implicito_sem_crescimento'] - 100.0 / ev) < 1e-12


def test_reverse_dcf_fecha_com_o_dcf():
    """O ROIC implicito, devolvido ao DCF pela formula, reproduz o mesmo EV.

    E o teste que amarra as duas pontas: se a inversao estivesse errada, o ROIC
    que ela devolve nao geraria de volta o enterprise value de onde saiu.
    """
    a = ancoras_simples()
    preco, wacc, g = 10.0, 0.10, 0.02
    r = v.reverse_dcf(a, preco=preco, convencao='passivo_na_divida')
    linha = next(x for x in r['por_aliquota'] if x['aliquota'] == 0.0)
    celula = next(c for c in linha['grade'] if c['wacc'] == wacc and c['crescimento'] == g)
    roic = celula['roic_implicito']
    ev_reconstruido = linha['nopat'] * (1 - g / roic) / (wacc - g)
    assert abs(ev_reconstruido - r['enterprise_value']) < 1e-6


if __name__ == '__main__':
    testes = [f for n, f in sorted(globals().items()) if n.startswith('test_')]
    for t in testes:
        t()
        print('  ok  ', t.__name__)
    print(f'{len(testes)} testes passaram')
