"""Fatores fundamentais CVM conhecidos na data de corte.

Uso: `fundamentos_cvm([2024, 2025, 2026], '2026-09-25')` devolve uma linha
por CNPJ. A Fintz não participa desta transformação; serve exclusivamente
para a reconciliação histórica em módulo separado.

ROIC v2 = EBIT TTM * (1 - alíquota efetiva TTM) / capital financiado bruto.
Alíquota = -IR/CSLL (DRE 3.08) / LAIR (DRE 3.07), aceita só entre 0 e 1 e
LAIR positivo. Capital financiado bruto = patrimônio líquido + dívida bruta,
usando o balanço mais recente até o fim do TTM. O capital líquido médio fica
como variante diagnóstica, não fator. Ausência ou base não positiva produz NaN
e motivo de quarentena; não se supõe imposto, preço ou quantidade de ações.
Esta convenção de capital fornecido por credores e acionistas obteve erro
mediano de 0,56-0,73 ponto percentual ante gabarito Fintz em dois cortes
históricos independentes (2024-11-01 e 2025-11-03). A fórmula exata da Fintz
não está documentada; diferenças por emissor continuam em quarentena.
"""

from __future__ import annotations

import pandas as pd

from fontes import cvm


def _ttm_contiguo(serie: pd.DataFrame, valor: str, as_of: pd.Timestamp) -> pd.DataFrame:
    """Soma os últimos quatro trimestres sem aceitar lacunas ou informação futura."""
    if serie.empty:
        return pd.DataFrame(columns=['cnpj', valor + '_ttm', 'dt_refer', 'dt_receb'])
    s = serie.copy()
    s = s[s['dt_receb'].lt(as_of) & s['dt_refer'].lt(as_of)].copy()
    s['periodo'] = s['dt_refer'].dt.to_period('Q')
    if s.duplicated(['cnpj', 'periodo']).any():
        raise ValueError(f'{valor} duplicado por CNPJ e trimestre no corte')
    linhas = []
    for cnpj, g in s.sort_values('periodo').groupby('cnpj'):
        ultimo = g.tail(4)
        if len(ultimo) != 4 or ultimo[valor].isna().any():
            continue
        periodos = ultimo['periodo'].astype('int64').to_numpy()
        if not ((periodos[1:] - periodos[:-1]) == 1).all():
            continue
        linhas.append({'cnpj': cnpj, valor + '_ttm': ultimo[valor].sum(),
                       'dt_refer': ultimo['dt_refer'].max(),
                       'dt_receb': ultimo['dt_receb'].max()})
    return pd.DataFrame(linhas, columns=['cnpj', valor + '_ttm',
                                         'dt_refer', 'dt_receb'])


def capital_cvm(anos: list[int], as_of: str | pd.Timestamp) -> pd.DataFrame:
    """Quantidade declarada ON/PN e tesouraria, bruta e com protocolo.

O CSV da CVM não informa unidade/milhar da composição do capital. Os números
devem ser reconciliados por emissor e classe com uma fonte datada antes de
virarem valor de mercado. Esta função deliberadamente não calcula EV.
    """
    corte = pd.Timestamp(as_of).normalize()
    partes = []
    for doc in ('ITR', 'DFP'):
        for ano in anos:
            d = cvm.tabela(doc, ano, 'composicao_capital')
            d = d.rename(columns={'CNPJ_CIA': 'cnpj', 'DT_REFER': 'dt_refer',
                                  'VERSAO': 'versao',
                                  'QT_ACAO_ORDIN_CAP_INTEGR': 'on_integralizadas_bruto',
                                  'QT_ACAO_PREF_CAP_INTEGR': 'pn_integralizadas_bruto',
                                  'QT_ACAO_ORDIN_TESOURO': 'on_tesouraria_bruto',
                                  'QT_ACAO_PREF_TESOURO': 'pn_tesouraria_bruto'})
            d['dt_refer'] = pd.to_datetime(d['dt_refer'], errors='coerce')
            d['versao'] = d['versao'].astype(str)
            p = cvm.protocolo([ano], strict=True)
            p = p[p['doc'].eq(doc)].copy()
            p['versao'] = p['versao'].astype(str)
            d = d.merge(p[['cnpj', 'dt_refer', 'versao', 'dt_receb']],
                        on=['cnpj', 'dt_refer', 'versao'], how='inner',
                        validate='many_to_one')
            d = d[d['dt_receb'].lt(corte) & d['dt_refer'].lt(corte)].copy()
            d['doc'] = doc
            partes.append(d)
    colunas = ['cnpj', 'dt_refer', 'dt_receb', 'versao', 'doc',
               'on_integralizadas_bruto', 'pn_integralizadas_bruto',
               'on_tesouraria_bruto', 'pn_tesouraria_bruto']
    if not partes:
        return pd.DataFrame(columns=colunas)
    d = pd.concat(partes, ignore_index=True)
    d = d.sort_values(['dt_refer', 'dt_receb', 'versao'])
    d = d.drop_duplicates('cnpj', keep='last')
    return d[colunas].reset_index(drop=True)


