"""FCF, qualidade do lucro e ROIC de SBFG3 -- direto da CVM.

Por que existir
---------------
A cobertura de SBFG3 nasceu so com a API Fintz, que nao publica demonstracao de
fluxo de caixa. Sem CFO e sem capex nao existe FCF, e o DCF ficava bloqueado. A base em lote da Fintz tambem parou em 2025-11-03, o que
congelou o corte contabil da cobertura em 2T25.

A CVM resolve as duas coisas de uma vez: tem a DFC e tem o dado ate 2T26.

Reaproveita fontes/cvm.py de fatores-b3 -- download, cache em disco,
fallback consolidado/individual e conversao de MIL para unidade ja estao la.

A armadilha do CD_CONTA
-----------------------
Verificado contra os arquivos, nao suposto: os codigos de sub-conta da DFC
trocam de significado entre trimestres da mesma empresa. Nos ITRs de 1T23 e
2T23 da SBF, 6.03.04 era "Dividedos pagos" (o typo e da companhia) e virou
"Arrendamentos pagos" no 3T23; 6.03.09 era "Adiantamento para futuro aumento de
capital" e virou "Dividendos pagos". Casar por codigo daria numero errado sem
levantar erro nenhum.

Por isso as sub-contas sao casadas por DESCRICAO normalizada. So o primeiro
nivel (6.01 a 6.05) e a DRE (3.01 a 3.11) usam codigo: essas duas faixas sao
estrutura fixa da CVM, igual para toda empresa nao financeira.

A DFC so vem acumulada
----------------------
Ao contrario da DRE, a CVM nunca publica a janela isolada do trimestre na DFC:
so 3, 6 e 9 meses (ITR) e 12 meses (DFP). Todo trimestre isolado sai por
diferenca contra o acumulado anterior, e o 4T sai da DFP menos o 9M do ITR.
Somar quatro linhas de ITR contaria o comeco do ano varias vezes.
"""

from __future__ import annotations

import json
import os
import sys
import unicodedata

import pandas as pd

AQUI = os.path.dirname(os.path.abspath(__file__))
RAIZ = os.path.abspath(os.path.join(AQUI, '..', '..'))
sys.path.insert(0, os.path.join(RAIZ, 'fatores-b3'))

from fontes import cvm  # noqa: E402

TICKER = 'SBFG3'
ANOS = (2023, 2024, 2025, 2026)


def cnpj_do_ticker(ticker: str = TICKER) -> str:
    """Resolve o CNPJ pelo ticker, em vez de fixar a constante.

    Nao e preciosismo. O grupo entrega DUAS demonstracoes a CVM:

        Grupo SBF S.A.                          13.217.485/0001-11  <- tem o SBFG3
        SBF Comercio de Produtos Esportivos     06.347.409/0001-65  <- subsidiaria integral

    As duas publicam ITR, DFP e DFC consolidada, com numeros proximos mas
    diferentes -- o CFO de 2025 e R$ 641,7 mi na holding e R$ 642,8 mi na
    subsidiaria. Casar por nome (DENOM_CIA contendo "SBF") pega a errada e nada
    no resultado denuncia o engano. O security master da CVM resolve pelo codigo
    de negociacao, que e o unico identificador que liga a acao a entidade certa.
    """

    sm = cvm.security_master(ANOS)
    achado = sm[sm['ticker'].eq(ticker)]
    if achado.empty:
        raise ValueError(f'{ticker} nao encontrado no security master da CVM')
    return str(achado['cnpj'].iloc[0])


CNPJ = cnpj_do_ticker()

# DRE: estrutura fixa da CVM para empresa nao financeira.
RECEITA, EBIT, LAIR, IMPOSTOS, LUCRO = '3.01', '3.05', '3.07', '3.08', '3.11'


def normalizar(texto: str) -> str:
    """Sem acento, minusculo, espaco colapsado. Para casar descricao."""
    sem_acento = unicodedata.normalize('NFKD', str(texto))
    sem_acento = ''.join(c for c in sem_acento if not unicodedata.combining(c))
    return ' '.join(sem_acento.lower().split())


def eh_capex(desc: str) -> bool:
    d = normalizar(desc)
    return 'adicoes' in d and ('imobilizado' in d or 'intangivel' in d)


