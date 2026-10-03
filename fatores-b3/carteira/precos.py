"""Feed de precos diarios -- independente da Fintz.

Motivo
------
Fundamento e preco tem cadencias diferentes. Balanco sai 4x por ano, entao
atraso de meses no ROIC e correto -- e o que o point-in-time captura. Preco
precisa ser de ontem para acompanhar o indice forward da carteira.

A Fintz esta com os dados parados (arquivos em lote em 2025-11-03, endpoints
por ticker em 2026-06-03). Este modulo cobre a perna de preco enquanto isso.

Fonte
-----
brapi.dev. Historico diario ate 2000, com adjustedClose e volume.

Limites medidos no plano em uso, ambos verificados contra a API:
  - **1 ativo por requisicao**. Pedir 7 devolve 400 ("Seu plano permite no
    maximo 1 ativo(s) por requisicao"). A chave nao libera batch -- eleva o
    limite de chamadas.
  - **range maximo de 3mo**. Em 2026-08-29 a API passou a recusar 1y, 2y, 5y e
    max com "Ranges permitidos: 1d, 5d, 1mo, 3mo". O historico anterior a isso
    permanece no cache; o feed so consegue ESTENDER, nao reconstruir o passado.
    Para serie longa o caminho e o COTAHIST da B3.

Consequencia: as 5 posicoes da carteira custam 5 chamadas (trivial), mas o
universo completo de rebalanceamento (~460 tickers) custaria 460. Para isso o
caminho e o arquivo COTAHIST da B3, que traz todos os tickers de uma vez e nao
tem limite de requisicao.

O adaptador e trocavel: qualquer fonte que devolva
`data | ticker | preco_fechamento_ajustado | volume_negociado` serve.
"""

import os
import time
import datetime as dt

import re

import pandas as pd
import requests

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ_PROJ = os.path.dirname(AQUI)
CACHE = os.path.join(AQUI, 'ledger', 'precos_feed.parquet')
BRAPI = "https://brapi.dev/api/quote/"


def _token():
    """Chave brapi. Procura no .env do projeto e no .env da raiz do repositorio."""
    for caminho, chaves in [(os.path.join(RAIZ_PROJ, '.env'), ('BRAPI_TOKEN', 'token_brapi')),
                            (os.path.join(RAIZ_PROJ, '..', '.env'), ('token_brapi', 'BRAPI_TOKEN'))]:
        if not os.path.exists(caminho):
            continue
        txt = open(caminho, encoding='utf-8-sig').read()
        for k in chaves:
            m = re.search(rf"{k}\s*=\s*['\"]?([^\s'\"]+)", txt)
            if m:
                return m.group(1)
    return None

COLUNAS = ['data', 'ticker', 'preco_fechamento_ajustado', 'volume_negociado']


class FeedBrapi:
    """Cotacoes diarias da B3 via brapi.dev."""

    # O plano gratuito permite 1 ativo por requisicao (testado: 7 devolve 400).
    # A chave nao libera batch -- eleva o limite de chamadas.
    def __init__(self, pausa=0.6, timeout=40, token=None):
        self.pausa = pausa          # cortesia com a API publica
        self.timeout = timeout
        self.token = token or _token()

    def historico(self, ticker, intervalo='1y'):
        """Serie diaria de um ticker. intervalo: 3mo, 1y, 5y, max."""

        params = {'range': intervalo, 'interval': '1d'}
        if self.token:
            params['token'] = self.token
        r = requests.get(BRAPI + ticker, params=params, timeout=self.timeout)
        if r.status_code != 200:
            raise RuntimeError(f"{ticker}: HTTP {r.status_code} {r.text[:120]}")

        res = r.json().get('results') or []
        if not res or not res[0].get('historicalDataPrice'):
            raise RuntimeError(f"{ticker}: sem historico na resposta")

        linhas = []
        for p in res[0]['historicalDataPrice']:
            if p.get('adjustedClose') is None:
                continue
            linhas.append({
                'data': dt.date.fromtimestamp(p['date']),
                'ticker': ticker,
                'preco_fechamento_ajustado': float(p['adjustedClose']),
                'volume_negociado': float(p.get('volume') or 0),
            })
        return pd.DataFrame(linhas, columns=COLUNAS)

    def coletar(self, tickers, intervalo='1y'):
        """Serie de varios tickers. Falha de um nao derruba os demais."""

        partes, falhas = [], []
        for i, t in enumerate(tickers, 1):
            try:
                df = self.historico(t, intervalo)
                partes.append(df)
                print(f"  [{i}/{len(tickers)}] {t:8s} {len(df):4d} pregoes "
                      f"ate {df['data'].max()}", flush=True)
            except Exception as e:
                falhas.append((t, str(e)[:90]))
                print(f"  [{i}/{len(tickers)}] {t:8s} FALHOU: {str(e)[:90]}", flush=True)
            time.sleep(self.pausa)

        if not partes:
            raise RuntimeError("nenhum ticker coletado")
        return pd.concat(partes, ignore_index=True), falhas


