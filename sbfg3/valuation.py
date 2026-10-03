"""Maquina de valuation de SBFG3 -- testa premissas, nao as inventa.

Divisao de trabalho
-------------------
As ANCORAS sao fato: saem de data/fluxo_caixa.json (CVM, LTM ate 2T26) e do
Formulario de Referencia. As PREMISSAS sao julgamento e vivem em premissas.json,
que nasce com null nos campos que dependem de decisao.

O reverse DCF nao usa premissa nenhuma: ele inverte o preco de mercado e devolve
o que o preco ja assume, numa grade de aliquota e WACC. Roda sempre.

O DCF usa as premissas e fica bloqueado enquanto houver null. Preencher esses
campos sem base seria fabricar julgamento.

A formula
---------
Value driver, na forma de Miller-Modigliani:

    EV = NOPAT * (1 - g / ROIC) / (WACC - g)

Ela liga preco, retorno e crescimento numa relacao so, e por isso serve nos dois
sentidos. Invertida:

    g = 0        ->  WACC = NOPAT / EV          (o retorno que o preco embute sem crescimento)
    WACC, g      ->  ROIC = g / (1 - EV * (WACC - g) / NOPAT)
    WACC, ROIC   ->  g = WACC - NOPAT * (1 - g/ROIC) / EV, resolvido em g

Uso
---
    python valuation.py                  le premissas.json, grava valuation.json
    python valuation.py --xlsx           grava tambem valuation.xlsx
"""

from __future__ import annotations

import argparse
import json
import os

AQUI = os.path.dirname(os.path.abspath(__file__))
FLUXO = os.path.join(AQUI, 'data', 'fluxo_caixa.json')
PREMISSAS = os.path.join(AQUI, 'premissas.json')
SAIDA = os.path.join(AQUI, 'valuation.json')

# Fato, nao premissa. Fonte no comentario de cada um.
ACOES = 230_663_994          # FRE 2026, capital integralizado aprovado em 08/05/2026
ARRENDAMENTO_PASSIVO = 1_587_000_000.0   # 2.01.04.03 + 2.02.01.03 no ITR 2T26

GRADE_ALIQUOTA = [0.00, 0.15, 0.25, 0.34]
GRADE_WACC = [0.10, 0.12, 0.14, 0.16, 0.18]


def ancoras() -> dict:
    """Os numeros de fato, do bloco CVM da cobertura."""
    with open(FLUXO, encoding='utf-8') as f:
        d = json.load(f)
    return {
        'data_corte': d['data_corte'],
        'receita': d['ltm']['receita_bruta_cvm_3_01'],
        'ebit': d['ltm']['ebit'],
        'capex_ltm': d['ltm']['capex'],
        'arrendamento_pago_ltm': d['ltm']['arrendamento_pago'],
        'capital_investido': d['retorno']['capital_investido'],
        'divida_liquida': d['retorno']['divida_liquida'],
        'acoes': ACOES,
        'arrendamento_passivo': ARRENDAMENTO_PASSIVO,
    }


def premissas(caminho: str | None = None) -> dict:
    with open(caminho or PREMISSAS, encoding='utf-8') as f:
        return {k: v for k, v in json.load(f).items() if not k.startswith('_')}


def pendentes(p: dict) -> list[str]:
    return sorted(k for k, v in p.items() if v is None)


# ---------------------------------------------------------------------------
# Reverse DCF -- nao depende de premissa
# ---------------------------------------------------------------------------

def enterprise_value(a: dict, preco: float, convencao: str) -> float:
    """EV no preco dado.

    Em 'passivo_na_divida' o arrendamento fica na divida liquida, coerente com o
    EBIT pos-IFRS 16 que ja exclui o aluguel. Em 'aluguel_no_fluxo' ele sai da
    divida, e quem usar essa convencao tem de subtrair o aluguel do fluxo.
    """
    divida = a['divida_liquida']
    if convencao == 'aluguel_no_fluxo':
        divida -= a['arrendamento_passivo']
    return preco * a['acoes'] + divida


def reverse_dcf(a: dict, preco: float, convencao: str) -> dict:
    """O que o preco ja assume, sem escolher aliquota nem WACC."""

    ev = enterprise_value(a, preco, convencao)
    linhas = []
    for aliq in GRADE_ALIQUOTA:
        nopat = a['ebit'] * (1 - aliq)
        # com g = 0 a formula colapsa em EV = NOPAT / WACC
        wacc_sem_crescimento = nopat / ev
        roic_medido = nopat / a['capital_investido']
        celulas = []
        for wacc in GRADE_WACC:
            for g in (0.0, 0.02, 0.04):
                if g >= wacc:
                    continue
                denominador = 1 - ev * (wacc - g) / nopat
                # denominador <= 0 significa que nenhum ROIC finito justifica o preco:
                # o proprio NOPAT ja nao cobre o custo de capital exigido.
                roic = g / denominador if denominador > 1e-9 else None
                celulas.append({
                    'wacc': wacc,
                    'crescimento': g,
                    'roic_implicito': roic,
                    'inviavel': roic is None or roic < 0,
                })
        linhas.append({
            'aliquota': aliq,
            'nopat': nopat,
            'roic_medido': roic_medido,
            'wacc_implicito_sem_crescimento': wacc_sem_crescimento,
            'grade': celulas,
        })
    return {'enterprise_value': ev, 'preco_por_acao': preco, 'convencao': convencao, 'por_aliquota': linhas}


# ---------------------------------------------------------------------------
# DCF explicito -- depende das premissas
# ---------------------------------------------------------------------------

