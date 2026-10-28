"""Histórico do pipeline B3/CVM nas datas de rebalanceamento, no formato da Fintz.

Roda o mesmo código da carteira ao vivo (`gerar_selecao_atual`) em cada data
da grade, com a quantidade de ações da CVM no lugar do capital atual da B3.
A CVM só publica a composição do capital no ITR desde o 1T20 e o código de
negociação no FCA desde 2018. Antes de jun/2020 a quantidade de ações vem do
capital social do FRE (`fundamentos.quantidade_acoes_fre`) e os tickers que o
FCA não traz vêm das cotações do FRE (`fontes.tickers_fre`). Cada data fica
gravada em `paineis/` e uma rodada interrompida retoma de onde parou. No fim, monta em `dados/historico_pipeline/` os arquivos que
`calculadora_factor` lê; como os indicadores só existem nas datas da grade, o
backtest roda com `balanceamento=1`.

Uso (na pasta `fatores-b3`):
    python -m coleta.construir_historico --inicio 2020-06-01 --fim 2026-10-07
"""

from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pandas as pd

from carteira import painel_precos as pp
from carteira import pipeline_atual as pa
from fontes import b3

RAIZ = Path(__file__).resolve().parents[1]
DESTINO = RAIZ / 'dados' / 'historico_pipeline'
INDICADORES = {'EV_EBIT': 'EV_EBIT', 'ROIC': 'ROIC',
               'momento_12_meses': 'momento_12_meses', 'volume_mediano': 'volume'}


def grade(inicio, fim, passo: int = 21, dia5: bool = False) -> list[pd.Timestamp]:
    """A cada `passo` pregões, ou no primeiro pregão a partir do dia 5 de cada
    mês (`dia5`), que é a regra da carteira ao vivo."""
    sessoes = pd.DatetimeIndex(sorted(pp._cotacoes_intervalo(pd.Timestamp(inicio), pd.Timestamp(fim))['data'].unique()))
    if dia5:
        validas = sessoes[sessoes.day >= 5]
        return [pd.Timestamp(d) for d in pd.Series(validas).groupby(validas.to_period('M')).min()]
    return [pd.Timestamp(d) for d in sessoes[::passo]]


