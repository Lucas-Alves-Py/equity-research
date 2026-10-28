"""Desdobramentos, grupamentos e bonificações que faltam na lista da B3.

A lista de eventos em ações da B3 (`GetListedSupplementCompany`) é
incompleta: não tem o desdobramento da KEPL3 de 05/2022 nem a maior parte das
bonificações da ITSA e da CMIG. Este módulo reconstrói os eventos com duas
fontes públicas e confirma cada um no preço:

1. Candidatos. Até 2022, as tabelas de desdobramento do Formulário de
   Referência (FRE) dão tipo, data de aprovação e quantidades antes/depois. De
   2020 em diante, a quantidade de ações do ITR/DFP muda de um trimestre para
   o outro na proporção do evento. A CVM parou de publicar as tabelas do FRE
   em 2023, e a composição do capital no ITR só existe desde 2020.
2. Data. O COTAHIST tem um número de distribuição (DISMES, colunas 243-245)
   que a B3 incrementa a cada evento. O pregão ex é o dia em que ele muda e o
   preço salta na razão inversa da quantidade de ações.

Emissão de ações também muda a quantidade, mas não faz o preço saltar: sem
salto compatível, o candidato é descartado e fica registrado.
"""

from __future__ import annotations

import math
import zipfile
from functools import lru_cache

import pandas as pd

from fontes import cvm

VARIACAO_MINIMA = 0.015    # abaixo disso a mudança de quantidade é ruído
# Folga em log entre o salto de preço e a razão. Cresce com o evento: o
# pregão ex também carrega dividendo e mercado (CMIG4 05/2022: 0,734 contra
# 0,769; RCSL4 05/2024: 3,83 contra 4).
TOLERANCIA_BASE, TOLERANCIA_PROPORCIONAL = 0.04, 0.08
# A quantidade nova pode sair no ITR antes do pregão ex (NGRD3: ITR de 30/09,
# ex em 08/10/2024).
FOLGA_APOS_TRIMESTRE = pd.Timedelta(days=75)


@lru_cache(maxsize=None)
def precos_com_distribuicao(anos: tuple[int, ...]) -> pd.DataFrame:
    """Fechamento bruto e número de distribuição do mercado à vista, lote padrão."""
    from carteira import cotahist
    linhas = []
    for ano in anos:
        with zipfile.ZipFile(cotahist.baixar(ano)) as z, z.open(z.namelist()[0]) as f:
            for b in f:
                if b[:2] != b'01' or b[10:12].decode() not in cotahist.CODBDI_ACOES or b[24:27] != b'010':
                    continue
                fator = int(b[210:217]) or 1
                linhas.append((b[2:10].decode(), b[12:24].decode('latin-1').strip(),
                               int(b[108:121]) / (100 * fator), int(b[242:245])))
    d = pd.DataFrame(linhas, columns=['data', 'ticker', 'fechamento', 'dismes'])
    d['data'] = pd.to_datetime(d['data'], format='%Y%m%d')
    return d.sort_values(['ticker', 'data']).reset_index(drop=True)



def candidatos_fre(anos) -> pd.DataFrame:
    partes = []
    for ano in anos:
        try:
            d = cvm.tabela('FRE', ano, 'capital_social_desdobramento')
        except KeyError:
            continue  # sem a tabela desde o FRE de 2023
        partes.append(d)
    d = pd.concat(partes, ignore_index=True)
    d = d.drop_duplicates(['CNPJ_Companhia', 'Data_Aprovacao', 'Tipo_Evento'])
    antes = d['Quantidade_Total_Acoes_Antes_Aprovacao']
    # FRE antigos trazem eventos com quantidade depois zerada (razão 0).
    d = d[antes.gt(0) & d['Quantidade_Total_Acoes_Depois_Aprovacao'].gt(0)]
    inicio = pd.to_datetime(d['Data_Aprovacao'], errors='coerce')
    return pd.DataFrame({'cnpj': d['CNPJ_Companhia'], 'inicio': inicio - pd.Timedelta(days=5),
                         'fim': inicio + pd.Timedelta(days=120),
                         'razao': d['Quantidade_Total_Acoes_Depois_Aprovacao'] / d['Quantidade_Total_Acoes_Antes_Aprovacao'],
                         'fonte': 'FRE ' + d['Tipo_Evento'].astype(str)}).dropna()


