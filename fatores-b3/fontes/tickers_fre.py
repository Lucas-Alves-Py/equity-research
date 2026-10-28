"""Ticker -> CNPJ antes de 2018, pela cotação máxima e mínima de cada trimestre.

O FCA só traz código de negociação a partir de 2018 (de 2011 a 2017 a coluna
vem vazia). O FRE, porém, informa por empresa, espécie de ação e trimestre a
maior e a menor cotação na bolsa (`volume_valor_mobiliario`). O mesmo par de
extremos sai do COTAHIST para cada ticker; quem bate trimestre após trimestre
é o mesmo papel. Casar por nome pegaria a subsidiária em silêncio.

A empresa pode informar extremos de fechamento ou do intradia; aceito os dois.
Units (sufixo 11) entram como certificados de depósito. Regra (14/10/2026):
máxima e mínima a até 2% em pelo menos 3 trimestres, com o CNPJ vencedor ao
menos 3 vezes à frente do segundo. Contra o FCA de 2018 em diante, nos 290
tickers presentes nas duas fontes, acertou 289 (DTEX3 saiu como Cemig; o FCA
tem prioridade). Comparar só o formato (fator constante entre FRE e B3, para
quem informa cotação ajustada) cobriu menos e errou mais. Ficam de fora 5 a 7
papéis líquidos por data cujo FRE não traz cotações (AEDU3, GETI3/4, CTIP3...).
"""

from __future__ import annotations

import zipfile

import numpy as np
import pandas as pd

from carteira import cotahist
from fontes import cvm

# Dígito do ticker -> espécie/classe como o FRE escreve.
CLASSE = {'3': 'ON', '4': 'PN', '5': 'PNA', '6': 'PNB', '7': 'PNC', '8': 'PND', '11': 'UNT'}
CLASSE_FRE = {'Preferencial Classe A': 'PNA', 'Preferencial Classe B': 'PNB',
              'Preferencial Classe C': 'PNC', 'Preferencial Classe D': 'PND'}
CODBDI_ACOES = (b'02', b'07', b'08')


def cotacoes_fre(anos) -> pd.DataFrame:
    partes = []
    for ano in anos:
        d = cvm.tabela('FRE', ano, 'volume_valor_mobiliario')
        unit = d['Valor_Mobiliario'].str.contains('Certificados de [Dd]epósito', na=False)
        d = d[(d['Valor_Mobiliario'].eq('Ações') | unit) & d['Mercado_Valor_Mobiliario'].eq('Bolsa')]
        partes.append(d.assign(unit=unit))
    d = pd.concat(partes, ignore_index=True)
    especie = np.where(d['unit'], 'UNT', np.where(d['Especie_Acao'].eq('Ordinária'), 'ON',
                       d['Classe_Acao_Preferencial'].map(CLASSE_FRE).fillna('PN')))
    escala = np.where(d['Escala_Cotacao'].astype(str).str.contains('1000'), 1000.0, 1.0)
    saida = pd.DataFrame({
        'cnpj': d['CNPJ_Companhia'].str.replace(r'\D', '', regex=True),
        'nome': d['Nome_Companhia'],
        'documento': d['ID_Documento'],
        'trimestre': pd.to_datetime(d['Data_Fim_Trimestre'], errors='coerce').dt.to_period('Q'),
        'classe': especie,
        'maxima': pd.to_numeric(d['Valor_Maior_Cotacao'], errors='coerce') / escala,
        'minima': pd.to_numeric(d['Valor_Menor_Cotacao'], errors='coerce') / escala})
    saida = saida[(saida['maxima'] > 0) & (saida['minima'] > 0)].dropna(subset=['trimestre'])
    return saida.drop_duplicates(['documento', 'trimestre', 'classe', 'maxima', 'minima'])


def extremos_cotahist(anos) -> pd.DataFrame:
    linhas = []
    for ano in anos:
        with zipfile.ZipFile(cotahist.baixar(ano)) as z, z.open(z.namelist()[0]) as f:
            for l in f:
                if l[:2] != b'01' or l[24:27] != b'010' or l[10:12] not in CODBDI_ACOES:
                    continue
                fator = int(l[210:217]) or 1
                linhas.append((l[2:10], l[12:24].strip(), int(l[69:82]) / 100 / fator,
                               int(l[82:95]) / 100 / fator, int(l[108:121]) / 100 / fator))
    d = pd.DataFrame(linhas, columns=['data', 'ticker', 'premax', 'premin', 'fechamento'])
    d['ticker'] = d['ticker'].str.decode('latin-1')
    d['trimestre'] = pd.to_datetime(d['data'].str.decode('ascii'), format='%Y%m%d').dt.to_period('Q')
    d = d[d['ticker'].str.fullmatch(r'[A-Z][A-Z0-9]{3}([3-8]|11)')]
    g = d.groupby(['ticker', 'trimestre'])
    saida = g.agg(max_intradia=('premax', 'max'), min_intradia=('premin', 'min'),
                  max_fechamento=('fechamento', 'max'), min_fechamento=('fechamento', 'min'),
                  pregoes=('data', 'size')).reset_index()
    saida['classe'] = saida['ticker'].str[4:].map(CLASSE)
    return saida


def mapear(anos_fre, anos_cotahist, tolerancia: float = 0.02, minimo: int = 3) -> pd.DataFrame:
    """Uma linha por ticker identificado: CNPJ, trimestres casados e o segundo colocado."""
    fre, cot = cotacoes_fre(anos_fre), extremos_cotahist(anos_cotahist)
    par = cot.merge(fre, on=['trimestre', 'classe'])
    bate = np.zeros(len(par), bool)
    for tipo in ('intradia', 'fechamento'):
        bate |= (((par['maxima'] / par[f'max_{tipo}'] - 1).abs() <= tolerancia) &
                 ((par['minima'] / par[f'min_{tipo}'] - 1).abs() <= tolerancia)).to_numpy()
    contagem = (par[bate].groupby(['ticker', 'cnpj'])
                .agg(trimestres=('trimestre', 'nunique'), nome=('nome', 'last'),
                     primeiro=('trimestre', 'min'), ultimo=('trimestre', 'max'))
                .reset_index().sort_values(['ticker', 'trimestres'], ascending=[True, False]))
    posicao = contagem.groupby('ticker').cumcount()
    melhor = contagem[posicao.eq(0)].set_index('ticker')
    melhor['segundo'] = contagem[posicao.eq(1)].set_index('ticker')['trimestres'].reindex(melhor.index).fillna(0)
    melhor = melhor[(melhor['trimestres'] >= minimo) & (melhor['segundo'] * 3 <= melhor['trimestres'])]
    return melhor.reset_index()


def master_historico(anos_fca, mapa_fre: pd.DataFrame) -> pd.DataFrame:
    """security_master do FCA mais os tickers que só o FRE identifica (sem janela de datas)."""
    fca = cvm.security_master(anos_fca)
    extra = mapa_fre[~mapa_fre['ticker'].isin(fca['ticker'])]
    cnpj = extra['cnpj'].str.replace(r'^(\d{2})(\d{3})(\d{3})(\d{4})(\d{2})$', r'\1.\2.\3/\4-\5', regex=True)
    extra = pd.DataFrame({'cnpj': cnpj, 'ticker': extra['ticker'], 'nome': extra['nome'],
                          'inicio': pd.NaT, 'ano_fca': pd.NA, 'fim': pd.NaT})
    return pd.concat([fca, extra], ignore_index=True)