def dcf(a: dict, p: dict) -> dict:
    """Projecao explicita mais perpetuidade. So roda com as premissas fechadas."""

    faltando = pendentes(p)
    if faltando:
        return {'bloqueado_por': faltando}

    anos = int(p['anos_projecao'])
    cresc = p['crescimento_receita']
    if not isinstance(cresc, list):
        cresc = [cresc] * anos
    if len(cresc) != anos:
        raise ValueError(f'crescimento_receita precisa de {anos} valores, veio {len(cresc)}')

    wacc, g_term = p['wacc'], p['crescimento_terminal']
    if g_term >= wacc:
        raise ValueError('crescimento terminal precisa ser menor que o WACC')

    receita = a['receita']
    fluxos, detalhe = [], []
    for i, taxa in enumerate(cresc, start=1):
        receita *= (1 + taxa)
        ebit = receita * p['margem_ebit']
        nopat = ebit * (1 - p['aliquota_normalizada'])
        capex = receita * p['capex_sobre_receita']
        giro = receita * p['variacao_capital_giro_sobre_receita']
        fcf = nopat - capex - giro
        if p['convencao_arrendamento'] == 'aluguel_no_fluxo':
            fcf += a['arrendamento_pago_ltm']   # ja e negativo
        desconto = (1 + wacc) ** i
        fluxos.append(fcf / desconto)
        detalhe.append({'ano': i, 'receita': receita, 'ebit': ebit, 'nopat': nopat,
                        'capex': capex, 'variacao_giro': giro, 'fcf': fcf,
                        'fcf_descontado': fcf / desconto})

    fcf_terminal = detalhe[-1]['fcf'] * (1 + g_term)
    valor_terminal = fcf_terminal / (wacc - g_term)
    valor_terminal_descontado = valor_terminal / ((1 + wacc) ** anos)

    ev = sum(fluxos) + valor_terminal_descontado
    divida = a['divida_liquida']
    if p['convencao_arrendamento'] == 'aluguel_no_fluxo':
        divida -= a['arrendamento_passivo']
    equity = ev - divida
    por_acao = equity / a['acoes']

    return {
        'projecao': detalhe,
        'valor_terminal': valor_terminal,
        'valor_terminal_descontado': valor_terminal_descontado,
        'peso_do_terminal': valor_terminal_descontado / ev if ev else None,
        'enterprise_value': ev,
        'divida_liquida': divida,
        'equity_value': equity,
        'valor_por_acao': por_acao,
        'upside': por_acao / p['preco_por_acao'] - 1,
    }


def sensibilidade(a: dict, p: dict) -> dict:
    """Valor por acao variando WACC e crescimento terminal em torno da premissa."""

    if pendentes(p):
        return {'bloqueado_por': pendentes(p)}
    grade = []
    for dw in (-0.02, -0.01, 0.0, 0.01, 0.02):
        for dg in (-0.01, 0.0, 0.01):
            alt = dict(p, wacc=p['wacc'] + dw, crescimento_terminal=p['crescimento_terminal'] + dg)
            if alt['crescimento_terminal'] >= alt['wacc']:
                continue
            grade.append({'wacc': alt['wacc'], 'crescimento_terminal': alt['crescimento_terminal'],
                          'valor_por_acao': dcf(a, alt)['valor_por_acao']})
    return {'grade': grade}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--xlsx', action='store_true', help='exporta tambem valuation.xlsx')
    # --premissas e --saida existem para o app poder simular sem tocar nos arquivos
    # versionados: a rota do Next escreve num temporario e le de outro.
    ap.add_argument('--premissas', help='json de premissas alternativo')
    ap.add_argument('--saida', help='caminho de saida alternativo')
    ap.add_argument('--silencioso', action='store_true', help='nao imprime o resumo')
    args = ap.parse_args()

    a, p = ancoras(), premissas(args.premissas)
    falta = pendentes(p)

    saida = {
        'gerado_em': __import__('datetime').date.today().isoformat(),
        'ancoras': a,
        'premissas': p,
        'premissas_pendentes': falta,
        'reverse_dcf': reverse_dcf(a, p['preco_por_acao'], p['convencao_arrendamento']),
        'dcf': dcf(a, p),
        'sensibilidade': sensibilidade(a, p),
    }
    destino = args.saida or SAIDA
    with open(destino, 'w', encoding='utf-8') as f:
        json.dump(saida, f, ensure_ascii=False, indent=1)

    # A planilha vem antes de qualquer saida antecipada: a rota do app chama com
    # --silencioso e --xlsx juntos, e com o return na frente ela nunca era gerada.
    if args.xlsx:
        from exportar_xlsx import exportar
        exportar(saida, os.path.join(AQUI, 'valuation.xlsx'))

    if args.silencioso:
        return

    rev = saida['reverse_dcf']
    print(f"corte {a['data_corte']} | preco R$ {p['preco_por_acao']:.2f} | EV R$ {rev['enterprise_value']/1e6:,.0f} mi")
    print("\nO que o preco embute sem nenhum crescimento:")
    for linha in rev['por_aliquota']:
        print(f"  aliquota {linha['aliquota']:>5.0%}  ->  WACC implicito {linha['wacc_implicito_sem_crescimento']:>6.1%}"
              f"   (ROIC medido {linha['roic_medido']:>5.1%})")
    if falta:
        print(f"\nDCF bloqueado. Premissas pendentes: {', '.join(falta)}")
    else:
        print(f"\nvalor por acao R$ {saida['dcf']['valor_por_acao']:.2f} "
              f"({saida['dcf']['upside']:+.1%}) | terminal = {saida['dcf']['peso_do_terminal']:.0%} do EV")

    if args.xlsx:
        print('planilha: valuation.xlsx')
    print(f'gravado em {destino}')


if __name__ == '__main__':
    main()
