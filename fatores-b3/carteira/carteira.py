"""Nucleo da carteira sistematica modelo.

O produto e uma carteira publicada, nao um fundo. O capital de referencia
serve apenas para transformar pesos em quantidades executaveis e nao representa
patrimonio administrado, cota ou dinheiro real.

Principios de projeto
---------------------
1. O ranking historico preserva os tres fatores congelados. Empates recebem
   desempate deterministico para que a carteira contenha exatamente 10 nomes.

2. O ledger e APPEND-ONLY. Nada e reescrito. O historico do git e a auditoria:
   cada carteira e commitada ANTES do periodo que ela vai render.

3. Quantidade INTEIRA de acoes, com sobra em caixa. E o que da para executar
   de verdade, e mantem o track record honesto se um dia virar dinheiro real.

4. Execucao em D+1: o sinal sai no fechamento de D, a compra ocorre no
   fechamento do pregao seguinte. Mesma disciplina do backtest.
"""

import os
import datetime as dt
import math
import tempfile

import pandas as pd
import yaml

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DADOS = os.path.join(RAIZ, 'dados')
LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ledger')
CONFIG = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'config', 'modelos.yaml')


# --------------------------------------------------------------------------
# dados
# --------------------------------------------------------------------------

def _ler(nome, colunas=None):
    df = pd.read_parquet(os.path.join(DADOS, f'{nome}.parquet'), columns=colunas)
    df['data'] = pd.to_datetime(df['data']).dt.date
    return df


def carregar_painel(indicadores, liquidez):
    """Mesmo painel que `calculadora_factor.pegando_dados` monta."""

    cot = _ler('cotacoes', ['data', 'ticker', 'preco_fechamento_ajustado'])
    cot = cot[cot['preco_fechamento_ajustado'] > 0]     # bug GOLL54
    cot['ticker'] = cot['ticker'].astype(str)

    vol = _ler('volume_mediano')[['data', 'ticker', 'valor']]
    vol.columns = ['data', 'ticker', 'volume']
    vol['ticker'] = vol['ticker'].astype(str)

    painel = cot.merge(vol, on=['data', 'ticker'], how='inner')

    for ind in indicadores:
        d = _ler(ind)[['data', 'ticker', 'valor']]
        d.columns = ['data', 'ticker', ind]
        d['ticker'] = d['ticker'].astype(str)
        d[ind] = d[ind].astype(float)
        painel = painel.merge(d, on=['data', 'ticker'], how='inner')

    return painel.dropna()


# --------------------------------------------------------------------------
# selecao -- espelha criando_carteiras()
# --------------------------------------------------------------------------

