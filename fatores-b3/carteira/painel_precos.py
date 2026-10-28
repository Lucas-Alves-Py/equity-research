"""Preços e momentum do sinal atual a partir de COTAHIST e eventos B3.

O COTAHIST determina pregões, ticker, fechamento bruto e liquidez. Eventos da
B3 são aplicados apenas se tinham aprovação anterior à data do sinal. Falhas
de consulta ou cobertura preservam preço, mas deixam momentum ausente.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd

from carteira import cotahist
from fontes import b3


COLUNAS = ['data', 'ticker', 'preco_bruto', 'preco_fechamento_ajustado',
           'volume', 'momento_12_meses', 'status_eventos', 'erro_eventos',
           'eventos_caixa', 'eventos_acoes', 'fonte_preco', 'fonte_eventos']
EVENTOS_SNAPSHOTS = Path(__file__).resolve().parent / 'ledger' / 'eventos_b3'


def _cotacoes(as_of: pd.Timestamp) -> pd.DataFrame:
    inicio = as_of - pd.Timedelta(days=510)
    partes = []
    for ano in range(inicio.year, as_of.year + 1):
        caminho = cotahist.baixar(ano)
        partes.append(cotahist.parsear_periodo(caminho, inicio, as_of))
    if not partes:
        raise ValueError('sem anos COTAHIST para o sinal')
    return pd.concat(partes, ignore_index=True)


def _cotacoes_intervalo(inicio: pd.Timestamp, fim: pd.Timestamp) -> pd.DataFrame:
    partes = []
    for ano in range(inicio.year, fim.year + 1):
        caminho = cotahist.baixar(ano)
        partes.append(cotahist.parsear_periodo(caminho, inicio, fim))
    if not partes:
        raise ValueError('sem anos COTAHIST no intervalo')
    return pd.concat(partes, ignore_index=True)


def _guardar_eventos(prefixo: str, captura: tuple, as_of: pd.Timestamp) -> str:
    """Snapshot endereçado pelo conteúdo: o nome é o SHA-256 dos bytes gravados.

    Só dados entram no arquivo. Com hora de captura e data da rodada dentro,
    cada rodada diária gravava um arquivo novo por empresa (2.240 num dia de
    testes); o instante da decisão já fica no relatório que cita o hash.
    """
    del as_of  # mantido na assinatura; a data pertence ao relatório da decisão
    registro, caixa, acoes = captura
    documento = {
        'fonte': 'B3 CompanyCall', 'prefixo': prefixo,
        'empresa': {k: registro.get(k) for k in ('issuingCompany', 'tradingName', 'codeCVM')},
        'caixa': json.loads(caixa.to_json(orient='records', date_format='iso', date_unit='s')),
        'acoes': json.loads(acoes.to_json(orient='records', date_format='iso', date_unit='s')),
    }
    bruto = json.dumps(documento, ensure_ascii=False, sort_keys=True,
                       separators=(',', ':')).encode('utf-8')
    digest = hashlib.sha256(bruto).hexdigest()
    EVENTOS_SNAPSHOTS.mkdir(parents=True, exist_ok=True)
    destino = EVENTOS_SNAPSHOTS / f'{digest}.json'
    if not destino.exists():
        with tempfile.NamedTemporaryFile(dir=EVENTOS_SNAPSHOTS, suffix='.tmp',
                                         delete=False) as arquivo:
            temporario = Path(arquivo.name)
            arquivo.write(bruto)
        try:
            os.replace(temporario, destino)
        finally:
            temporario.unlink(missing_ok=True)
    return digest


def encadear_trocas(bruto: pd.DataFrame, cnpj_de, minimo: int = 253):
    """Emenda no ticker novo o histórico do código anterior da mesma empresa.

    Troca de código (ELET3 -> AXIA3, EMBR3 -> EMBJ3, GUAR3 -> RIAA3) zerava o
    histórico do momentum. Só emenda com mesmo CNPJ, mesma classe, pregões
    consecutivos e preço sem salto; fusão com CNPJ novo (PETZ3 -> AUAU3) ou
    troca com relação de conversão fica de fora. `cnpj_de(ticker)` devolve um
    conjunto de CNPJs.
    """
    sessoes = sorted(bruto['data'].unique())
    ultima = sessoes[-1]
    posicao = {d: i for i, d in enumerate(sessoes)}
    por = bruto.groupby('ticker')['data'].agg(['min', 'max', 'count'])
    novos = por[por['max'].eq(ultima) & por['count'].lt(minimo) & por['min'].gt(sessoes[0])]
    encerrados = por[por['max'].lt(ultima)]
    fechamento = bruto.set_index(['ticker', 'data'])['fechamento']
    trocas = []
    for novo, n in novos.iterrows():
        anterior = sessoes[posicao[n['min']] - 1]
        candidatos = encerrados[encerrados['max'].eq(anterior) &
                                (encerrados.index.str[4:] == novo[4:])]
        for velho in candidatos.index:
            salto = fechamento[(novo, n['min'])] / fechamento[(velho, anterior)]
            if not 0.8 <= salto <= 1.25 or not (cnpj_de(velho) & cnpj_de(novo)):
                continue
            trocas.append({'novo': novo, 'anterior': velho,
                           'ultimo_pregao_anterior': str(pd.Timestamp(anterior).date()),
                           'primeiro_pregao_novo': str(n['min'].date())})
            break
    if trocas:
        bruto = bruto.copy()
        bruto['ticker'] = bruto['ticker'].replace({t['anterior']: t['novo'] for t in trocas})
        # O código antigo pode vir depois do novo (ELET3 -> AXIA3); quem lê a
        # janela com tail() precisa das linhas em ordem de data.
        bruto = bruto.sort_values(['ticker', 'data']).reset_index(drop=True)
    return bruto, trocas


def _cnpj_por_ticker():
    """CNPJ pelo histórico do FCA; para ticker ausente nele, pelo cadastro B3."""
    cache, master = {}, []

    def busca(ticker):
        if ticker not in cache:
            if not master:
                from fontes import cvm
                ano = dt.date.today().year
                master.append(cvm.security_master(list(range(ano - 8, ano + 1))))
            achados = set(master[0].loc[master[0]['ticker'].eq(ticker), 'cnpj'])
            if not achados:
                c = str((b3.empresa(ticker) or {}).get('cnpj') or '').strip()
                c = c.zfill(14) if c.isdigit() else ''
                if len(c) == 14:
                    achados = {f'{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}'}
            cache[ticker] = achados
        return cache[ticker]
    return busca


def _validar_cotacoes(cotacoes: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    requeridas = {'data', 'ticker', 'fechamento', 'volume_negociado'}
    if faltando := requeridas - set(cotacoes.columns):
        raise ValueError(f'COTAHIST sem colunas: {sorted(faltando)}')
    df = cotacoes[list(requeridas)].copy()
    df['data'] = pd.to_datetime(df['data'], errors='coerce').dt.normalize()
    df['ticker'] = df['ticker'].astype(str).str.upper().str.strip()
    df = df[df['data'].le(as_of)]
    if df.empty or df['data'].isna().any():
        raise ValueError('COTAHIST vazio ou com datas inválidas')
    if df.duplicated(['data', 'ticker']).any():
        raise ValueError('COTAHIST com duplicata data/ticker')
    df['fechamento'] = pd.to_numeric(df['fechamento'], errors='coerce')
    df['volume_negociado'] = pd.to_numeric(df['volume_negociado'], errors='coerce')
    if (df['fechamento'].isna() | df['fechamento'].le(0) |
            df['volume_negociado'].isna() | df['volume_negociado'].lt(0)).any():
        raise ValueError('COTAHIST contém preço/volume inválido')
    return df.sort_values(['ticker', 'data']).reset_index(drop=True)


def _eventos_emissor(prefixo: str):
    registro = b3.empresa(prefixo + '3')
    if not registro:
        raise ValueError(f'emissor não localizado na B3: {prefixo}')
    if not registro.get('tradingName'):
        raise ValueError(f'emissor sem tradingName na B3: {prefixo}')
    caixa = b3.proventos(registro['tradingName'], pausa=0.05)
    acoes = b3.eventos_ativos_empresa(registro)
    return registro, caixa, acoes


def painel_precos(as_of: str | dt.date | pd.Timestamp,
                  tickers: list[str] | None = None,
                  cotacoes: pd.DataFrame | None = None,
                  liquidez_minima: float = 500_000,
                  buscar_eventos: bool = True,
                  max_workers: int = 4,
                  eventos_por_emissor: dict | None = None,
                  guardar_snapshots: bool = True,
                  cnpj_de=None) -> pd.DataFrame:
    """Um registro por ticker no último pregão COTAHIST até `as_of`.

    `volume` é a mediana das últimas 21 sessões negociadas do ticker.
    `momento_12_meses` replica o indicador legado: retorno total de 252
    observações, sem pular o mês recente. Eventos só são buscados para ativos
    líquidos e com 253 observações; consultas são compartilhadas por emissor.
    Injetar `cotacoes`/`eventos_por_emissor` permite reproduzir capturas.
    """
    corte = pd.Timestamp(as_of).normalize()
    bruto = _validar_cotacoes(_cotacoes(corte) if cotacoes is None else cotacoes, corte)
    bruto, trocas = encadear_trocas(bruto, cnpj_de or _cnpj_por_ticker())
    data = bruto['data'].max()
    atual = bruto[bruto['data'].eq(data)].copy()
    if tickers is not None:
        atual = atual[atual['ticker'].isin({str(t).upper() for t in tickers})]
    if atual.empty:
        return pd.DataFrame(columns=COLUNAS)

    grupos = {ticker: bloco.tail(253) for ticker, bloco in bruto.groupby('ticker')
              if ticker in set(atual['ticker'])}
    saida = []
    for linha in atual.itertuples(index=False):
        hist = grupos[linha.ticker]
        mediana = (float(hist['volume_negociado'].tail(21).median())
                   if len(hist) >= 21 else float('nan'))
        status = ('baixa_liquidez' if not (mediana > liquidez_minima) else
                  'historico_insuficiente' if len(hist) < 253 else
                  'nao_consultado')
        saida.append({'data': data.date(), 'ticker': linha.ticker,
                      'preco_bruto': float(linha.fechamento),
                      'preco_fechamento_ajustado': float('nan'),
                      'volume': mediana, 'momento_12_meses': float('nan'),
                      'status_eventos': status, 'erro_eventos': '',
                      'eventos_caixa': 0, 'eventos_acoes': 0,
                      'fonte_preco': 'B3 COTAHIST', 'fonte_eventos': 'B3 CompanyCall'})
    painel = pd.DataFrame(saida)
    elegiveis = painel[painel['status_eventos'].eq('nao_consultado')]['ticker'].tolist()
    if not buscar_eventos or not elegiveis:
        resultado = painel[COLUNAS].sort_values('ticker').reset_index(drop=True)
        resultado.attrs['eventos_sha256_por_emissor'] = {}
        resultado.attrs['trocas_ticker'] = trocas
        return resultado

    prefixos = sorted({ticker[:4] for ticker in elegiveis})
    capturas = dict(eventos_por_emissor or {})
    faltantes = [p for p in prefixos if p not in capturas]
    if faltantes:
        with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
            trabalhos = {pool.submit(_eventos_emissor, prefixo): prefixo
                        for prefixo in faltantes}
            for trabalho in as_completed(trabalhos):
                prefixo = trabalhos[trabalho]
                try:
                    capturas[prefixo] = trabalho.result()
                except Exception as exc:
                    capturas[prefixo] = exc

    hashes = {}
    if guardar_snapshots:
        for prefixo in prefixos:
            if not isinstance(capturas[prefixo], Exception):
                hashes[prefixo] = _guardar_eventos(prefixo, capturas[prefixo], corte)

    for ticker in elegiveis:
        indice = painel.index[painel['ticker'].eq(ticker)][0]
        capturado = capturas[ticker[:4]]
        if isinstance(capturado, Exception):
            painel.at[indice, 'status_eventos'] = 'falha_consulta'
            painel.at[indice, 'erro_eventos'] = str(capturado)[:200]
            continue
        registro, caixa_emissor, acoes_emissor = capturado
        hist = grupos[ticker].set_index('data')['fechamento']
        inicio = hist.index.min()
        try:
            caixa = caixa_emissor[caixa_emissor['ultimo_com'].between(inicio, data)].copy()
            classe = b3.CLASSE.get(ticker[4:])
            if classe is None and not caixa.empty:
                raise ValueError(f'classe de provento em dinheiro não mapeada: {ticker}')
            if classe is not None:
                caixa = caixa[caixa['classe'].eq(classe)]
            acoes = b3.proventos_ativos(ticker, empresa_b3=registro,
                                       eventos_empresa=acoes_emissor)
            acoes = acoes[acoes['ultimo_com'].between(inicio, data)].copy()
            if (caixa['aprovado_em'].isna().any() or acoes['aprovado_em'].isna().any()):
                raise ValueError('evento na janela sem data de aprovação')
            caixa = caixa[caixa['aprovado_em'].lt(data)]
            acoes = acoes[acoes['aprovado_em'].lt(data)]
            if (caixa['pct'].isna() | ~caixa['pct'].between(0, 100, inclusive='neither')).any():
                raise ValueError('provento em dinheiro com percentual inválido')
            partes_fatores = [parte for parte in (
                b3.fatores(caixa, ticker, as_of=data),
                b3.fatores_ativos(acoes, as_of=data)) if not parte.empty]
            fatores = (pd.concat(partes_fatores, ignore_index=True) if partes_fatores else
                       pd.DataFrame(columns=['ultimo_com', 'fator']))
            if not fatores.empty:
                fatores = fatores.groupby('ultimo_com', as_index=False)['fator'].prod()
            ajustado = b3.ajustar(hist, fatores)
            painel.at[indice, 'preco_fechamento_ajustado'] = float(ajustado.iloc[-1])
            painel.at[indice, 'momento_12_meses'] = float(ajustado.iloc[-1] / ajustado.iloc[0] - 1)
            painel.at[indice, 'eventos_caixa'] = len(caixa)
            painel.at[indice, 'eventos_acoes'] = len(acoes)
            painel.at[indice, 'status_eventos'] = 'ok'
        except Exception as exc:
            painel.at[indice, 'status_eventos'] = 'falha_ajuste'
            painel.at[indice, 'erro_eventos'] = str(exc)[:200]
    resultado = painel[COLUNAS].sort_values('ticker').reset_index(drop=True)
    resultado.attrs['eventos_sha256_por_emissor'] = hashes
    resultado.attrs['trocas_ticker'] = trocas
    resultado.attrs['as_of'] = corte.date().isoformat()
    return resultado


def series_ajustadas(tickers: list[str], inicio: str | dt.date | pd.Timestamp,
                     fim: str | dt.date | pd.Timestamp,
                     as_of: str | dt.date | pd.Timestamp,
                     cotacoes: pd.DataFrame | None = None,
                     max_workers: int = 4,
                     eventos_por_emissor: dict | None = None,
                     guardar_snapshots: bool = True) -> pd.DataFrame:
    """Séries diárias completas para execução fictícia e marcação da carteira.

    `inicio`/`fim` devem ser pregões no COTAHIST; cada ticker precisa ter preço
    em todas as sessões oficiais do intervalo. `as_of` é a data em que a
    captura de eventos está disponível. Eventos sem aprovação anterior a ela
    ou com tipo/fator inválido bloqueiam a série. O retorno ajustado aplica a
    convenção JCP 85% de `fontes.b3.fatores` e inclui bonificações/splits.
    Hashes dos snapshots normalizados ficam em `resultado.attrs`.
    """
    nomes = list(dict.fromkeys(str(t).upper().strip() for t in tickers if str(t).strip()))
    if not nomes:
        raise ValueError('informe tickers para séries ajustadas')
    de, ate, corte = (pd.Timestamp(valor).normalize() for valor in (inicio, fim, as_of))
    if not (de <= ate <= corte):
        raise ValueError('exige inicio <= fim <= as_of')
    bruto = _validar_cotacoes(
        _cotacoes_intervalo(de, ate) if cotacoes is None else cotacoes, ate)
    bruto = bruto[bruto['data'].between(de, ate)].copy()
    sessoes = pd.DatetimeIndex(sorted(bruto['data'].unique()))
    if sessoes.empty or sessoes[0] != de or sessoes[-1] != ate:
        raise ValueError('início/fim ausentes do calendário COTAHIST')
    bruto = bruto[bruto['ticker'].isin(nomes)]
    if set(bruto['ticker']) != set(nomes):
        raise ValueError(f'ativos sem preços COTAHIST: {sorted(set(nomes) - set(bruto["ticker"]))}')
    por_ticker = {}
    for ticker in nomes:
        serie = bruto[bruto['ticker'].eq(ticker)].set_index('data')['fechamento']
        faltantes = sessoes.difference(serie.index)
        if not faltantes.empty:
            raise ValueError(f'pregões sem preço para {ticker}: '
                             f'{[d.date().isoformat() for d in faltantes[:5]]}')
        por_ticker[ticker] = serie

    prefixos = sorted({nome[:4] for nome in nomes})
    capturas = dict(eventos_por_emissor or {})
    faltantes = [p for p in prefixos if p not in capturas]
    if faltantes:
        with ThreadPoolExecutor(max_workers=max(1, max_workers)) as pool:
            trabalhos = {pool.submit(_eventos_emissor, prefixo): prefixo
                        for prefixo in faltantes}
            for trabalho in as_completed(trabalhos):
                prefixo = trabalhos[trabalho]
                try:
                    capturas[prefixo] = trabalho.result()
                except Exception as exc:
                    raise RuntimeError(f'falha ao consultar eventos de {prefixo}: {exc}') from exc

    hashes = {}
    if guardar_snapshots:
        for prefixo in prefixos:
            hashes[prefixo] = _guardar_eventos(prefixo, capturas[prefixo], corte)

    partes = []
    for ticker in nomes:
        registro, caixa_emissor, acoes_emissor = capturas[ticker[:4]]
        caixa = caixa_emissor[caixa_emissor['ultimo_com'].between(de, ate, inclusive='left')].copy()
        classe = b3.CLASSE.get(ticker[4:])
        if classe is None and not caixa.empty:
            raise ValueError(f'classe de provento em dinheiro não mapeada: {ticker}')
        if classe is not None:
            caixa = caixa[caixa['classe'].eq(classe)]
        acoes = b3.proventos_ativos(ticker, empresa_b3=registro,
                                   eventos_empresa=acoes_emissor)
        acoes = acoes[acoes['ultimo_com'].between(de, ate, inclusive='left')].copy()
        if caixa['aprovado_em'].isna().any() or acoes['aprovado_em'].isna().any():
            raise ValueError(f'evento sem data de aprovação para {ticker}')
        # Se um evento afetou preços observados antes de `as_of` mas aparece
        # aprovado depois, não há trilha temporal suficiente para reproduzi-lo.
        if caixa['aprovado_em'].ge(corte).any() or acoes['aprovado_em'].ge(corte).any():
            raise ValueError(f'evento observado sem aprovação anterior a as_of: {ticker}')
        fatores = [parte for parte in (
            b3.fatores(caixa, ticker, as_of=corte),
            b3.fatores_ativos(acoes, as_of=corte)) if not parte.empty]
        fatores = (pd.concat(fatores).groupby('ultimo_com', as_index=False)['fator'].prod()
                   if fatores else pd.DataFrame(columns=['ultimo_com', 'fator']))
        serie = por_ticker[ticker]
        ajustado = b3.ajustar(serie, fatores)
        partes.append(pd.DataFrame({'data': serie.index.date, 'ticker': ticker,
                                    'preco_bruto': serie.to_numpy(),
                                    'preco_fechamento_ajustado': ajustado.to_numpy()}))
    saida = pd.concat(partes, ignore_index=True).sort_values(['data', 'ticker']).reset_index(drop=True)
    saida.attrs['fonte_preco'] = 'B3 COTAHIST'
    saida.attrs['fonte_eventos'] = 'B3 CompanyCall'
    saida.attrs['eventos_sha256_por_emissor'] = hashes
    saida.attrs['as_of'] = corte.date().isoformat()
    return saida