def eh_arrendamento(desc: str) -> bool:
    return 'arrendamentos pagos' in normalizar(desc)


def eh_distribuicao(desc: str) -> bool:
    """Dividendo ou JCP PAGO. 'JCP recebido' e entrada e nao pode entrar aqui."""
    d = normalizar(desc)
    if 'pago' not in d:
        return False
    return 'divid' in d or 'capital proprio' in d   # 'divid' cobre o typo 'dividedos'


def dfc_acumulada(anos=ANOS) -> pd.DataFrame:
    """Uma linha por (data de referencia, janela, conceito). Valores acumulados."""

    partes = []
    for doc in ('ITR', 'DFP'):
        for ano in anos:
            try:
                d = cvm.tabela(doc, ano, 'DFC_MI_con')
            except Exception:
                continue
            d = d[d['CNPJ_CIA'].eq(CNPJ) & d['ORDEM_EXERC'].eq(cvm.CORRENTE)]
            if d.empty:
                continue
            partes.append(d.assign(doc=doc))

    d = pd.concat(partes, ignore_index=True)
    d['valor'] = pd.to_numeric(d['VL_CONTA'], errors='coerce') * 1000   # MIL -> unidade
    d['ref'] = pd.to_datetime(d['DT_REFER'])
    d['ini'] = pd.to_datetime(d['DT_INI_EXERC'])
    d['fim'] = pd.to_datetime(d['DT_FIM_EXERC'])
    d['meses'] = ((d['fim'] - d['ini']).dt.days / 30.44).round()
    d = d[d['meses'].between(1, 12)]

    nivel = d['CD_CONTA'].str.count(r'\.')
    filho_de_investimento = d['CD_CONTA'].str.startswith('6.02.') & nivel.eq(2)
    filho_de_financiamento = d['CD_CONTA'].str.startswith('6.03.') & nivel.eq(2)

    def bloco(mascara, conceito):
        sub = d[mascara]
        if sub.empty:
            return None
        g = sub.groupby(['ref', 'meses'], as_index=False)['valor'].sum()
        return g.assign(conceito=conceito)

    blocos = [
        bloco(d['CD_CONTA'].eq('6.01'), 'cfo'),
        bloco(d['CD_CONTA'].eq('6.02'), 'investimento'),
        bloco(d['CD_CONTA'].eq('6.03'), 'financiamento'),
        bloco(d['CD_CONTA'].eq('6.04'), 'cambial'),
        bloco(d['CD_CONTA'].eq('6.05'), 'variacao_caixa'),
        bloco(filho_de_investimento & d['DS_CONTA'].map(eh_capex), 'capex'),
        bloco(filho_de_financiamento & d['DS_CONTA'].map(eh_arrendamento), 'arrendamento_pago'),
        bloco(filho_de_financiamento & d['DS_CONTA'].map(eh_distribuicao), 'distribuicao'),
    ]
    return pd.concat([b for b in blocos if b is not None], ignore_index=True)


def trimestres(acum: pd.DataFrame) -> pd.DataFrame:
    """Trimestre isolado por diferenca contra o acumulado anterior do mesmo ano."""

    saida = []
    for (conceito, ano), g in acum.assign(ano=acum['ref'].dt.year).groupby(['conceito', 'ano']):
        anterior = 0.0
        for _, linha in g.sort_values('meses').iterrows():
            saida.append({
                'conceito': conceito,
                'ref': linha['ref'],
                'trimestre': f"{linha['ref'].quarter}T{str(ano)[2:]}",
                'valor': linha['valor'] - anterior,
            })
            anterior = linha['valor']
    return pd.DataFrame(saida).sort_values(['conceito', 'ref']).reset_index(drop=True)