def _limpar(cotacoes: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    # O COTAHIST tem prints com preço zero (BGIP4 em 18/05/2021). O validador
    # ao vivo aborta; no histórico a linha sai e fica registrada.
    ruins = cotacoes['fechamento'].le(0) | cotacoes['volume_negociado'].lt(0)
    return cotacoes[~ruins], cotacoes[ruins]


def _codigos_atuais() -> dict:
    """Prefixo antigo -> prefixos vigentes hoje da mesma empresa (mesmo CNPJ).

    A B3 só acha o emissor pelo código atual: ELET virou AXIA, GUAR virou
    RIAA, BRDT virou VBBR. O histórico de eventos segue sob o código novo.
    Códigos que saíram antes de 2018 (TBLE, DROG, TLPP) chegam ao CNPJ pelo FRE.
    """
    m = _master_historico()
    m = m.assign(prefixo=m['ticker'].str[:4])
    vigentes = (m[m['ano_fca'].notna() & m['fim'].isna()]
                .groupby('cnpj')['prefixo'].agg(lambda s: sorted(set(s))))
    por_prefixo = m.groupby('prefixo')['cnpj'].agg(lambda s: sorted(set(s)))
    return {p: sorted({n for c in cnpjs for n in vigentes.get(c, []) if n != p})
            for p, cnpjs in por_prefixo.items() if len(cnpjs) == 1}


_NOMES: dict = {}
SEM_EVENTOS_EM_ACOES = set()  # deslistadas: só proventos em dinheiro


def _nome_pregao(prefixo: str) -> str | None:
    """Nome de pregão (NOMRES, colunas 28-39 do COTAHIST) mais recente do emissor."""
    if not _NOMES:
        import zipfile
        from carteira import cotahist
        for ano in range(2010, pd.Timestamp.today().year + 1):
            with zipfile.ZipFile(cotahist.baixar(ano)) as z, z.open(z.namelist()[0]) as f:
                for linha in f:
                    if linha[:2] == b'01' and linha[24:27] == b'010':
                        _NOMES[linha[12:16].decode('latin-1')] = linha[27:39].decode('latin-1').strip()
    return _NOMES.get(prefixo)


def _eventos_deslistada(prefixo: str):
    # A busca de empresas da B3 não acha quem saiu da bolsa, mas os proventos
    # continuam consultáveis pelo nome de pregão. Eventos em ações não vêm;
    # o painel bloqueia o momentum se houver salto de preço sem explicação.
    nome = _nome_pregao(prefixo)
    if not nome:
        raise ValueError(f'sem nome de pregão no COTAHIST: {prefixo}')
    caixa = b3.proventos(nome, pausa=0.05)
    if caixa.empty:
        raise ValueError(f'B3 sem proventos para {nome}')
    SEM_EVENTOS_EM_ACOES.add(prefixo)
    return ({'issuingCompany': prefixo, 'tradingName': nome}, caixa,
            pd.DataFrame(columns=['ultimo_com', 'aprovado_em', 'isin', 'tipo', 'fator_b3']))


def _capturar_eventos(prefixos, eventos: dict) -> None:
    faltam = sorted(set(prefixos) - set(eventos))
    atuais = _codigos_atuais() if faltam else {}
    def um(prefixo):
        try:
            return prefixo, pp._eventos_emissor(prefixo)
        except Exception as exc:
            for atual in atuais.get(prefixo, []):
                try:
                    return prefixo, pp._eventos_emissor(atual)
                except Exception:
                    continue
            try:
                return prefixo, _eventos_deslistada(prefixo)
            except Exception:
                return prefixo, exc
    with ThreadPoolExecutor(4) as pool:
        capturados = dict(pool.map(um, faltam))
    eventos.update({p: _completar(p, c) for p, c in capturados.items()})


_INFERIDOS: list = []
CLASSE_ISIN = {'3': 'NOR', '4': 'NPR', '5': 'NPA', '6': 'NPB', '11': 'CDA'}


_MASTER: list = []
PRIMEIRO_ANO_ITR = 2011


def _master_historico() -> pd.DataFrame:
    """FCA de 2018 em diante mais os tickers identificados pelo FRE (cache em dados/)."""
    if not _MASTER:
        from fontes import tickers_fre
        cache = RAIZ / 'dados' / 'tickers_fre.parquet'
        if not cache.exists():
            tickers_fre.mapear(range(2011, 2020), range(2010, 2019)).to_parquet(cache, index=False)
        hoje = pd.Timestamp.today().year
        _MASTER.append(tickers_fre.master_historico(list(range(2018, hoje + 1)), pd.read_parquet(cache)))
    return _MASTER[0]


def _inferidos() -> pd.DataFrame:
    """Eventos em ações reconstruídos da CVM (ver `fontes.eventos_cvm`), uma vez por rodada."""
    if not _INFERIDOS:
        from fontes import eventos_cvm
        hoje = pd.Timestamp.today().year
        mapa = _master_historico().groupby('cnpj')['ticker'].agg(lambda s: sorted(set(s))).to_dict()
        eventos, _ = eventos_cvm.eventos_inferidos(mapa, range(2011, 2023), range(2020, hoje + 1),
                                                   range(2010, hoje + 1))
        _INFERIDOS.append(eventos)
    return _INFERIDOS[0]


def _completar(prefixo: str, capturado):
    """Soma à lista da B3 os eventos inferidos que ela não tem (KEPL3, 05/2022)."""
    if isinstance(capturado, Exception):
        return capturado
    registro, caixa, acoes = capturado
    inferidos = _inferidos()
    novos = []
    for e in inferidos[inferidos['ticker'].str[:4].eq(prefixo)].itertuples(index=False):
        classe = CLASSE_ISIN.get(e.ticker[4:])
        if classe is None:
            continue
        da_b3 = acoes[acoes['isin'].str.contains(classe, regex=False)]
        if (pd.to_datetime(da_b3['ultimo_com']) - pd.Timestamp(e.ultimo_com)).abs().le(pd.Timedelta(days=7)).any():
            continue
        novos.append({'ultimo_com': e.ultimo_com, 'aprovado_em': e.aprovado_em,
                      'isin': f'CVM{classe}', 'tipo': e.tipo, 'fator_b3': e.fator_b3})
    if novos:
        acoes = pd.concat([acoes, pd.DataFrame(novos)], ignore_index=True).sort_values('ultimo_com')
    return registro, caixa, acoes


def _bloquear_saltos_sem_evento(precos: pd.DataFrame, cotacoes: pd.DataFrame) -> pd.DataFrame:
    """Sem eventos em ações, um salto de 1,8x na janela anula o momentum.

    Salto no pregão ex de um evento inferido da CVM já está explicado.
    """
    from carteira import eventos as detector
    alvo = precos['ticker'].str[:4].isin(SEM_EVENTOS_EM_ACOES) & precos['momento_12_meses'].notna()
    if not alvo.any():
        return precos
    janela = (cotacoes[cotacoes['ticker'].isin(precos.loc[alvo, 'ticker'])]
              .sort_values('data').groupby('ticker').tail(253))
    saltos = detector.detectar(janela)
    inferidos = _inferidos()
    explicados = set(zip(inferidos['ticker'], inferidos['ex']))
    suspeitos = {t for t, d in zip(saltos['ticker'], saltos['data']) if (t, d) not in explicados}
    bloquear = alvo & precos['ticker'].isin(suspeitos)
    precos = precos.copy()
    precos.loc[bloquear, 'momento_12_meses'] = float('nan')
    precos.loc[bloquear, 'status_eventos'] = 'salto_sem_evento_em_acoes'
    return precos


def _precos_da_data(data: pd.Timestamp, eventos: dict):
    cotacoes, ruins = _limpar(pp._cotacoes(data))
    precos = pp.painel_precos(data, cotacoes=cotacoes, buscar_eventos=False)
    _capturar_eventos(precos.loc[precos['status_eventos'].eq('nao_consultado'), 'ticker'].str[:4], eventos)
    precos = pp.painel_precos(data, cotacoes=cotacoes, eventos_por_emissor=eventos,
                              guardar_snapshots=False)
    return _bloquear_saltos_sem_evento(precos, cotacoes), ruins


COLUNAS_DE_PRECO = ['preco_fechamento_ajustado', 'volume', 'momento_12_meses', 'status_eventos',
                    'erro_eventos', 'eventos_caixa', 'eventos_acoes']


def recalcular_precos(painel: pd.DataFrame, data: pd.Timestamp, eventos: dict) -> pd.DataFrame:
    """Refaz só as colunas de preço de um painel gravado; fundamentos ficam."""
    precos, _ = _precos_da_data(data, eventos)
    novas = precos.set_index('ticker')[COLUNAS_DE_PRECO]
    painel = painel.set_index('ticker')
    painel.update(novas)
    sem_preco = painel.index.difference(novas.index)
    painel.loc[sem_preco, 'momento_12_meses'] = float('nan')
    return painel.reset_index()


def painel_da_data(data: pd.Timestamp, eventos: dict):
    precos, ruins = _precos_da_data(data, eventos)
    instante = data.tz_localize('America/Sao_Paulo') + pd.Timedelta(hours=23)
    # Os dados abertos de ITR começam em 2011 (DFP em 2010); anos sem arquivo davam 404.
    anos = [a for a in (data.year - 2, data.year - 1, data.year) if a >= PRIMEIRO_ANO_ITR]
    extra = ({'master_provider': lambda _: _master_historico()}
             if data < pa.INICIO_COMPOSICAO_ITR else {})
    painel, alvo = pa.gerar_selecao_atual(instante, anos, capital_provider=pa.capitalizacao_cvm,
                                          preco_provider=lambda _: precos, referencia=data.date(), **extra)
    return painel, alvo, len(ruins)


def precos_ajustados(tickers, inicio, fim, eventos: dict) -> tuple[pd.DataFrame, list]:
    """Fechamento ajustado diário por proventos e eventos em ações já conhecidos."""
    bruto, _ = _limpar(pp._cotacoes_intervalo(pd.Timestamp(inicio), pd.Timestamp(fim)))
    # Na janela inteira, qualquer código que surgiu depois do início é candidato
    # a emenda (o padrão de 253 pregões vale para a janela do momentum).
    bruto, _ = pp.encadear_trocas(pp._validar_cotacoes(bruto, pd.Timestamp(fim)),
                                  pp._cnpj_por_ticker(), minimo=10**9)
    _capturar_eventos({t[:4] for t in tickers}, eventos)
    partes, sem_ajuste = [], []
    for ticker in sorted(tickers):
        serie = bruto[bruto['ticker'].eq(ticker)].set_index('data')['fechamento']
        if serie.empty:
            continue
        capturado = eventos.get(ticker[:4])
        try:
            if isinstance(capturado, Exception) or capturado is None:
                raise ValueError(str(capturado))
            registro, caixa, acoes = capturado
            classe = b3.CLASSE.get(ticker[4:])
            caixa = caixa[caixa['classe'].eq(classe)] if classe else caixa.iloc[:0]
            acoes = b3.proventos_ativos(ticker, empresa_b3=registro, eventos_empresa=acoes)
            # Como no painel: só eventos do período da série. Registros antigos
            # da B3 (anos 2000) trazem percentuais inválidos que travam o ajuste.
            periodo = (serie.index.min(), serie.index.max())
            caixa = caixa[caixa['ultimo_com'].between(*periodo)]
            acoes = acoes[acoes['ultimo_com'].between(*periodo)]
            # Incorporação, cisão ou restituição em ações (BRAP4, dez/2021) não
            # têm fator de preço. Neutraliza o pregão ex: a variação daquele dia
            # vira a própria distribuição, e o resto da série segue ajustado.
            suportado = acoes['tipo'].str.contains('DESDOBRAMENTO|BONIFICACAO|GRUPAMENTO')
            neutros = []
            for com in acoes.loc[~suportado, 'ultimo_com'].drop_duplicates():
                depois = serie[serie.index > com]
                if not depois.empty and com in serie.index:
                    neutros.append({'ultimo_com': com, 'fator': depois.iloc[0] / serie[com]})
            if neutros:
                sem_ajuste.append({'ticker': ticker, 'erro': 'pregao_ex_neutralizado: ' +
                                   ', '.join(acoes.loc[~suportado, 'tipo'].unique())})
            fatores = [f for f in (b3.fatores(caixa, ticker), b3.fatores_ativos(acoes[suportado]),
                                   pd.DataFrame(neutros)) if not f.empty]
            fatores = (pd.concat(fatores).groupby('ultimo_com', as_index=False)['fator'].prod()
                       if fatores else pd.DataFrame(columns=['ultimo_com', 'fator']))
            ajustado = b3.ajustar(serie, fatores)
        except Exception as exc:
            sem_ajuste.append({'ticker': ticker, 'erro': str(exc)[:120]})
            ajustado = serie
        partes.append(pd.DataFrame({'data': ajustado.index, 'ticker': ticker,
                                    'preco_fechamento_ajustado': ajustado.values}))
    return pd.concat(partes, ignore_index=True), sem_ajuste


def construir(inicio, fim, so_precos: bool = False, dia5: bool = False) -> dict:
    """`so_precos` refaz preço e momentum dos painéis já gravados, sem a CVM."""
    pasta = DESTINO / 'paineis'
    pasta.mkdir(parents=True, exist_ok=True)
    eventos, rodadas = {}, []
    for data in grade(inicio, fim, dia5=dia5):
        arquivo = pasta / f'{data.date()}.parquet'
        if arquivo.exists():
            if so_precos:
                recalcular_precos(pd.read_parquet(arquivo), data, eventos).to_parquet(arquivo, index=False)
                print(data.date(), 'preços recalculados', flush=True)
            rodadas.append({'data': str(data.date()),
                            'status': 'precos_recalculados' if so_precos else 'reaproveitado'})
            continue
        try:
            painel, alvo, ruins = painel_da_data(data, eventos)
        except Exception as exc:
            rodadas.append({'data': str(data.date()), 'status': 'erro', 'erro': str(exc)[:200]})
            print(data.date(), 'erro:', str(exc)[:120], flush=True)
            continue
        painel.assign(data=data).to_parquet(arquivo, index=False)
        alvo.to_csv(pasta / f'{data.date()}-carteira.csv', index=False)
        rodadas.append({'data': str(data.date()), 'status': 'ok', 'acoes': len(painel),
                        'linhas_cotahist_descartadas': ruins})
        print(data.date(), len(painel), 'ações;', alvo['ticker'].tolist(), flush=True)

    paineis = pd.concat([pd.read_parquet(p) for p in sorted(pasta.glob('*.parquet'))],
                        ignore_index=True)
    for nome, coluna in INDICADORES.items():
        (paineis[['data', 'ticker', coluna]].rename(columns={coluna: 'valor'})
         .dropna().to_parquet(DESTINO / f'{nome}.parquet', index=False))
    cotacoes, sem_ajuste = precos_ajustados(set(paineis['ticker']), inicio, fim, eventos)
    cotacoes.to_parquet(DESTINO / 'cotacoes.parquet', index=False)
    manifesto = {'inicio': str(inicio), 'fim': str(fim), 'rodadas': rodadas,
                 'precos_sem_ajuste': sem_ajuste,
                 'eventos_sem_captura': sorted(p for p, v in eventos.items()
                                               if isinstance(v, Exception))}
    (DESTINO / 'manifesto.json').write_text(json.dumps(manifesto, ensure_ascii=False, indent=2),
                                            encoding='utf-8')
    return manifesto


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inicio', default='2020-06-01')
    parser.add_argument('--fim', default=str(pd.Timestamp.today().date()))
    parser.add_argument('--so-precos', action='store_true',
                        help='refaz preço e momentum dos painéis já gravados')
    parser.add_argument('--dia5', action='store_true',
                        help='grade da carteira ao vivo, em dados/historico_pipeline_dia5/')
    args = parser.parse_args()
    if args.dia5:
        global DESTINO
        DESTINO = RAIZ / 'dados' / 'historico_pipeline_dia5'
    m = construir(args.inicio, args.fim, so_precos=args.so_precos, dia5=args.dia5)
    erros = [r for r in m['rodadas'] if r['status'] == 'erro']
    print(f"{len(m['rodadas'])} datas, {len(erros)} com erro; "
          f"{len(m['precos_sem_ajuste'])} tickers sem ajuste; "
          f"{len(m['eventos_sem_captura'])} emissores sem eventos")


if __name__ == '__main__':
    main()
