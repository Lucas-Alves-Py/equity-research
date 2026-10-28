"""Relatório em PDF de uma seleção registrada pelo pipeline atual.

Uso, na raiz do projeto:
    python -m carteira.relatorio_carteira artifacts/selecao_atual/<registro>.json

Lê o JSON da seleção e o parquet do painel gravados ao lado dele, recalcula
a posição de cada ação em cada fator com a mesma regra do seletor e gera um
PDF de três páginas. Não consulta preços nem altera registros.
"""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd
import yaml
from fpdf import FPDF

from carteira.carteira import selecionar

RAIZ = Path(__file__).resolve().parents[1]
MODELOS = RAIZ / 'carteira' / 'config' / 'modelos.yaml'
BACKTEST = [('Inicial: 5 ações, liquidez R$ 2 mi', '33,2%', '28,6%', '0,83', '-59,3%'),
            ('Controle: 10 ações, liquidez R$ 2 mi', '24,8%', '24,3%', '0,63', '-52,8%'),
            ('Atual: 10 ações, liquidez R$ 500 mil', '28,0%', '24,0%', '0,77', '-52,7%')]
IDENTIDADE = RAIZ / 'carteira' / 'identidade'
# Alpha Thesis: azul-marinho, dourado e papel claro.
AZUL, OURO, CINZA = (3, 33, 66), (196, 155, 85), (120, 128, 140)
MESES = 'JAN FEV MAR ABR MAI JUN JUL AGO SET OUT NOV DEZ'.split()
TROCAS = {'—': '-', '–': '-', '−': '-', '→': '->', '≥': '>=',
          '≤': '<=', '×': 'x', '’': "'", '“': '"', '”': '"'}


def _t(texto) -> str:
    """O fpdf 1.7 usa fontes básicas em Latin-1."""
    texto = str(texto)
    for a, b in TROCAS.items():
        texto = texto.replace(a, b)
    return texto.encode('latin-1', 'replace').decode('latin-1')


def _curto(texto, n):
    texto = str(texto or '')
    return texto if len(texto) <= n else texto[:n - 1].rstrip(' ,-.') + '.'


def _pct(x, casas=1):
    return '-' if pd.isna(x) else f'{x * 100:.{casas}f}%'.replace('.', ',')


def _num(x, casas=1):
    return '-' if pd.isna(x) else f'{x:,.{casas}f}'.replace(',', 'X').replace('.', ',').replace('X', '.')


def ranking_completo(painel: pd.DataFrame, modelo: dict) -> pd.DataFrame:
    """Mesma regra do seletor, sem cortar em N, com a posição em cada fator."""
    indicadores = modelo['indicadores']
    liquidez = modelo['filtro_liquidez_mm'] * 1e6
    data = painel['data'].iat[0]
    # O seletor usa uma classe por empresa (a mais negociada).
    elegiveis = painel[pd.to_numeric(painel['volume'], errors='coerce') > liquidez]
    todos = selecionar(painel, data, indicadores, liquidez, elegiveis['cnpj'].nunique())
    for ind, ordem in indicadores.items():
        todos[f'pos_{ind}'] = todos[ind].rank(ascending=ordem == 'crescente').astype(int)
    return todos


