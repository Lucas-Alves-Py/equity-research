"""Resumo do fundo fictício mensal para o painel do QuantHub.

Lê as decisões da agenda e o snapshot mais recente gravado por
`execucao_mensal`; não recalcula NAV. O JSON resultante é servido pela rota
/api/systematic-research/fundo. Se a última rodada falhou, o resumo mantém a
última série calculada e expõe o erro, em vez de esconder o bloqueio.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from carteira.agenda import mandato_mensal


def _ler(caminho: Path) -> dict:
    return json.loads(caminho.read_text(encoding='utf-8'))


def _decisoes(pasta: Path) -> list[dict]:
    # Uma decisão por mês: a última validada, como em execucao_mensal;
    # sem validação no mês, a última tentativa mostra o bloqueio.
    por_mes = {}
    for caminho in sorted(pasta.glob('agenda-????-??-*.json')):
        r = _ler(caminho)
        mes = caminho.name[len('agenda-'):len('agenda-YYYY-MM')]
        valida = r.get('status') == 'selecao_tecnica_validada_para_treino'
        if valida or not por_mes.get(mes, {}).get('valida'):
            por_mes[mes] = {'valida': valida, 'caminho': caminho, 'r': r}
    decisoes = []
    for mes in sorted(por_mes):
        caminho, r = por_mes[mes]['caminho'], por_mes[mes]['r']
        alvo = caminho.with_name(caminho.stem + '-alvo_pendente.csv')
        posicoes = []
        if alvo.exists():
            posicoes = [{'ticker': t, 'weight': float(p)} for t, p in
                        pd.read_csv(alvo)[['ticker', 'peso']].itertuples(index=False)]
        decisoes.append({'decisionDate': r.get('data_decisao'),
                         'signalDate': r.get('data_sinal'),
                         'status': r.get('status'),
                         'error': r.get('erro'),
                         'executionDate': None,
                         'positions': posicoes})
    return decisoes


def resumir_fundo(pasta_relatorios) -> dict:
    pasta = Path(pasta_relatorios)
    regra = mandato_mensal()
    decisoes = _decisoes(pasta)
    snapshots = sorted(pasta.glob('fundo-*.json'))
    ultimo = _ler(snapshots[-1]) if snapshots else {}
    calculados = [c for c in snapshots
                  if _ler(c).get('status') == 'nav_nocional_calculado']
    resumo = {'mandate': {'id': regra['id'], 'model': regra['modelo_fatores'],
                          'start': str(regra['inicio_mandato']),
                          'decisionDay': int(regra['dia_decisao']),
                          'monthlyContribution': float(regra['aporte_ficticio_brl'])},
              'status': (ultimo.get('status') if ultimo else
                         'pendente_D+1' if decisoes else 'sem_decisao'),
              'error': ultimo.get('erro'),
              'calculatedAt': ultimo.get('calculado_em'),
              'lastSession': ultimo.get('ultima_sessao_b3'),
              'summary': None, 'series': [], 'decisions': decisoes}
    if not calculados:
        return resumo
    base = calculados[-1].with_suffix('')
    patrimonio = pd.read_parquet(f'{base}-patrimonio.parquet')
    indice = pd.read_parquet(f'{base}-indice.parquet')
    operacoes = pd.read_parquet(f'{base}-operacoes_nocionais.parquet')
    execucao = {str(d): str(e) for d, e in operacoes[
        ['data_decisao', 'data_execucao']].drop_duplicates().itertuples(index=False)}
    for decisao in decisoes:
        decisao['executionDate'] = execucao.get(decisao['decisionDate'])
    fim = patrimonio.iloc[-1]
    resumo['summary'] = {
        'firstExecution': str(patrimonio['data'].iat[0]),
        'lastSession': str(fim['data']),
        'sessions': len(patrimonio),
        'latestIndex': float(fim['indice']),
        'totalReturn': float(fim['indice'] / patrimonio['indice'].iat[0] - 1),
        'maxDrawdown': float(indice['drawdown'].min()),
        'currentDrawdown': float(indice['drawdown'].iat[-1]),
        'equity': float(fim['patrimonio_ficticio']),
        'contributed': float(fim['total_aportado']),
        'gain': float(fim['ganho_ficticio'])}
    resumo['series'] = [{'date': str(r.data), 'index': float(r.indice),
                         'equity': float(r.patrimonio_ficticio),
                         'contributed': float(r.total_aportado)}
                        for r in patrimonio.itertuples(index=False)]
    return resumo


def exportar_resumo(pasta_relatorios, destino) -> str:
    resumo = resumir_fundo(pasta_relatorios)
    resumo['generatedAt'] = str(pd.Timestamp.now(tz='America/Sao_Paulo'))
    destino = Path(destino)
    temporario = destino.with_suffix('.tmp')
    temporario.write_text(json.dumps(resumo, ensure_ascii=False, indent=2,
                                     default=str), encoding='utf-8')
    temporario.replace(destino)
    return str(destino)
