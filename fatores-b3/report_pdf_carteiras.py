import os
import shutil

from carteira.relatorio_carteira import AZUL, CINZA, Relatorio, _t


def _pct(x):
    return f'{x * 100:.2f}%'.replace('.', ',')


class MakePDF():

    def __init__(self, dd_all, dia_inicial, dia_final, dias_totais_backtest,
            retorno_acum_modelo, retorno_acum_cdi, retorno_acum_ibov, turn_over_medio,
            retorno_aa_modelo, vol_ultimo_ano, sharpe, var_diario,
            numero_trades, operacoes_vencedoras, operacoes_perdedoras, media_ganhos, media_perdas,
            expectativa_matematica, percentual_cart_supera_ibov, maior_sequencia_vitorias,
            maior_sequencia_derrotas, joesley_day, mar20, boasorteday, greve_caminhao,
            crise_2008, precatorios, retorno_21_min, retorno_63_min, retorno_126_min,
            retorno_252_min, retorno_504_min, retorno_756_min, nome_arquivo = "backtest.pdf"):

        self.risco_retorno = [
            ('Retorno acum. modelo', _pct(retorno_acum_modelo)),
            ('Retorno acum. CDI', _pct(retorno_acum_cdi)),
            ('Retorno acum. IBOV', _pct(retorno_acum_ibov)),
            ('Retorno a.a. modelo', _pct(retorno_aa_modelo)),
            ('Vol 252d', _pct(vol_ultimo_ano)),
            ('Índice Sharpe', f'{sharpe:.2f}'.replace('.', ',')),
            ('VAR diário 95%', _pct(var_diario)),
            ('Turnover da carteira', _pct(turn_over_medio)),
            ('Drawdown máximo', _pct(dd_all))]
        self.trade = [
            ('Número de carteiras', str(int(numero_trades))),
            ('% Operações vencedoras', _pct(operacoes_vencedoras)),
            ('% Operações perdedoras', _pct(operacoes_perdedoras)),
            ('Média de ganhos', _pct(media_ganhos)),
            ('Média de perdas', _pct(media_perdas)),
            ('Expec. matemática por trade', _pct(expectativa_matematica)),
            ('Maior sequência de vitórias', str(int(maior_sequencia_vitorias))),
            ('Maior sequência de derrotas', str(int(maior_sequencia_derrotas))),
            ('% Meses carteira > IBOV', _pct(percentual_cart_supera_ibov))]
        # Evento fora do período do backtest vem como '-'.
        self.estresse = [
            ('Crise de 2008', crise_2008),
            ('Joesley Day - 18/05/2017', joesley_day),
            ('Greve dos caminhoneiros - 2018', greve_caminhao),
            ('Auge da pandemia - mar/2020', mar20),
            ('Precatórios - ago/nov 2021', precatorios),
            ('Boa sorte Day - 10/11/2022', boasorteday)]
        self.sem_lucro = [
            ('Pior período de 1 mês', retorno_21_min),
            ('Pior período de 3 meses', retorno_63_min),
            ('Pior período de 6 meses', retorno_126_min),
            ('Pior período de 1 ano', retorno_252_min),
            ('Pior período de 2 anos', retorno_504_min),
            ('Pior período de 3 anos', retorno_756_min)]
        self.nome_arquivo = nome_arquivo

        self.pdf = Relatorio('P', 'mm', 'A4')
        self.pdf.titulo = 'RELATÓRIO DE BACKTEST'
        self.pdf.set_auto_page_break(True, 18)
        self.pdf.set_margins(11, 10, 11)
        self.largura = self.pdf.w - self.pdf.l_margin - self.pdf.r_margin

        self.pagina_resumo(dia_inicial, dia_final, dias_totais_backtest)
        self.pagina_quedas()
        self.pagina_retornos()
        self.pagina_janelas_moveis()

        self.pdf.output(self.nome_arquivo, 'F')

        shutil.move(f'./{self.nome_arquivo}', f'./pdfs/{self.nome_arquivo}')

    def _imagem(self, nome, largura, x=None, y=None, proporcao=None):
        self.pdf.image(os.path.join('.', 'imagens', nome), x=x if x is not None else self.pdf.l_margin,
                       y=y, w=largura, h=largura * proporcao)

    def pagina_resumo(self, dia_inicial, dia_final, dias_totais_backtest):

        pdf = self.pdf
        pdf.add_page()
        pdf.set_font('Times', 'B', 22)
        pdf.set_text_color(*AZUL)
        pdf.cell(0, 10, 'Relatório de backtest', 0, 1)
        pdf.set_font('Helvetica', '', 9.5)
        pdf.set_text_color(*CINZA)
        pdf.cell(0, 5.5, _t(f'Modelo: {os.path.splitext(self.nome_arquivo)[0]}'), 0, 1)
        pdf.cell(0, 5.5, _t(f'Período: {dia_inicial:%d/%m/%Y} a {dia_final:%d/%m/%Y}  |  '
                            f'{dias_totais_backtest} pregões'), 0, 1)
        pdf.set_text_color(0, 0, 0)
        pdf.secao('Retorno acumulado')
        self._imagem('rent_acum.png', self.largura, proporcao=4 / 7)
        pdf.secao('Estatísticas de retorno, risco e operação')
        linhas = [[a, b, c, d] for (a, b), (c, d) in zip(self.risco_retorno, self.trade)]
        pdf.tabela(['Retorno e risco', '', 'Operação', ''], linhas, [62, 32, 62, 32],
                   ['L', 'R', 'L', 'R'], tamanho=8.5)

    def pagina_quedas(self):

        pdf = self.pdf
        pdf.add_page()
        pdf.secao('Underwater')
        self._imagem('grafico_underwater.png', self.largura, proporcao=4.5 / 7)
        pdf.secao('Eventos de estresse e piores janelas')
        linhas = [[a, b if b == '-' else _pct(b), c, _pct(d)]
                  for (a, b), (c, d) in zip(self.estresse, self.sem_lucro)]
        pdf.tabela(['Evento', 'Retorno', 'Janela', 'Pior retorno'], linhas, [62, 32, 62, 32],
                   ['L', 'R', 'L', 'R'], tamanho=8.5)
        pdf.ln(2)
        pdf.paragrafo('Eventos marcados com "-" ficam fora do período do backtest.', 8)

    def pagina_retornos(self):

        self.pdf.add_page()
        self.pdf.secao('Retorno ano a ano')
        self._imagem('grafico_ano.png', self.largura, proporcao=4 / 8.75)
        self.pdf.secao('Retorno mês a mês')
        self._imagem('grafico_mes.png', self.largura, proporcao=4 / 8.75)

    def pagina_janelas_moveis(self):

        pdf = self.pdf
        pdf.add_page()
        pdf.secao('Janelas móveis de retorno e alfa sobre o IBOV')
        largura = (self.largura - 6) / 2
        y = pdf.get_y()
        for periodo in ['12M', '24M', '36M']:
            self._imagem(f'janela_movel_{periodo}_retorno_movel.png', largura, y=y, proporcao=0.8)
            self._imagem(f'janela_movel_{periodo}_alfa.png', largura, x=pdf.l_margin + largura + 6,
                         y=y, proporcao=0.8)
            y += largura * 0.8 + 3
