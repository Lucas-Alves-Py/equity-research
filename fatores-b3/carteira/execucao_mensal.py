"""Conclui o fundo fictício apenas com D+1 e séries totais completas.

Lê alvos publicados pela agenda mensal, calcula NAV unitizado e operações
nocionais fracionárias. Cada cálculo é um artefato imutável; falhas deixam
diagnóstico e preservam os artefatos anteriores. Nenhuma ordem é enviada.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import tempfile

import pandas as pd

from carteira import cotahist
from carteira.fundo_ficticio import simular_fundo_mensal
from carteira.painel_precos import series_ajustadas


def _publicacoes(pasta: Path) -> list[tuple[dict, pd.DataFrame]]:
    por_mes = {}
    for caminho in sorted(pasta.glob('agenda-????-??-*.json')):
        r = json.loads(caminho.read_text(encoding='utf-8'))
        if r.get('status') != 'selecao_tecnica_validada_para_treino':
            continue
        csv = caminho.with_name(caminho.stem + '-alvo_pendente.csv')
        if not csv.exists():
            raise ValueError(f'publicacao sem alvo preservado: {caminho.name}')
        esperado = r.get('alvo_sha256')
        if not esperado or hashlib.sha256(csv.read_bytes()).hexdigest() != esperado:
            raise ValueError(f'alvo mensal sem hash valido: {csv.name}')
        data = pd.Timestamp(r['data_decisao']).date()
        mes = (data.year, data.month)
        por_mes[mes] = (r, pd.read_csv(csv))
    return [por_mes[k] for k in sorted(por_mes)]


def _cotacoes_observadas(inicio: dt.date, fim: dt.date) -> pd.DataFrame:
    partes = []
    for ano in range(inicio.year, fim.year + 1):
        caminho = cotahist.baixar(ano)
        partes.append(cotahist.parsear_periodo(caminho, inicio, fim))
    bruto = pd.concat(partes, ignore_index=True)
    if bruto.empty:
        raise ValueError('COTAHIST sem pregões observados para o fundo mensal')
    return bruto


def calcular_fundo_mensal(pasta_relatorios, as_of=None,
                          cotacoes=None, provedor_series=series_ajustadas) -> dict:
    """Calcula NAV e trades nocionais até a última sessão B3 observada.

    Sem D+1 do primeiro alvo, retorna estado pendente. Falta de preço/evento
    em qualquer posição ativa gera erro e não produz NAV parcial.
    """
    pasta = Path(pasta_relatorios)
    publicacoes = _publicacoes(pasta)
    if not publicacoes:
        return {'status': 'sem_alvos_mensais_publicados'}
    corte = pd.Timestamp(as_of or pd.Timestamp.now(tz='America/Sao_Paulo'))
    inicio = min(pd.Timestamp(r['data_decisao']).date() for r, _ in publicacoes)
    bruto = cotacoes.copy() if cotacoes is not None else _cotacoes_observadas(
        inicio, corte.date())
    bruto['data'] = pd.to_datetime(bruto['data'], errors='coerce').dt.date
    sessoes = sorted({d for d in bruto['data'] if pd.notna(d) and d <= corte.date()})
    if not sessoes:
        raise ValueError('nenhum pregão B3 observado até o corte')
    publicadas = []
    pendentes = []
    for r, alvo in publicacoes:
        decisao = pd.Timestamp(r['data_decisao']).date()
        posteriores = [d for d in sessoes if d > decisao]
        if not posteriores:
            pendentes.append(str(decisao))
            continue
        execucao = posteriores[0]
        if len(alvo) != 10 or alvo['ticker'].duplicated().any():
            raise ValueError(f'alvo mensal inválido na decisão {decisao}')
        bloco = alvo[['ticker', 'peso']].copy()
        bloco['data_decisao'] = decisao
        bloco['data_execucao'] = execucao
        publicadas.append((r, bloco))
    if not publicadas:
        return {'status': 'pendente_D+1', 'decisoes_pendentes': pendentes,
                'ultima_sessao_b3': str(sessoes[-1])}

    alvos = pd.concat([a for _, a in publicadas], ignore_index=True)
    agenda = pd.DataFrame([{
        'data_decisao': pd.Timestamp(r['data_decisao']).date(),
        'data_execucao': a['data_execucao'].iat[0],
        'aporte_ficticio': float(r['aporte_ficticio'])}
        for r, a in publicadas])
    execucoes = sorted(alvos['data_execucao'].unique())
    # Uma única escala de preço ajustado por trecho contínuo de posse.
    # Calcular cada mês separadamente reescalaria a data de transição quando
    # um evento futuro do mesmo ticker cair no trecho seguinte.
    ativos_por_execucao = {d: set(alvos.loc[alvos['data_execucao'].eq(d),
                                            'ticker']) for d in execucoes}
    segmentos = []
    for ticker in sorted(set(alvos['ticker'])):
        aberto = None
        for i, execucao in enumerate(execucoes):
            presente = ticker in ativos_por_execucao[execucao]
            if presente and aberto is None:
                aberto = execucao
            if aberto is not None and not presente:
                segmentos.append((ticker, aberto, execucao))
                aberto = None
        if aberto is not None:
            segmentos.append((ticker, aberto, sessoes[-1]))
    partes = []
    hashes_eventos = []
    for ticker, inicio_trecho, fim_trecho in segmentos:
        serie = provedor_series([ticker], inicio_trecho, fim_trecho,
                                corte.date(), cotacoes=bruto)
        hashes_eventos.append({'ticker': ticker, 'de': str(inicio_trecho),
                               'ate': str(fim_trecho),
                               'sha256_por_emissor': serie.attrs.get(
                                   'eventos_sha256_por_emissor', {})})
        partes.append(serie)
    precos = pd.concat(partes, ignore_index=True)
    if precos.duplicated(['data', 'ticker']).any():
        raise ValueError('séries ajustadas duplicadas entre trechos de posse')
    fundo = simular_fundo_mensal(alvos, precos, agenda)
    return {'status': 'nav_nocional_calculado',
            'indice': fundo['indice'],
            'patrimonio': fundo['patrimonio'],
            'operacoes_nocionais': fundo['operacoes_nocionais'],
            'posicoes': fundo['posicoes'],
            'sha256_entradas': fundo['sha256_entradas'],
            'eventos_sha256_por_intervalo': hashes_eventos,
            'tipo': fundo['tipo'],
            'primeira_execucao': str(execucoes[0]),
            'ultima_sessao_b3': str(sessoes[-1]),
            'decisoes_pendentes': pendentes,
            'n_aportes_executados': len(agenda)}


def atualizar_fundo_mensal(pasta_relatorios, as_of=None,
                           cotacoes=None, provedor_series=series_ajustadas) -> dict:
    """Persiste snapshot imutável do NAV ou diagnóstico de bloqueio."""
    pasta = Path(pasta_relatorios)
    pasta.mkdir(parents=True, exist_ok=True)
    instante = pd.Timestamp.now(tz='America/Sao_Paulo')
    try:
        resultado = calcular_fundo_mensal(pasta, as_of=as_of,
                                         cotacoes=cotacoes,
                                         provedor_series=provedor_series)
    except Exception as exc:
        resultado = {'status': 'nav_bloqueado',
                     'erro': f'{type(exc).__name__}: {exc}'}
    metadados = {k: v for k, v in resultado.items()
                 if not isinstance(v, pd.DataFrame)}
    anteriores = sorted(pasta.glob('fundo-*.json'))
    if anteriores and resultado['status'] in ('nav_nocional_calculado',
                                              'pendente_D+1'):
        ultimo = json.loads(anteriores[-1].read_text(encoding='utf-8'))
        identidade = (('sha256_entradas', 'ultima_sessao_b3')
                      if resultado['status'] == 'nav_nocional_calculado' else
                      ('decisoes_pendentes', 'ultima_sessao_b3'))
        if ultimo.get('status') == resultado['status'] and all(
                ultimo.get(k) == metadados.get(k) for k in identidade):
            return {'status': 'ja_calculado', 'arquivo': str(anteriores[-1]),
                    'estado_anterior': resultado['status']}
    payloads = {}
    if resultado['status'] == 'nav_nocional_calculado':
        for chave in ('indice', 'patrimonio', 'operacoes_nocionais', 'posicoes'):
            payloads[chave] = resultado[chave].to_parquet(index=False)
        metadados['arquivos_sha256'] = {
            chave: hashlib.sha256(conteudo).hexdigest()
            for chave, conteudo in payloads.items()}
    metadados['calculado_em'] = str(pd.Timestamp.now(tz='America/Sao_Paulo'))
    bruto = json.dumps(metadados, sort_keys=True, ensure_ascii=False,
                       default=str, indent=2).encode('utf-8')
    digest = hashlib.sha256(bruto).hexdigest()[:16]
    nome = f'fundo-{instante:%Y%m%dT%H%M%S}-{digest}'
    caminho = pasta / f'{nome}.json'
    for chave, conteudo in payloads.items():
        destino = pasta / f'{nome}-{chave}.parquet'
        with tempfile.NamedTemporaryFile(dir=pasta, suffix='.tmp', delete=False) as arquivo:
            temporario = Path(arquivo.name)
            arquivo.write(conteudo)
        try:
            os.replace(temporario, destino)
        finally:
            temporario.unlink(missing_ok=True)
    # O manifesto e o commit: se um parquet falhar, nao ha JSON publicado.
    with caminho.open('xb') as arquivo:
        arquivo.write(bruto)
    return {'arquivo': str(caminho), **metadados}