class Relatorio(FPDF):
    titulo = ''

    def header(self):
        # Monograma em marca d'água, como no papel timbrado.
        self.image(str(IDENTIDADE / 'marca_dagua.png'), x=62, y=105, w=160)
        self.image(str(IDENTIDADE / 'simbolo.png'), x=self.l_margin, y=9, h=10)
        x = self.l_margin + 13
        self.set_xy(x, 9.2)
        self.set_font('Times', 'B', 15)
        self.set_text_color(*AZUL)
        self.cell(52, 6, 'ALPHA THESIS', 0, 2)
        self.set_font('Helvetica', '', 6.3)
        self.cell(52, 3.5, 'Quantitative & Fundamental Research')
        direita = self.w - self.r_margin
        self.set_font('Helvetica', '', 7)
        rotulo = _t(f'{self.titulo}   |   {self.page_no()}')
        larg = self.get_string_width(rotulo)
        self.set_draw_color(*AZUL)
        self.set_line_width(0.15)
        self.line(x + 54, 14.5, direita - larg - 14, 14.5)
        self.set_draw_color(*OURO)
        self.set_line_width(0.8)
        self.line(direita - larg - 10, 14.5, direita - larg - 3, 14.5)
        self.set_xy(direita - larg, 12.6)
        self.cell(larg, 4, rotulo, 0, 0, 'R')
        self.set_line_width(0.2)
        self.set_text_color(0, 0, 0)
        self.set_y(26)

    def footer(self):
        y = self.h - 13
        self.set_draw_color(215, 215, 215)
        self.set_line_width(0.15)
        self.line(self.l_margin, y, self.w - self.r_margin, y)
        self.set_xy(self.l_margin, y + 2.5)
        self.set_font('Helvetica', 'B', 7)
        self.set_text_color(*AZUL)
        self.cell(80, 4, 'Quantitative & Fundamental Research')
        self.set_font('Helvetica', '', 7)
        self.cell(0, 4, '    '.join(['Pesquisa', 'Disciplina', 'Evidências', 'Longo Prazo']), 0, 0, 'R')
        self.set_draw_color(*OURO)
        self.set_line_width(0.7)
        self.line(self.l_margin, y + 8, self.l_margin + 8, y + 8)
        self.set_line_width(0.2)
        self.set_text_color(0, 0, 0)

    def secao(self, texto):
        self.ln(3)
        self.set_font('Times', 'B', 13)
        self.set_text_color(*AZUL)
        self.cell(0, 7, _t(texto), 0, 1)
        self.set_text_color(0, 0, 0)
        self.set_draw_color(*OURO)
        self.set_line_width(0.7)
        self.line(self.l_margin, self.get_y(), self.l_margin + 12, self.get_y())
        self.set_line_width(0.2)
        self.ln(2.5)

    def paragrafo(self, texto, tamanho=9):
        self.set_font('Helvetica', '', tamanho)
        self.multi_cell(0, 4.6, _t(texto))
        self.ln(1)

    def tabela(self, cabecalho, linhas, larguras, alinhamentos, destaque=None, tamanho=7.5):
        self.set_font('Helvetica', 'B', tamanho)
        self.set_fill_color(*AZUL)
        self.set_text_color(255, 255, 255)
        for texto, w, a in zip(cabecalho, larguras, alinhamentos):
            self.cell(w, 6, _t(texto), 0, 0, a, True)
        self.ln()
        self.set_text_color(0, 0, 0)
        self.set_font('Helvetica', '', tamanho)
        for i, linha in enumerate(linhas):
            cor = (244, 234, 214) if destaque and destaque(i) else (
                (244, 246, 249) if i % 2 else None)
            if cor:
                self.set_fill_color(*cor)
            for texto, w, a in zip(linha, larguras, alinhamentos):
                self.cell(w, 5.4, _t(texto), 0, 0, a, cor is not None)
            self.ln()


def _grafico(todos: pd.DataFrame, n: int, destino: Path):
    sel = todos[todos['posicao'] <= n]
    resto = todos[todos['posicao'] > n]
    positivos = resto[resto['EV_EBIT'] > 0]
    fig, ax = plt.subplots(figsize=(8, 4.2), dpi=150)
    ax.scatter(positivos['EV_EBIT'].clip(upper=30), positivos['ROIC'].clip(-0.1, 0.5) * 100,
               s=14, c='#b7c0cc', alpha=0.7, label='Universo')
    ax.scatter(sel['EV_EBIT'].clip(upper=30), sel['ROIC'].clip(-0.1, 0.5) * 100,
               s=44, c='#c49b55', edgecolor='white', linewidth=0.6, label='Carteira', zorder=3)
    for _, r in sel.iterrows():
        ax.annotate(r['ticker'], (min(r['EV_EBIT'], 30), r['ROIC'] * 100),
                    xytext=(4, 3), textcoords='offset points', fontsize=7, color='#032142')
    ax.set_xlabel('EV/EBIT (menor = mais barato; valores acima de 30 no limite)', fontsize=8)
    ax.set_ylabel('ROIC (%)', fontsize=8)
    ax.tick_params(labelsize=7)
    ax.grid(alpha=0.25)
    ax.legend(fontsize=7, loc='upper right', frameon=False)
    negativos = int((resto['EV_EBIT'] <= 0).sum())
    ax.set_title(f'Valor x qualidade no universo ({len(todos)} empresas; '
                 f'{negativos} com EBIT negativo fora do gráfico)', fontsize=9)
    fig.tight_layout()
    fig.savefig(destino)
    plt.close(fig)