def candidatos_itr(anos) -> pd.DataFrame:
    partes = []
    for doc in ('ITR', 'DFP'):
        for ano in anos:
            try:
                partes.append(cvm.tabela(doc, ano, 'composicao_capital'))
            except KeyError:
                continue
    d = pd.concat(partes, ignore_index=True)
    d['dt_refer'] = pd.to_datetime(d['DT_REFER'])
    d['total'] = d['QT_ACAO_ORDIN_CAP_INTEGR'] + d['QT_ACAO_PREF_CAP_INTEGR']
    d = (d[d['total'].gt(0)].sort_values(['CNPJ_CIA', 'dt_refer', 'VERSAO'])
         .drop_duplicates(['CNPJ_CIA', 'dt_refer'], keep='last'))
    d['anterior'] = d.groupby('CNPJ_CIA')['total'].shift()
    d['inicio'] = d.groupby('CNPJ_CIA')['dt_refer'].shift()
    d = d.dropna(subset=['anterior'])
    # Trimestres em unidades diferentes (ações x milhares) multiplicam a razão
    # por 1.000 (SHOW: 99,999 era um grupamento de 10:1; AMER3 em 2024 foi de
    # 100:1). Entram as três versões; o salto de preço decide qual é real.
    bruta = d['total'] / d['anterior']
    d = pd.concat([d.assign(razao=bruta * 1000.0 ** k) for k in (-1, 0, 1)], ignore_index=True)
    razao = d['razao']
    # Recompra cancelada e emissão também mudam a quantidade. Grupamento real
    # é grande (2:1 ou mais); bonificação é percentual redondo (KLBN 10%, GGBR
    # 20%, CMIG 30%), emissão não (BEEF3 10,4%).
    redondo = ((razao - 1) * 200).sub(((razao - 1) * 200).round()).abs().le(0.1)
    evento = razao.le(0.67) | razao.ge(1.9) | (razao.gt(1 + VARIACAO_MINIMA) & redondo)
    d = d[evento]
    return pd.DataFrame({'cnpj': d['CNPJ_CIA'], 'inicio': d['inicio'],
                         'fim': d['dt_refer'] + FOLGA_APOS_TRIMESTRE,
                         'razao': d['razao'], 'fonte': 'ITR quantidade de ações'})


def localizar(precos: pd.DataFrame, candidato) -> dict | None:
    """Pregão ex dentro da janela: muda o DISMES e o preço salta ~1/razão."""
    janela = precos[precos['data'].gt(candidato.inicio) & precos['data'].le(candidato.fim)]
    anterior = precos[precos['data'].le(candidato.inicio)].tail(1)
    p = pd.concat([anterior, janela])
    p = p[p['fechamento'].gt(0)]  # o COTAHIST tem prints a zero (BGIP4, 05/2021)
    if len(p) < 2:
        return None
    salto = p['fechamento'] / p['fechamento'].shift()
    mudou = p['dismes'].ne(p['dismes'].shift())
    erro = (salto.map(math.log) - math.log(1 / candidato.razao)).abs()
    # O salto precisa ter ao menos metade do tamanho esperado: uma bonificação
    # de 3% não se confirma num dia de -0,7% (GFSA3, 07/2021).
    forte = salto.map(math.log).abs().ge(0.5 * abs(math.log(candidato.razao)))
    erro = erro[mudou & salto.notna() & forte]
    tolerancia = TOLERANCIA_BASE + TOLERANCIA_PROPORCIONAL * abs(math.log(candidato.razao))
    if erro.empty or erro.min() > tolerancia:
        return None
    i = p.index.get_loc(erro.idxmin())
    return {'ultimo_com': p['data'].iloc[i - 1], 'ex': p['data'].iloc[i],
            'salto_preco': float(salto.iloc[i]), 'erro_log': float(erro.min())}


def eventos_inferidos(tickers_por_cnpj: dict, anos_fre, anos_itr, anos_cotahist) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Eventos confirmados por ticker, no formato de `b3.eventos_ativos_empresa`.

    Devolve (eventos, descartados). `fator_b3` segue a convenção da B3:
    aumento percentual em desdobramento/bonificação, razão remanescente em
    grupamento.
    """
    cand = pd.concat([candidatos_fre(anos_fre), candidatos_itr(anos_itr)], ignore_index=True)
    cand = cand[cand['cnpj'].isin(tickers_por_cnpj)]
    precos = precos_com_distribuicao(tuple(anos_cotahist))
    achados, descartados = [], []
    for c in cand.itertuples(index=False):
        for ticker in tickers_por_cnpj[c.cnpj]:
            achado = localizar(precos[precos['ticker'].eq(ticker)], c)
            if achado is None:
                descartados.append({'ticker': ticker, 'inicio': c.inicio, 'fim': c.fim,
                                    'razao': c.razao, 'fonte': c.fonte})
                continue
            tipo = 'GRUPAMENTO' if c.razao < 1 else ('DESDOBRAMENTO' if c.razao >= 1.9 else 'BONIFICACAO')
            achados.append({'ticker': ticker, 'ultimo_com': achado['ultimo_com'],
                            'aprovado_em': achado['ultimo_com'], 'tipo': tipo,
                            'fator_b3': c.razao if tipo == 'GRUPAMENTO' else (c.razao - 1) * 100,
                            'razao': c.razao, 'fonte': c.fonte, **achado})
    eventos = pd.DataFrame(achados)
    if not eventos.empty:
        # FRE e ITR achando o mesmo pregão: um evento só.
        eventos = eventos.sort_values('fonte').drop_duplicates(['ticker', 'ex'])
    return eventos, pd.DataFrame(descartados)