def selecionar(painel, data, indicadores, liquidez, n_ativos):
    """Devolve os n_ativos escolhidos na data, com peso igual."""

    obrigatorias = {'data', 'ticker', 'preco_fechamento_ajustado', 'volume', *indicadores}
    faltantes = obrigatorias - set(painel.columns)
    if faltantes:
        raise ValueError(f"painel sem colunas obrigatorias: {sorted(faltantes)}")
    data = pd.Timestamp(data).date()
    df = painel[pd.to_datetime(painel['data'], errors='coerce').dt.date.eq(data)].copy()
    if df.empty:
        raise ValueError(f"sem dados no painel para {data}")

    if df['ticker'].duplicated().any():
        raise ValueError(f"tickers duplicados no painel em {data}")
    valores = ['preco_fechamento_ajustado', 'volume', *indicadores]
    for coluna in valores:
        df[coluna] = pd.to_numeric(df[coluna], errors='coerce')
    if df[valores].isna().any().any() or not df[valores].apply(
            lambda serie: serie.map(math.isfinite)).all().all():
        raise ValueError(f"fatores, volumes ou precos ausentes/invalidos em {data}")
    if (df['preco_fechamento_ajustado'] <= 0).any() or (df['volume'] < 0).any():
        raise ValueError(f"preco ou volume invalido em {data}")

    df = df[df['volume'] > liquidez]

    # CNPJ e a identidade preferida; prefixo preserva a regra do backtest
    # quando o painel historico nao traz security master.
    df = df.assign(prefixo=(df['cnpj'].astype(str) if 'cnpj' in df.columns
                            else df['ticker'].str[:4]))
    # Em empate de liquidez, o ticker alfabeticamente menor prevalece.
    df = (df.sort_values(['prefixo', 'volume', 'ticker'],
                         ascending=[True, False, True])
            .drop_duplicates('prefixo', keep='first')
            .drop(columns='prefixo'))
    if len(df) < n_ativos:
        raise ValueError(f"universo elegivel insuficiente: {len(df)} < {n_ativos}")

    df = df.assign(rank_final=0.0)
    for ind, ordem in indicadores.items():
        crescente = (ordem.lower() == 'crescente')
        df[f'rank_{ind}'] = df[ind].rank(ascending=crescente)
        df['rank_final'] += df[f'rank_{ind}']

    df = df.sort_values(['rank_final', 'ticker']).head(n_ativos).copy()
    df['posicao'] = range(1, n_ativos + 1)
    escolhidas = df
    escolhidas['peso'] = 1.0 / n_ativos

    cols = ['data', 'ticker', 'preco_fechamento_ajustado', 'volume',
            'rank_final', 'posicao', 'peso'] + list(indicadores)
    return escolhidas.sort_values('posicao')[cols].reset_index(drop=True)


def validar_painel_atual(painel, indicadores, liquidez, n_ativos,
                        referencia=None, max_atraso_pregoes=2,
                        minimo_empresas=30):
    """Bloqueia uma indicacao atual baseada em dados velhos ou incompletos.

    A idade e medida em dias uteis como limite conservador. A fonte de precos
    deve validar o calendario oficial da B3 antes de construir este painel.
    """
    referencia = pd.Timestamp(referencia or dt.date.today()).date()
    if painel.empty:
        raise ValueError('painel atual vazio')
    if not {'data', 'ticker', 'fonte_preco', 'fonte_fundamentos',
            *indicadores}.issubset(painel.columns):
        raise ValueError('painel atual sem fatores ou proveniencia obrigatoria')
    datas = pd.to_datetime(painel['data'], errors='coerce')
    if datas.isna().any() or datas.dt.date.nunique() != 1:
        raise ValueError('painel atual precisa conter exatamente uma data de sinal')
    data = datas.dt.date.iloc[0]
    if data > referencia:
        raise ValueError(f'data de sinal futura: {data}')
    if data.weekday() >= 5:
        raise ValueError(f'data de sinal fora de dia util: {data}')
    atraso = len(pd.bdate_range(data + dt.timedelta(days=1), referencia))
    if atraso > max_atraso_pregoes:
        raise ValueError(f'painel desatualizado: {data}; {atraso} dias uteis de atraso')
    if painel[['fonte_preco', 'fonte_fundamentos']].isna().any().any():
        raise ValueError('painel atual sem proveniencia em parte do universo')
    # A selecao valida os numeros antes de calcular qualquer ranking.
    selecionar(painel, data, indicadores, liquidez, n_ativos)
    liquidos = painel[pd.to_numeric(painel['volume'], errors='coerce') > liquidez]
    empresas = liquidos['ticker'].astype(str).str[:4].nunique()
    if empresas < max(n_ativos, minimo_empresas):
        raise ValueError(f'cobertura insuficiente: {empresas} empresas liquidas')
    return data


class ExecucaoPendente(RuntimeError):
    """Decisao registrada, sem pregao posterior disponivel para executar."""