def dre_trimestral(anos=ANOS) -> pd.DataFrame:
    """Mesmo desmonte, para as linhas da DRE que o ROIC e o CFO/lucro pedem."""

    partes = []
    for doc in ('ITR', 'DFP'):
        for ano in anos:
            try:
                d = cvm._contas(doc, ano, 'DRE', [RECEITA, EBIT, LAIR, IMPOSTOS, LUCRO])
            except Exception:
                continue
            partes.append(d[d['cnpj'].eq(CNPJ)])

    d = pd.concat(partes, ignore_index=True)
    d['meses'] = ((d['fim'] - d['ini']).dt.days / 30.44).round()
    d = d[d['meses'].between(1, 12)]
    # a DRE as vezes publica a janela de 3 meses junto da acumulada; fica so a acumulada
    # para que o desmonte seja o mesmo da DFC e os dois casem trimestre a trimestre.
    acum = (d[d['meses'].isin([3, 6, 9, 12])]
            .groupby(['conta', 'dt_refer', 'meses'], as_index=False)['valor'].last()
            .rename(columns={'dt_refer': 'ref', 'conta': 'conceito'}))
    dedup = acum.sort_values('meses').drop_duplicates(['conceito', 'ref'], keep='last')
    return trimestres(dedup)


def reconciliacao(dre: pd.DataFrame) -> dict:
    """Quanto a CVM difere da Fintz nas mesmas linhas e no mesmo ano.

    Nao e erro de leitura: sao definicoes diferentes. O 3.01 da CVM para a SBF
    e receita bruta; a ReceitaLiquida da Fintz e liquida. O 3.11 e o resultado
    consolidado, que inclui minoritarios. Medir a diferenca uma vez evita que
    alguem compare um numero de uma fonte com o de outra dentro da cobertura.
    """

    caminho = os.path.join(AQUI, 'analise.json')
    if not os.path.exists(caminho):
        return {}
    with open(caminho, encoding='utf-8') as f:
        fintz = json.load(f)['dre_anual']

    par = {'receita': (RECEITA, 'ReceitaLiquida'),
           'ebit': (EBIT, 'EBIT'),
           'lucro_liquido': (LUCRO, 'LucroLiquido')}

    saida = {}
    for ano in ('2023', '2024'):
        linhas = {}
        for nome, (cod, chave) in par.items():
            f_val = fintz.get(chave, {}).get(ano)
            c_val = dre[dre['conceito'].eq(cod) & dre['ref'].dt.year.eq(int(ano))]['valor'].sum()
            if f_val and c_val:
                linhas[nome] = {'cvm': c_val, 'fintz': f_val, 'razao_cvm_sobre_fintz': c_val / f_val}
        saida[ano] = linhas
    return saida


def ltm(tri: pd.DataFrame, conceito: str, ate: pd.Timestamp) -> float:
    """Soma dos quatro trimestres encerrados em `ate`."""
    s = tri[tri['conceito'].eq(conceito) & tri['ref'].le(ate)].sort_values('ref').tail(4)
    if len(s) < 4:
        raise ValueError(f'{conceito}: so {len(s)} trimestres ate {ate.date()}')
    return float(s['valor'].sum())


def conferir(acum: pd.DataFrame, tri: pd.DataFrame) -> list[str]:
    """As duas identidades que provam que o desmonte nao inventou numero."""

    notas = []
    largo = acum.pivot_table(index=['ref', 'meses'], columns='conceito', values='valor')

    soma = largo[['cfo', 'investimento', 'financiamento', 'cambial']].sum(axis=1)
    erro = (soma - largo['variacao_caixa']).abs().max()
    notas.append(f'6.01+6.02+6.03+6.04 = 6.05 em todas as janelas (erro max R$ {erro:,.0f})')
    assert erro < 1.0, f'identidade da DFC nao fecha: erro de R$ {erro:,.0f}'

    for ano in (2023, 2024, 2025):
        anual = largo.xs(12, level='meses')
        ref = [r for r in anual.index if r.year == ano]
        if not ref:
            continue
        publicado = anual.loc[ref[0], 'cfo']
        somado = tri[tri['conceito'].eq('cfo') & tri['ref'].dt.year.eq(ano)]['valor'].sum()
        assert abs(publicado - somado) < 1.0, f'{ano}: trimestres somam {somado}, DFP diz {publicado}'
        notas.append(f'{ano}: 4 trimestres de CFO somam a DFP anual (R$ {publicado / 1e6:,.1f} mi)')
    return notas