# --------------------------------------------------------------------------
# cache local
# --------------------------------------------------------------------------

def salvar(df):
    """Grava no cache, deduplicando por (data, ticker). O novo prevalece."""

    if os.path.exists(CACHE):
        antigo = pd.read_parquet(CACHE)
        df = pd.concat([antigo, df], ignore_index=True)
    df = (df.sort_values('data')
            .drop_duplicates(subset=['data', 'ticker'], keep='last')
            .reset_index(drop=True))
    df.to_parquet(CACHE, index=False)
    return df


def carregar():
    if not os.path.exists(CACHE):
        return pd.DataFrame(columns=COLUNAS)
    df = pd.read_parquet(CACHE)
    df['data'] = pd.to_datetime(df['data']).dt.date
    return df


def cobertura():
    """Resumo do que existe em cache."""
    df = carregar()
    if df.empty:
        return None
    return (df.groupby('ticker')
              .agg(pregoes=('data', 'size'), de=('data', 'min'), ate=('data', 'max'))
              .sort_index())


if __name__ == "__main__":
    import sys
    sys.path.insert(0, AQUI)
    from carteira import CarteiraSistematica

    f = CarteiraSistematica()
    carteira = pd.read_parquet(os.path.join(AQUI, 'ledger', 'carteiras.parquet'))
    tickers = sorted(carteira['ticker'].unique())

    print(f"coletando {len(tickers)} tickers da carteira: {tickers}\n")
    feed = FeedBrapi()
    df, falhas = feed.coletar(tickers, intervalo='3mo')   # teto do plano
    total = salvar(df)

    print(f"\ncache: {len(total)} linhas, {total['ticker'].nunique()} tickers")
    if falhas:
        print("falhas:")
        for t, e in falhas:
            print(f"   {t}: {e}")
    print("\ncobertura:")
    print(cobertura().to_string())


# --------------------------------------------------------------------------
# emenda de series de fontes diferentes
# --------------------------------------------------------------------------

def emendar(base, nova, coluna='preco_fechamento_ajustado'):
    """Junta duas series de precos reescalonando `nova` para casar com `base`.

    Por que reescalonar em vez de concatenar
    ----------------------------------------
    Fontes distintas aplicam ajuste de proventos de formas diferentes. Medimos
    isso: no trecho sobreposto de 2026-06-01 a 2026-06-03, quatro tickers
    batiam exatamente e JHSF3 diferia -1,23% em TODOS os dias -- offset
    multiplicativo constante, tipico de dividendo/JCP tratado de forma
    diferente.

    Concatenar direto injetaria um retorno falso de -1,23% no dia da emenda.
    O fator e estimado na sobreposicao e aplicado a serie nova, preservando
    os retornos de cada fonte e eliminando o degrau artificial.
    """

    saida = []
    for ticker in sorted(set(base['ticker']) | set(nova['ticker'])):
        b = base[base['ticker'] == ticker].sort_values('data')
        n = nova[nova['ticker'] == ticker].sort_values('data')
        if b.empty:
            saida.append(n); continue
        if n.empty:
            saida.append(b); continue

        comum = b[['data', coluna]].merge(n[['data', coluna]], on='data',
                                          suffixes=('_b', '_n'))
        if comum.empty:
            saida.append(b); saida.append(n); continue

        fator = (comum[f'{coluna}_b'] / comum[f'{coluna}_n']).median()
        n = n.copy()
        n[coluna] = n[coluna] * fator

        # a base manda no trecho que ela cobre; a nova entra so depois
        n = n[n['data'] > b['data'].max()]
        saida.append(b); saida.append(n)

    return (pd.concat(saida, ignore_index=True)
              .sort_values(['ticker', 'data'])
              .reset_index(drop=True))