def precos_execucao(tickers, data_referencia, cotacoes=None):
    """Preco BRUTO observavel no primeiro pregao APOS a referencia (D+1).

    Tres decisoes aqui, todas deliberadas:

    1. D+1, nao D. O sinal sai no fechamento de D; comprar ao preco de D seria
       usar a informacao que gerou a decisao. E look-ahead de um dia, e e
       sistematico. A engine do backtest ja faz assim (logica de `delay`).

    2. Preco BRUTO do COTAHIST, nao ajustado. Ordem se executa ao preco que
       aparece na tela. Preco ajustado muda retroativamente quando sai
       provento -- serve para medir retorno, nunca para registrar execucao.

    3. A referencia e a data da DECISAO, que so coincide com a data do sinal
       quando o modelo publica no ritmo dos dados. Se o modelo muda hoje sobre
       um painel antigo, D+1 e o pregao seguinte a HOJE -- executar no dia
       seguinte ao sinal antigo importaria meses de hindsight.
    """

    data_referencia = pd.Timestamp(data_referencia).date()
    if cotacoes is None:
        from carteira import cotahist
        inicio = data_referencia + dt.timedelta(days=1)
        fim = min(dt.date.today(), data_referencia + dt.timedelta(days=14))
        partes = []
        for ano in range(inicio.year, fim.year + 1):
            caminho = cotahist.baixar(ano)
            partes.append(cotahist.parsear_periodo(caminho, inicio, fim))
        ch = pd.concat(partes, ignore_index=True) if partes else pd.DataFrame()
    else:
        ch = cotacoes.copy()
    if ch.empty:
        raise ExecucaoPendente(f'sem pregao B3 observado apos {data_referencia}')
    ch['data'] = pd.to_datetime(ch['data']).dt.date

    posteriores = sorted(d for d in ch['data'].unique() if d > data_referencia)
    if not posteriores:
        raise ExecucaoPendente(
            f"sem pregao apos {data_referencia} no COTAHIST -- decisao "
            f"registrada, execucao pendente. Baixe o COTAHIST mais recente.")

    d1 = posteriores[0]
    if d1 > dt.date.today():
        raise ExecucaoPendente(f'pregao D+1 {d1} ainda nao ocorreu')
    precos = ch[(ch['data'] == d1) & (ch['ticker'].isin(tickers))]
    if precos['ticker'].duplicated().any():
        raise RuntimeError(f'cotacoes duplicadas no pregao D+1 {d1}')
    fechamento = pd.to_numeric(precos['fechamento'], errors='coerce')
    if fechamento.isna().any() or not fechamento.map(math.isfinite).all() or fechamento.le(0).any():
        raise RuntimeError(f'preco bruto invalido no pregao D+1 {d1}')
    precos = precos.set_index('ticker')['fechamento']

    faltando = set(tickers) - set(precos.index)
    if faltando:
        raise RuntimeError(f"sem cotacao em {d1} para {sorted(faltando)}")

    return d1, precos


# --------------------------------------------------------------------------
# carteira sistematica
# --------------------------------------------------------------------------