def fundamentos_cvm(anos: list[int], as_of: str | pd.Timestamp) -> pd.DataFrame:
    """Entrega fundamentos datados por emissor para o painel atual.

    A função é somente CVM; a união com ticker, preço e quantidade de ações
    reconciliada pertence ao orquestrador. Retorna NaN no ROIC se qualquer
    entrada obrigatória falhar, com `motivo_roic` para auditoria.
    """
    corte = pd.Timestamp(as_of).normalize()
    nomes = {
        'ebit': cvm.ebit_trimestral,
        'imposto': cvm.impostos_trimestral,
        'lair': cvm.lucro_antes_tributos_trimestral,
    }
    ttms = {nome: _ttm_contiguo(fn(anos, as_of=corte), nome, corte)
            for nome, fn in nomes.items()}
    ttm = ttms['ebit'].rename(columns={'dt_refer': 'dt_refer_dre',
                                       'dt_receb': 'dt_receb_ebit'})
    for nome in ('imposto', 'lair'):
        other = ttms[nome].rename(columns={'dt_refer': f'dt_refer_{nome}',
                                           'dt_receb': f'dt_receb_{nome}'})
        ttm = ttm.merge(other, on='cnpj', how='left', validate='one_to_one')
        # TTM de imposto/LAIR precisa terminar no mesmo trimestre do EBIT.
        ttm.loc[ttm[f'dt_refer_{nome}'].ne(ttm['dt_refer_dre']),
                nome + '_ttm'] = float('nan')
    ttm['dt_receb_dre'] = ttm[[c for c in ttm if c.startswith('dt_receb_')]].max(axis=1)

    b = cvm.balanco(anos, as_of=corte).copy()
    b = b[b['dt_refer'].lt(corte)].copy()
    b = b.merge(ttm[['cnpj', 'dt_refer_dre']], on='cnpj', how='inner',
                validate='many_to_one')
    b = b[b['dt_refer'].le(b['dt_refer_dre'])].copy()
    b['capital_investido'] = (b['patrimonio_liquido'] + b['divida_bruta'] - b['caixa'])
    b['capital_financiado_bruto'] = b['patrimonio_liquido'] + b['divida_bruta']
    b['periodo'] = b['dt_refer'].dt.to_period('Q')
    if b.duplicated(['cnpj', 'periodo']).any():
        raise ValueError('balanço duplicado por CNPJ e trimestre no corte')
    b = b.sort_values(['cnpj', 'periodo'])
    ultimos = b.groupby('cnpj').tail(1).copy()
    ultimos['periodo_anterior'] = ultimos['periodo'] - 4
    anterior = b[['cnpj', 'periodo', 'capital_investido']].rename(
        columns={'periodo': 'periodo_anterior',
                 'capital_investido': 'capital_investido_ano_anterior'})
    ultimos = ultimos.merge(anterior, on=['cnpj', 'periodo_anterior'],
                            how='left', validate='one_to_one')
    ultimos['capital_investido_medio'] = (
        ultimos['capital_investido'] + ultimos['capital_investido_ano_anterior']) / 2
    bal_cols = ['cnpj', 'dt_refer', 'dt_receb', 'doc', 'versao', 'ativo_total',
                'caixa', 'divida_bruta', 'patrimonio_liquido', 'capital_investido',
                'capital_financiado_bruto',
                'capital_investido_ano_anterior', 'capital_investido_medio']
    ultimos = ultimos[bal_cols].rename(columns={
        'dt_refer': 'dt_refer_balanco', 'dt_receb': 'dt_receb_balanco',
        'doc': 'doc_balanco', 'versao': 'versao_balanco'})
    out = ttm.merge(ultimos, on='cnpj', how='left', validate='one_to_one')
    # Um balanço futuro relativamente ao TTM seria uma mistura de janelas.
    out['aliquota_efetiva'] = -out['imposto_ttm'] / out['lair_ttm']
    taxa_valida = (out['lair_ttm'].gt(0) & out['aliquota_efetiva'].between(0, 1))
    cap_valido = out['capital_financiado_bruto'].gt(0)
    out['nopat_ttm'] = out['ebit_ttm'] * (1 - out['aliquota_efetiva'])
    out['ROIC_liquido_medio_diagnostico'] = out['nopat_ttm'].div(
        out['capital_investido_medio'].where(out['capital_investido_medio'].gt(0)))
    out['ROIC'] = out['nopat_ttm'].div(out['capital_financiado_bruto'])
    out.loc[~(taxa_valida & cap_valido), 'ROIC'] = float('nan')
    out['metodo_roic'] = 'v2_nopat_efetivo_sobre_pl_mais_divida_bruta_atual'
    out['motivo_roic'] = 'ok'
    out.loc[~taxa_valida, 'motivo_roic'] = 'imposto_ou_lair_indisponivel_ou_atipico'
    out.loc[~cap_valido, 'motivo_roic'] = 'capital_financiado_indisponivel_ou_nao_positivo'
    out.insert(0, 'as_of', corte)
    return out