def _setor(setor: pd.Series) -> pd.Series:
    # "Emp. Adm. Part. - Petróleo e Gás" é holding do mesmo setor.
    return (setor.fillna('sem setor')
            .str.replace(r'^Emp\. Adm\. Part\. - ', '', regex=True)
            # A CVM grafa o mesmo setor de dois jeitos.
            .replace('Const. Civil, Mat. Const. e Decoração',
                     'Construção Civil, Mat. Constr. e Decoração'))


def exportar_universo(registro_json: Path, saida: Path) -> Path:
    """Universo ranqueado da decisão, para os gráficos do painel.

    Mesma regra do PDF (ranking_completo) e a mesma checagem: se o ranking
    recalculado não reproduz a seleção registrada, nada é gravado.
    """
    registro = json.loads(Path(registro_json).read_text(encoding='utf-8'))
    painel = pd.read_parquet(Path(registro_json).with_suffix('.parquet'))
    modelo = yaml.safe_load(MODELOS.read_text(encoding='utf-8'))['modelos'][0]
    n = int(modelo['numero_ativos'])
    todos = ranking_completo(painel, modelo)
    if todos.loc[todos['posicao'] <= n, 'ticker'].tolist() != registro.get('tickers'):
        raise ValueError('ranking recalculado não reproduz a seleção registrada')
    from fontes import cvm
    todos = (todos.merge(painel[['ticker', 'cnpj']], on='ticker', how='left')
             .merge(cvm.cadastro()[['cnpj', 'setor']], on='cnpj', how='left'))
    todos['setor'] = _setor(todos['setor'])

    def num(x):
        return None if pd.isna(x) else float(x)

    linhas = [{'ticker': r.ticker, 'sector': r.setor, 'evEbit': num(r.EV_EBIT),
               'roic': num(r.ROIC), 'momentum': num(r.momento_12_meses),
               'rankEvEbit': int(r.pos_EV_EBIT), 'rankRoic': int(r.pos_ROIC),
               'rankMomentum': int(r.pos_momento_12_meses), 'score': int(r.rank_final),
               'position': int(r.posicao), 'selected': bool(r.posicao <= n)}
              for r in todos.sort_values('posicao').itertuples(index=False)]
    saida = Path(saida)
    saida.write_text(json.dumps({'signalDate': str(pd.Timestamp(painel['data'].iat[0]).date()),
                                 'assets': n, 'eligible': len(linhas), 'rows': linhas},
                                ensure_ascii=False, indent=1), encoding='utf-8')
    return saida


