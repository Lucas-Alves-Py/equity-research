"""Exporta o valuation.json para planilha.

A planilha e apresentacao do resultado, nao ferramenta de trabalho: quem manda
e o valuation.py. Por isso cada aba carrega a data de geracao e as premissas
pendentes, para ninguem circular um numero achando que a tese esta fechada.
"""

from __future__ import annotations

import json
import os

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

TITULO = Font(bold=True, color='FFFFFF')
FUNDO = PatternFill('solid', fgColor='1F2937')
DESTAQUE = Font(bold=True)


def _cabecalho(ws, colunas):
    ws.append(colunas)
    for c in range(1, len(colunas) + 1):
        cel = ws.cell(row=ws.max_row, column=c)
        cel.font, cel.fill = TITULO, FUNDO
        cel.alignment = Alignment(horizontal='center')


def _largura(ws, larguras):
    for i, w in enumerate(larguras, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w


def exportar(dados: dict, destino: str) -> str:
    wb = Workbook()

    # --- capa -------------------------------------------------------------
    ws = wb.active
    ws.title = 'Capa'
    ws.append(['Valuation SBFG3 - Grupo SBF S.A.'])
    ws['A1'].font = Font(bold=True, size=14)
    ws.append([])
    ws.append(['Gerado em', dados['gerado_em']])
    ws.append(['Corte contabil', dados['ancoras']['data_corte']])
    ws.append(['Fonte das ancoras', 'CVM (ITR/DFP) e Formulario de Referencia 2026'])
    ws.append(['Gerado por', 'sbfg3/valuation.py'])
    ws.append([])
    pend = dados['premissas_pendentes']
    ws.append(['Premissas pendentes', len(pend)])
    ws.cell(row=ws.max_row, column=1).font = DESTAQUE
    for p in pend:
        ws.append(['', p])
    if pend:
        ws.append([])
        ws.append(['ATENCAO', 'O DCF nao foi calculado: ha premissa em aberto. '
                              'Somente o reverse DCF e valido nesta planilha.'])
        ws.cell(row=ws.max_row, column=1).font = Font(bold=True, color='B91C1C')
    _largura(ws, [26, 70])

    # --- ancoras ----------------------------------------------------------
    ws = wb.create_sheet('Ancoras')
    _cabecalho(ws, ['Entrada', 'Valor', 'Unidade'])
    a = dados['ancoras']
    for rotulo, chave, unidade in [
        ('Receita LTM', 'receita', 'R$'), ('EBIT LTM', 'ebit', 'R$'),
        ('Capex LTM', 'capex_ltm', 'R$'), ('Arrendamento pago LTM', 'arrendamento_pago_ltm', 'R$'),
        ('Capital investido', 'capital_investido', 'R$'), ('Divida liquida', 'divida_liquida', 'R$'),
        ('Passivo de arrendamento', 'arrendamento_passivo', 'R$'), ('Acoes', 'acoes', 'un'),
    ]:
        ws.append([rotulo, a[chave], unidade])
    _largura(ws, [30, 20, 10])

    # --- premissas --------------------------------------------------------
    ws = wb.create_sheet('Premissas')
    _cabecalho(ws, ['Premissa', 'Valor', 'Situacao'])
    for k, v in dados['premissas'].items():
        ws.append([k, v if v is not None else '', 'PENDENTE' if v is None else 'definida'])
        if v is None:
            ws.cell(row=ws.max_row, column=3).font = Font(bold=True, color='B91C1C')
    _largura(ws, [40, 22, 14])

    # --- reverse DCF ------------------------------------------------------
    rev = dados['reverse_dcf']
    ws = wb.create_sheet('Reverse DCF')
    ws.append(['Enterprise value', rev['enterprise_value']])
    ws.append(['Preco por acao', rev['preco_por_acao']])
    ws.append(['Convencao de arrendamento', rev['convencao']])
    ws.append([])
    ws.append(['O que o preco embute sem nenhum crescimento'])
    ws.cell(row=ws.max_row, column=1).font = DESTAQUE
    _cabecalho(ws, ['Aliquota', 'NOPAT', 'ROIC medido', 'WACC implicito com g=0'])
    for linha in rev['por_aliquota']:
        ws.append([linha['aliquota'], linha['nopat'], linha['roic_medido'],
                   linha['wacc_implicito_sem_crescimento']])
        for c in (1, 3, 4):
            ws.cell(row=ws.max_row, column=c).number_format = '0.0%'
    ws.append([])
    ws.append(['ROIC que o preco exige, por WACC e crescimento'])
    ws.cell(row=ws.max_row, column=1).font = DESTAQUE
    _cabecalho(ws, ['Aliquota', 'WACC', 'Crescimento', 'ROIC implicito'])
    for linha in rev['por_aliquota']:
        for cel in linha['grade']:
            ws.append([linha['aliquota'], cel['wacc'], cel['crescimento'],
                       'inviavel' if cel['inviavel'] else cel['roic_implicito']])
            for c in (1, 2, 3):
                ws.cell(row=ws.max_row, column=c).number_format = '0.0%'
            if not cel['inviavel']:
                ws.cell(row=ws.max_row, column=4).number_format = '0.0%'
    _largura(ws, [14, 12, 14, 22])

    # --- DCF e sensibilidade ---------------------------------------------
    d = dados['dcf']
    ws = wb.create_sheet('DCF')
    if 'bloqueado_por' in d:
        ws.append(['DCF nao calculado.'])
        ws['A1'].font = Font(bold=True, color='B91C1C')
        ws.append(['Premissas em aberto:'])
        for p in d['bloqueado_por']:
            ws.append(['', p])
    else:
        _cabecalho(ws, ['Ano', 'Receita', 'EBIT', 'NOPAT', 'Capex', 'Var. giro', 'FCF', 'FCF descontado'])
        for r in d['projecao']:
            ws.append([r['ano'], r['receita'], r['ebit'], r['nopat'], r['capex'],
                       r['variacao_giro'], r['fcf'], r['fcf_descontado']])
        ws.append([])
        for rotulo, chave in [('Valor terminal', 'valor_terminal'),
                              ('Terminal descontado', 'valor_terminal_descontado'),
                              ('Peso do terminal', 'peso_do_terminal'),
                              ('Enterprise value', 'enterprise_value'),
                              ('Divida liquida', 'divida_liquida'),
                              ('Equity value', 'equity_value'),
                              ('Valor por acao', 'valor_por_acao'),
                              ('Upside', 'upside')]:
            ws.append([rotulo, d[chave]])
            ws.cell(row=ws.max_row, column=1).font = DESTAQUE
    _largura(ws, [22, 16, 16, 16, 16, 16, 16, 18])

    s = dados['sensibilidade']
    ws = wb.create_sheet('Sensibilidade')
    if 'bloqueado_por' in s:
        ws.append(['Sensibilidade nao calculada: ha premissa em aberto.'])
        ws['A1'].font = Font(bold=True, color='B91C1C')
    else:
        _cabecalho(ws, ['WACC', 'Crescimento terminal', 'Valor por acao'])
        for r in s['grade']:
            ws.append([r['wacc'], r['crescimento_terminal'], r['valor_por_acao']])
            for c in (1, 2):
                ws.cell(row=ws.max_row, column=c).number_format = '0.0%'
    _largura(ws, [12, 24, 18])

    wb.save(destino)
    return destino


if __name__ == '__main__':
    aqui = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(aqui, 'valuation.json'), encoding='utf-8') as f:
        exportar(json.load(f), os.path.join(aqui, 'valuation.xlsx'))
    print('valuation.xlsx gravado')