def main() -> None:
    acum = dfc_acumulada()
    tri = trimestres(acum)
    dre = dre_trimestral()
    notas = conferir(acum, tri)

    corte = tri['ref'].max()
    cfo = ltm(tri, 'cfo', corte)
    capex = ltm(tri, 'capex', corte)
    arrendamento = ltm(tri, 'arrendamento_pago', corte)
    distribuicao = ltm(tri, 'distribuicao', corte)
    lucro = ltm(dre, LUCRO, corte)
    ebit = ltm(dre, EBIT, corte)
    lair = ltm(dre, LAIR, corte)
    impostos = ltm(dre, IMPOSTOS, corte)
    receita = ltm(dre, RECEITA, corte)

    bal = cvm.balanco(ANOS)
    bal = bal[bal['cnpj'].eq(CNPJ) & bal['dt_refer'].eq(corte)].iloc[-1]
    divida_liquida = float(bal['divida_bruta'] - bal['caixa'])
    capital_investido = float(bal['patrimonio_liquido']) + divida_liquida

    aliquota = -impostos / lair if lair else float('nan')
    nopat = ebit * (1 - aliquota)
    # A aliquota efetiva da SBF e de um digito -- incentivo fiscal, nao erro de leitura.
    # Um ROIC que a assume perpetua vira otimista, entao o estatutario anda junto.
    ESTATUTARIA = 0.34
    nopat_estatutario = ebit * (1 - ESTATUTARIA)

    saida = {
        'gerado_em': pd.Timestamp.now().strftime('%Y-%m-%d'),
        'fonte': 'CVM -- ITR e DFP (dados.cvm.gov.br), DFC pelo metodo indireto',
        'ticker': TICKER,
        'cnpj': CNPJ,
        'entidade': 'Grupo SBF S.A. (holding listada), resolvida pelo security master da CVM',
        'data_corte': corte.strftime('%Y-%m-%d'),
        'verificacoes': notas,
        'aviso': ('Bloco 100% CVM. Nao comparar linha a linha com analise.json, '
                  'que e Fintz e para em 2T25: o 3.01 da CVM e receita bruta e o '
                  '3.11 inclui minoritarios. Ver reconciliacao_com_fintz.'),
        'ltm': {
            'cfo': cfo,
            'capex': capex,
            'fcf': cfo + capex,
            'fcf_pos_arrendamento': cfo + capex + arrendamento,
            'arrendamento_pago': arrendamento,
            'distribuicao_aos_acionistas': distribuicao,
            'receita_bruta_cvm_3_01': receita,
            'ebit': ebit,
            'lucro_liquido': lucro,
            'aliquota_efetiva': aliquota,
            'nopat': nopat,
        },
        'qualidade_do_lucro': {
            'cfo_sobre_lucro': cfo / lucro if lucro else None,
            'cfo_pos_arrendamento_sobre_lucro': (cfo + arrendamento) / lucro if lucro else None,
        },
        'retorno': {
            'capital_investido': capital_investido,
            'patrimonio_liquido': float(bal['patrimonio_liquido']),
            'divida_liquida': divida_liquida,
            'divida_bruta': float(bal['divida_bruta']),
            'caixa': float(bal['caixa']),
            'roic_aliquota_efetiva': nopat / capital_investido if capital_investido else None,
            'roic_aliquota_estatutaria_34': nopat_estatutario / capital_investido if capital_investido else None,
        },
        'reconciliacao_com_fintz': reconciliacao(dre),
        'trimestres': {
            conceito: {
                r['trimestre']: r['valor']
                for _, r in g.sort_values('ref').iterrows()
            }
            for conceito, g in tri.groupby('conceito')
        },
    }

    destino = os.path.join(AQUI, 'fluxo_caixa.json')
    with open(destino, 'w', encoding='utf-8') as f:
        json.dump(saida, f, ensure_ascii=False, indent=1)

    for n in notas:
        print('  ok  ', n)
    print(f'\ncorte {corte.date()} | CFO LTM R$ {cfo / 1e6:,.1f} mi | capex R$ {capex / 1e6:,.1f} mi'
          f' | FCF R$ {(cfo + capex) / 1e6:,.1f} mi')
    print(f'CFO/lucro {cfo / lucro:.2f}x | ROIC {nopat / capital_investido:.1%} na aliquota efetiva'
          f' de {aliquota:.1%}, {nopat_estatutario / capital_investido:.1%} a 34%')
    print(f'\ngravado em {destino}')


if __name__ == '__main__':
    main()