def gerar(registro_json: Path, saida: Path) -> Path:
    registro = json.loads(Path(registro_json).read_text(encoding='utf-8'))
    painel = pd.read_parquet(Path(registro_json).with_suffix('.parquet'))
    modelo = yaml.safe_load(MODELOS.read_text(encoding='utf-8'))['modelos'][0]
    n = int(modelo['numero_ativos'])
    todos = ranking_completo(painel, modelo)
    carteira = todos[todos['posicao'] <= n].copy()
    if carteira['ticker'].tolist() != registro.get('tickers', carteira['ticker'].tolist()):
        raise ValueError('ranking recalculado não reproduz a seleção registrada')

    from fontes import cvm
    cadastro = cvm.cadastro()[['cnpj', 'nome', 'setor']]
    detalhe = painel[['ticker', 'cnpj', 'preco_bruto', 'valor_mercado', 'dt_refer_dre']]
    carteira = carteira.merge(detalhe, on='ticker', how='left').merge(cadastro, on='cnpj', how='left')
    # Nome de pregão da B3 é o atual (o cadastro da CVM ainda chama a RIAA3 de Guararapes).
    from fontes import b3
    carteira['nome'] = [((b3.empresa(t) or {}).get('tradingName') or n or '').strip()
                        for t, n in zip(carteira['ticker'], carteira['nome'])]
    carteira['setor'] = _setor(carteira['setor'])

    sinal = pd.Timestamp(painel['data'].iat[0]).strftime('%d/%m/%Y')
    pdf = Relatorio('P', 'mm', 'A4')
    data_sinal = pd.Timestamp(painel['data'].iat[0])
    pdf.titulo = f'CARTEIRA FACTOR B3   |   {MESES[data_sinal.month - 1]} {data_sinal.year}'
    pdf.set_auto_page_break(True, 18)
    pdf.set_margins(11, 10, 11)

    # Página 1: a carteira
    pdf.add_page()
    pdf.set_font('Times', 'B', 22)
    pdf.set_text_color(*AZUL)
    pdf.cell(0, 10, _t(f'Carteira {modelo["id"]}'), 0, 1)
    pdf.set_text_color(*CINZA)
    pdf.set_font('Helvetica', '', 9.5)
    cobertura = registro.get('cobertura') or {}
    pdf.cell(0, 5.5, _t(f'Sinal: {sinal}  |  Metodologia: {registro.get("versao_metodologia")}'), 0, 1)
    pdf.set_text_color(0, 0, 0)
    pdf.ln(2)
    pdf.set_fill_color(247, 241, 230)
    pdf.set_font('Helvetica', '', 8.5)
    pdf.multi_cell(0, 4.6, _t(
        'Carteira fictícia em teste, sem dinheiro real. A decisão de cada mês é registrada no '
        'GitHub no primeiro pregão a partir do dia 5 e executada no fechamento do pregão '
        'seguinte. Este documento descreve a saída do modelo; não é recomendação de '
        'investimento.'), 0, 'L', True)
    pdf.secao('As 10 ações')
    linhas = [[str(int(r.posicao)), r.ticker, _curto(r.nome, 24),
               _curto(r.setor, 24), _num(r.preco_bruto, 2), _num(r.EV_EBIT, 1),
               _pct(r.ROIC), _pct(r.momento_12_meses), _num(r.valor_mercado / 1e9, 1),
               _num(r.volume / 1e6, 1)] for r in carteira.itertuples()]
    pdf.tabela(['#', 'Ticker', 'Nome de pregão', 'Setor CVM', 'Preço R$', 'EV/EBIT', 'ROIC',
                'Mom. 12m', 'Val. R$ bi', 'Vol. R$ mi'],
               linhas, [6, 14, 40, 36, 15, 14, 13, 15, 19, 17],
               ['C', 'L', 'L', 'L', 'R', 'R', 'R', 'R', 'R', 'R'])
    pdf.ln(2)
    pdf.paragrafo(
        f'Pesos iguais de 10%. Preço de fechamento bruto da B3 no dia do sinal. EV/EBIT = '
        f'(valor de mercado + dívida bruta - caixa) / EBIT dos últimos 12 meses. ROIC = (EBIT + '
        f'IR/CSLL da DRE) / (patrimônio líquido + dívida bruta). Momentum = retorno total de '
        f'252 pregões, ajustado por proventos e eventos aprovados pela B3. Volume = mediana '
        f'diária dos últimos 21 pregões. Demonstrações mais recentes usadas: até '
        f'{pd.Timestamp(carteira["dt_refer_dre"].max()).strftime("%d/%m/%Y")}.', 8)
    pdf.secao('Regra de seleção')
    pdf.paragrafo(
        f'Universo: ações com volume mediano acima de R$ {modelo["filtro_liquidez_mm"] * 1000:.0f} '
        f'mil por dia, uma classe por empresa (a mais negociada), bancos excluídos. Neste sinal, '
        f'{len(todos)} empresas elegíveis ({len(painel)} tickers com os três fatores). Cada ação recebe uma posição em cada fator: EV/EBIT do menor para o maior, '
        f'ROIC e momentum do maior para o menor. A soma das três posições ordena o universo, e '
        f'as {n} primeiras formam a carteira. Rebalanceamento mensal.')

    # Página 2: por que cada ação entrou
    pdf.add_page()
    pdf.secao('Por que cada ação entrou')
    linhas = [[str(int(r.posicao)), r.ticker, str(int(r.pos_EV_EBIT)), str(int(r.pos_ROIC)),
               str(int(r.pos_momento_12_meses)), _num(r.rank_final, 0)]
              for r in carteira.itertuples()]
    pdf.paragrafo(f'Posição de cada ação em cada fator, entre as {len(todos)} empresas do universo '
                  f'(1 = melhor). Quanto menor a soma, melhor a classificação.', 8.5)
    pdf.tabela(['#', 'Ticker', 'Posição EV/EBIT', 'Posição ROIC', 'Posição momentum', 'Soma'],
               linhas, [10, 22, 34, 34, 36, 22], ['C', 'L', 'C', 'C', 'C', 'C'])
    seguintes = todos[(todos['posicao'] > n) & (todos['posicao'] <= n + 5)]
    pdf.ln(3)
    pdf.paragrafo('As cinco seguintes da fila. Uma mudança pequena de preço ou de balanço '
                  'pode trocar a última ação da carteira por uma destas.', 8.5)
    pdf.tabela(['#', 'Ticker', 'EV/EBIT', 'ROIC', 'Mom. 12m', 'Soma'],
               [[str(int(r.posicao)), r.ticker, _num(r.EV_EBIT, 1), _pct(r.ROIC),
                 _pct(r.momento_12_meses), _num(r.rank_final, 0)] for r in seguintes.itertuples()],
               [10, 22, 34, 34, 36, 22], ['C', 'L', 'C', 'C', 'C', 'C'])
    with tempfile.TemporaryDirectory() as tmp:
        imagem = Path(tmp) / 'universo.png'
        _grafico(todos, n, imagem)
        pdf.ln(4)
        pdf.image(str(imagem), x=pdf.l_margin, w=pdf.w - pdf.l_margin - pdf.r_margin)
    setores = carteira['setor'].value_counts()
    pdf.secao('Concentração por setor')
    pdf.paragrafo('; '.join(f'{s}: {q} {"ação" if q == 1 else "ações"}' for s, q in setores.items())
                  + '. O modelo não neutraliza setores.', 8.5)

    # Página 3: dados, histórico e limites
    pdf.add_page()
    pdf.secao('De onde vêm os dados')
    pdf.paragrafo(
        'Preços, volumes e calendário: arquivo COTAHIST da B3. Proventos, desdobramentos e '
        'grupamentos: cadastro de empresas listadas da B3, aplicados só se aprovados antes do '
        'sinal. Demonstrações: ITR e DFP da CVM, usadas só depois da data de protocolo. '
        'Quantidade de ações: capital social informado à B3. Trocas de código de negociação '
        'da mesma empresa têm o histórico emendado; ticker sem CNPJ válido no formulário da CVM '
        'é ligado pelo cadastro da B3.')
    pdf.paragrafo(
        'Antes de usar estes dados, confrontei o pipeline com o gabarito Fintz do backtest na '
        'última data disponível (03/11/2025): EBIT em 94% das ações até 5% de diferença, ROIC '
        'com mediana de 0,00 ponto percentual, patrimônio líquido em 99% até 5% e momentum em '
        '87% até 5 pontos. Nove erros encontrados foram corrigidos. O relatório completo está em '
        'AUDITORIA_DADOS_2026-10-03.md no repositório.')
    pdf.secao('Histórico do backtest (dentro da amostra)')
    pdf.tabela(['Configuração', 'Retorno a.a.', 'Volatilidade', 'Sharpe', 'Pior queda'],
               BACKTEST, [74, 28, 28, 22, 26], ['L', 'R', 'R', 'R', 'R'],
               destaque=lambda i: i == 2)
    pdf.ln(2)
    pdf.paragrafo('Dezembro de 2011 a novembro de 2025, base Fintz, rebalanceamento a cada 21 '
                  'pregões, sem custos. Os números foram obtidos depois de testar várias '
                  'alternativas na mesma base; não são estimativa de desempenho futuro.', 8)
    pdf.secao('Limitações')
    pdf.paragrafo(
        '- Sem corretagem, spread, impacto de mercado ou imposto.\n'
        '- Sem neutralização setorial; a carteira pode concentrar setores.\n'
        '- No histórico, a estratégia chegou a cair mais de 50% a partir de um pico.\n'
        '- Não há gabarito externo para dados posteriores a novembro de 2025.\n'
        '- Ficam fora do universo, por motivo documentado: estreias com menos de 12 meses na '
        'bolsa, empresas em cisão sem valor de referência e units cuja composição não se conhece.\n'
        '- O desempenho prospectivo só começa a existir com a decisão de 05/10/2026.')
    saida = Path(saida)
    saida.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(saida), 'F')
    return saida


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('registro', help='JSON da seleção (com o parquet ao lado)')
    parser.add_argument('--saida', default=None)
    args = parser.parse_args()
    registro = Path(args.registro)
    saida = Path(args.saida) if args.saida else RAIZ / 'pdfs' / f'carteira_{registro.stem}.pdf'
    print(gerar(registro, saida))


if __name__ == '__main__':
    main()
