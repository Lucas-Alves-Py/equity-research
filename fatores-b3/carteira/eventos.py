"""Deteccao de eventos societarios a partir da serie de precos brutos.

Para que serve
--------------
O COTAHIST da B3 traz preco bruto. Grupamentos e desdobramentos aparecem como
retornos gigantes e falsos -- um grupamento de 1:20 vira "+1.900%" e lidera
qualquer ranking de momentum. Pior que um NaN: nao ha sinal de erro.

DECISAO DE PROJETO: este modulo SINALIZA, nao corrige.
------------------------------------------------------
A tentacao e corrigir automaticamente. Nao fazemos, e a razao esta medida.

No universo liquido de 2025 (volume > R$ 2 mi), o detector produziu:
    3 verdadeiros positivos, 2 falsos positivos, 6 perdidos

Um dos falsos positivos foi AMBP3 em 2025-10-03, com queda de 49% num dia --
que foi uma **perda real** (crise de divida da Ambipar), nao um grupamento.
Corrigir automaticamente transformaria essa perda em evento societario e a
apagaria do backtest, inflando o retorno do modelo.

Errar para o lado de sinalizar demais e barato: alguem olha. Errar para o lado
de corrigir uma queda real e caro e invisivel.

Por que a deteccao e dificil
----------------------------
A razao observada mistura o evento com a variacao normal do dia. AMBP3 fez um
grupamento de 1:10 num dia em que subiu 18,7%, entao a razao ficou 0,1187 e
nao 0,1000 -- longe de um numero redondo. Nao ha limiar que separe isso de
forma limpa.

A correcao correta usa dados de eventos societarios (/desdobramentos,
/bonificacoes e /proventos da Fintz, ou os avisos da propria B3), nao
heuristica sobre preco.

Onde a exposicao real e pequena
-------------------------------
Dos 22 eventos de 2025, apenas 9 ocorreram em tickers com volume acima de
R$ 2 milhoes. O filtro de liquidez do modelo ja e a principal defesa: a maior
parte dos grupamentos acontece em acoes de centavos, que o modelo nao compra.
"""

import numpy as np
import pandas as pd

# Calibrado contra a serie ajustada da Fintz em 2025 (verdade conhecida).
LIMIAR_RAZAO = 1.8          # 1.8x para cima ou para baixo


def _desvio_do_inteiro(razao):
    """Distancia relativa ao inteiro mais proximo, em %.

    Um evento puro de N:1 daria desvio zero. Na pratica a razao observada
    mistura o evento com a variacao normal do dia -- AMBP3 fez 1:10 num dia
    em que subiu 18,7%, resultando em 0,1187 e nao 0,1000, ou seja 18,7% de
    desvio. Por isso NAO ha limiar limpo, e este numero e informativo em vez
    de virar uma flag binaria: quanto menor, mais parece evento societario;
    quanto maior, mais parece movimento real de preco.
    """
    razao = np.asarray(razao, dtype=float)  # array de objetos quebrava np.round
    with np.errstate(divide='ignore', invalid='ignore'):
        x = np.where(razao > 1, razao, np.divide(1.0, razao))
    alvo = np.round(x)
    return np.where(alvo >= 2, np.abs(x - alvo) / alvo * 100, np.nan)


def detectar(cotacoes, coluna_preco='fechamento', volume_minimo=None):
    """Sinaliza candidatos a evento societario.

    cotacoes precisa ter: data, ticker, <coluna_preco> e, se for filtrar por
    liquidez, volume_negociado.

    Devolve um DataFrame com um candidato por linha, ordenado por severidade.
    Nada e alterado na serie de entrada.
    """

    df = cotacoes.sort_values(['ticker', 'data']).copy()
    df = df[df[coluna_preco] > 0]                    # preco zerado quebra a razao

    df['preco_anterior'] = df.groupby('ticker')[coluna_preco].shift()
    df['razao'] = df[coluna_preco] / df['preco_anterior']
    df = df.dropna(subset=['razao'])

    if volume_minimo is not None:
        mediano = df.groupby('ticker')['volume_negociado'].transform(
            lambda s: s.rolling(21, min_periods=5).median())
        df = df[mediano > volume_minimo]

    grande = (df['razao'] > LIMIAR_RAZAO) | (df['razao'] < 1 / LIMIAR_RAZAO)
    cand = df[grande].copy()
    cand['proporcao_provavel'] = np.where(
        cand['razao'] > 1,
        cand['razao'].round().astype('Int64').astype(str) + ':1',
        (1 / cand['razao']).round().astype('Int64').astype(str) + ':1 (grupamento)')
    cand['desvio_do_inteiro_%'] = _desvio_do_inteiro(cand['razao'].values)
    cand['severidade'] = np.maximum(cand['razao'], 1 / cand['razao'])

    cols = ['data', 'ticker', 'preco_anterior', coluna_preco, 'razao',
            'proporcao_provavel', 'desvio_do_inteiro_%', 'severidade']
    return cand[cols].sort_values('severidade', ascending=False).reset_index(drop=True)


def relatorio(candidatos, minimo_para_alertar=1):
    """Texto curto para inspecao humana."""

    if candidatos.empty:
        return "Nenhum candidato a evento societario."

    d = candidatos['desvio_do_inteiro_%']
    linhas = [
        f"{len(candidatos)} candidatos a evento societario",
        f"  {(d < 5).sum():3d} com desvio < 5%  do inteiro  (forte indicio de evento)",
        f"  {((d >= 5) & (d < 15)).sum():3d} com desvio 5-15%             (ambiguo)",
        f"  {(d >= 15).sum():3d} com desvio > 15%            (pode ser preco real)",
        "",
        "REVISAR ANTES DE USAR EM MOMENTUM:",
    ]
    for _, c in candidatos.head(15).iterrows():
        dv = c['desvio_do_inteiro_%']
        linhas.append(
            f"  {c['ticker']:8s} {str(c['data'])}  "
            f"{c['preco_anterior']:9.2f} -> {c[candidatos.columns[3]]:9.2f}  "
            f"{c['proporcao_provavel']:22s} desvio {dv:5.1f}%")
    if len(candidatos) > 15:
        linhas.append(f"  ... e mais {len(candidatos) - 15}")
    return "\n".join(linhas)


if __name__ == "__main__":
    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from cotahist import parsear

    LEDGER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ledger')
    zips = [f for f in os.listdir(LEDGER) if f.upper().endswith('.ZIP')]
    if not zips:
        print("Nenhum COTAHIST em carteira/ledger. Rode cotahist.py primeiro.")
        sys.exit(1)

    for z in sorted(zips):
        print(f"\n{'='*66}\n{z}\n{'='*66}")
        df = parsear(os.path.join(LEDGER, z))

        print("\n--- universo inteiro ---")
        print(relatorio(detectar(df)))

        print("\n--- so no universo liquido (volume > R$ 2 milhoes) ---")
        print(relatorio(detectar(df, volume_minimo=2_000_000)))