class CarteiraSistematica:

    def __init__(self, config=CONFIG, painel_atual=None, mandato_mensal=False):
        with open(config, encoding='utf-8') as f:
            self.cfg = yaml.safe_load(f)
        self.modelo = self.cfg['modelos'][0]
        self.indicadores = self.modelo['indicadores']
        self.liquidez = self.modelo['filtro_liquidez_mm'] * 1_000_000
        self.n_ativos = self.modelo['numero_ativos']
        self.capital_referencia = self.cfg['carteira']['capital_referencia']
        self._mandato_mensal = mandato_mensal or painel_atual is not None
        if self._mandato_mensal:
            from carteira.agenda import mandato_mensal as ler_mandato
            self.mandato = ler_mandato()
            self.ledger_dir = os.path.join(LEDGER, 'mensal_dia5_v1')
        else:
            self.mandato = None
            self.ledger_dir = LEDGER
        os.makedirs(self.ledger_dir, exist_ok=True)
        self._painel = None
        self._painel_atual = painel_atual.copy() if painel_atual is not None else None

    @property
    def painel(self):
        if self._painel is None:
            self._painel = (self._painel_atual if self._painel_atual is not None
                            else carregar_painel(list(self.indicadores), self.liquidez))
        return self._painel

    def datas_disponiveis(self):
        return sorted(self.painel['data'].unique())

    def carteira_alvo(self, data=None, referencia=None):
        """Carteira historica por data explicita ou indicacao atual validada."""
        datas = self.datas_disponiveis()
        if not datas:
            raise ValueError('painel vazio')
        if data is None or self._painel_atual is not None:
            data_atual = validar_painel_atual(
                self.painel, self.indicadores, self.liquidez, self.n_ativos,
                referencia=referencia)
            if data is not None and pd.Timestamp(data).date() != data_atual:
                raise ValueError('data solicitada difere do painel atual')
            data = data_atual
        else:
            data = pd.Timestamp(data).date()
        return selecionar(self.painel, data, self.indicadores,
                          self.liquidez, self.n_ativos)

    # ---------------------------------------------------------------- ledger

    def _caminho(self, nome):
        return os.path.join(self.ledger_dir, f'{nome}.parquet')

    def _append(self, nome, df):
        caminho = self._caminho(nome)
        if os.path.exists(caminho):
            df = pd.concat([pd.read_parquet(caminho), df], ignore_index=True)
        with tempfile.NamedTemporaryFile(dir=self.ledger_dir, suffix='.parquet',
                                         delete=False) as tmp:
            temporario = tmp.name
        try:
            df.to_parquet(temporario, index=False)
            os.replace(temporario, caminho)
        finally:
            if os.path.exists(temporario):
                os.remove(temporario)
        return len(df)

    def registrar_carteira(self, data=None, capital_referencia=None, versao=1,
                           motivo='', data_decisao=None,
                           publicacao_aprovada=None):
        """Publica a carteira-alvo e sua implementacao de referencia. APPEND-ONLY.

        Sinal no fechamento de D, execucao no fechamento de D+1, ao preco
        bruto observavel. Nunca sobrescreve: uma correcao entra como nova
        versao, e a anterior continua visivel para auditoria.

        `data_decisao` separa QUANDO o modelo decidiu de QUANDO os dados sao.
        Sem ela, uma mudanca de modelo feita hoje sobre um painel parado seria
        registrada como executada no dia seguinte ao painel -- track record
        retroativo. Se ainda nao houve pregao apos a decisao, a carteira e
        gravada PENDENTE: composicao publicada, execucao em aberto.
        """

        if self._mandato_mensal:
            raise ValueError('mandato mensal usa ledger nocional; registro de quantidade inteira desativado')

        if self._painel_atual is None:
            raise ValueError('registro atual exige painel PIT injetado; dados legados sao historicos')
        if self._painel_atual.attrs.get('fatores_reconciliados') is not True:
            raise ValueError('fatores ainda sem reconciliacao historica suficiente para registro')
        if publicacao_aprovada is False:
            raise ValueError('publicacao explicitamente bloqueada')
        data_decisao = pd.Timestamp(data_decisao or dt.date.today()).date()
        if data_decisao != dt.date.today():
            raise ValueError('decisao prospectiva deve ser registrada na data corrente')
        from carteira import cotahist
        inicio_mes = data_decisao.replace(day=1)
        arquivo = cotahist.baixar(data_decisao.year)
        calendario = cotahist.parsear_periodo(arquivo, inicio_mes,
                                              data_decisao)['data']
        sessoes = sorted({pd.Timestamp(d).date() for d in calendario})
        alvo_mensal = min((d for d in sessoes
                           if d.month == data_decisao.month and
                           d.day >= int(self.mandato['dia_decisao'])),
                          default=None)
        if alvo_mensal is None or data_decisao < alvo_mensal:
            raise ValueError('dia de giro mensal ainda nao observado na B3')
        if alvo_mensal < pd.Timestamp(self.mandato['inicio_mandato']).date():
            raise ValueError('giro deste mes anterior ao inicio do mandato mensal')
        alvo = self.carteira_alvo(data, referencia=data_decisao)
        data_sinal = alvo['data'].iat[0]
        if data_decisao < data_sinal:
            raise ValueError('data da decisao anterior ao sinal')
        caminho = self._caminho('carteiras')
        if capital_referencia is None:
            if os.path.exists(caminho) and not pd.read_parquet(caminho).empty:
                raise ValueError('giros seguintes exigem patrimônio e caixa verificados; aporte nao e retorno')
            capital_referencia = float(self.mandato['aporte_ficticio_brl'])
        else:
            capital_referencia = float(capital_referencia)
        if not math.isfinite(capital_referencia) or capital_referencia <= 0:
            raise ValueError('capital de referencia precisa ser positivo')

        if os.path.exists(caminho):
            anterior = pd.read_parquet(caminho)
            if {'modelo', 'data_sinal', 'versao', 'data_decisao'}.issubset(anterior.columns):
                duplicado = (anterior['modelo'].eq(self.modelo['id']) &
                             pd.to_datetime(anterior['data_sinal']).dt.date.eq(data_sinal) &
                             anterior['versao'].eq(versao) &
                             pd.to_datetime(anterior['data_decisao']).dt.date.eq(data_decisao))
                if duplicado.any():
                    raise ValueError('publicacao desta versao e decisao ja existe')

        try:
            data_exec, precos = precos_execucao(list(alvo['ticker']),
                                                data_decisao)
        except ExecucaoPendente:
            data_exec, precos = None, None

        if precos is None:
            alvo['preco_execucao'] = float('nan')
            alvo['quantidade'] = float('nan')
            alvo['financeiro'] = float('nan')
            caixa_referencia = None
        else:
            alvo['preco_execucao'] = alvo['ticker'].map(precos)
            alvo['financeiro_alvo'] = capital_referencia * alvo['peso']
            # quantidade inteira -- o que da para executar de verdade
            alvo['quantidade'] = (alvo['financeiro_alvo'] //
                                  alvo['preco_execucao']).astype(int)
            alvo['financeiro'] = alvo['quantidade'] * alvo['preco_execucao']
            caixa_referencia = capital_referencia - alvo['financeiro'].sum()

        registro = alvo[['ticker', 'quantidade', 'preco_execucao',
                         'preco_fechamento_ajustado', 'financeiro',
                         'peso', 'posicao']].copy()
        registro.columns = ['ticker', 'quantidade', 'preco_execucao',
                            'preco_sinal', 'financeiro', 'peso_alvo',
                            'posicao_ranking']
        registro.insert(0, 'modelo', self.mandato['id'])
        registro['modelo_fatores'] = self.modelo['id']
        registro['versao_metodologia'] = self._painel_atual.attrs.get('versao_metodologia')
        registro['data_giro_planejada'] = alvo_mensal
        registro.insert(1, 'data_sinal', data_sinal)
        registro.insert(2, 'data_execucao', data_exec)
        registro['data_decisao'] = data_decisao
        registro['versao'] = versao
        registro['capital_referencia'] = capital_referencia
        registro['status_execucao'] = ('PENDENTE' if data_exec is None else
                                      'EXECUTADA')
        registro['fonte_execucao'] = 'B3 COTAHIST' if data_exec else None
        registro['motivo'] = motivo
        registro['registrado_em'] = dt.datetime.now().isoformat(timespec='seconds')

        n = self._append('carteiras', registro)
        return registro, caixa_referencia, n

    def concluir_execucao_pendente(self, data_decisao, cotacoes=None):
        """Acrescenta versão D+1 de uma decisão já publicada e aprovada.

        A linha PENDENTE permanece no ledger. A execução usa a composição,
        pesos e capital de referência originalmente registrados, sem reranking
        com fatores conhecidos depois da decisão.
        """
        if self._mandato_mensal:
            raise ValueError('execucao de quantidade inteira pertence apenas ao ledger legado')
        caminho = self._caminho('carteiras')
        if not os.path.exists(caminho):
            raise ValueError('ledger de carteiras ausente')
        anterior = pd.read_parquet(caminho)
        obrigatorias = {'modelo', 'data_decisao', 'data_sinal', 'ticker',
                        'versao', 'status_execucao', 'capital_referencia',
                        'peso_alvo', 'posicao_ranking'}
        if faltantes := obrigatorias - set(anterior.columns):
            raise ValueError(f'ledger sem colunas de execucao: {sorted(faltantes)}')
        decisao = pd.Timestamp(data_decisao).date()
        linhas = anterior[
            anterior['modelo'].eq(self.modelo['id']) &
            pd.to_datetime(anterior['data_decisao'], errors='coerce').dt.date.eq(decisao)
        ].copy()
        if linhas.empty:
            raise ValueError(f'sem decisao publicada em {decisao}')
        if linhas['status_execucao'].eq('EXECUTADA').any():
            raise ValueError(f'decisao {decisao} ja executada')
        versao_origem = int(pd.to_numeric(linhas['versao']).max())
        pendente = linhas[linhas['versao'].eq(versao_origem)].copy()
        if not pendente['status_execucao'].eq('PENDENTE').all():
            raise ValueError('versao vigente nao esta pendente')
        if len(pendente) != self.n_ativos or pendente['ticker'].duplicated().any():
            raise ValueError('decisao pendente sem composicao exata')
        capitais = pd.to_numeric(pendente['capital_referencia'], errors='coerce')
        if capitais.isna().any() or capitais.nunique() != 1 or capitais.iloc[0] <= 0:
            raise ValueError('capital de referencia pendente inconsistente')
        if pendente['data_sinal'].nunique() != 1:
            raise ValueError('mais de um sinal na mesma decisao')
        data_exec, precos = precos_execucao(
            pendente['ticker'].tolist(), decisao, cotacoes=cotacoes)
        registro = pendente.copy()
        registro['data_execucao'] = data_exec
        registro['preco_execucao'] = registro['ticker'].map(precos)
        registro['quantidade'] = (
            (capitais.iloc[0] * registro['peso_alvo']) //
            registro['preco_execucao']).astype(int)
        registro['financeiro'] = registro['quantidade'] * registro['preco_execucao']
        registro['status_execucao'] = 'EXECUTADA'
        registro['fonte_execucao'] = 'B3 COTAHIST'
        registro['versao'] = versao_origem + 1
        registro['versao_origem'] = versao_origem
        registro['motivo'] = 'execucao_D+1_de_decisao_pendente'
        registro['registrado_em'] = dt.datetime.now().isoformat(timespec='seconds')
        self._append('carteiras', registro)
        return registro

    # ------------------------------------------------------------- validacao

    def verificar_equivalencia(self, data, esperado):
        """Confere que a selecao bate com a do backtest para uma data conhecida."""
        obtido = set(self.carteira_alvo(data)['ticker'])
        return obtido == set(esperado), sorted(obtido)


if __name__ == "__main__":
    f = CarteiraSistematica()
    datas = f.datas_disponiveis()
    print(f"modelo congelado : {f.modelo['id']} (em {f.modelo['congelado_em']})")
    print(f"indicadores      : {f.indicadores}")
    print(f"painel           : {datas[0]} a {datas[-1]} ({len(datas)} datas)\n")

    # teste de equivalencia com a carteira que o backtest produziu em 2025-10-15
    ok, tickers = f.verificar_equivalencia(
        dt.date(2025, 10, 15), ['LAVV3', 'PGMN3', 'SEER3', 'TGMA3', 'WIZC3'])
    print(f"equivalencia com o backtest em 2025-10-15: {'OK' if ok else 'DIVERGIU'}")
    print(f"   selecionadas: {tickers}\n")

    print("carteira alvo na ultima data disponivel:")
    print(f.carteira_alvo()[['data', 'ticker', 'posicao', 'preco_fechamento_ajustado',
                             'EV_EBIT', 'ROIC', 'momento_12_meses']].to_string(index=False))
