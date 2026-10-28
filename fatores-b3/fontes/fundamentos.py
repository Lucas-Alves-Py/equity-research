"""Fatores fundamentais CVM conhecidos na data de corte.

Uso: `fundamentos_cvm([2024, 2025, 2026], '2026-09-25')` devolve uma linha
por CNPJ. A Fintz não participa desta transformação; serve exclusivamente
para a reconciliação histórica em módulo separado.

ROIC v3 = (EBIT TTM + IR/CSLL TTM da DRE 3.08) / capital financiado bruto.
O imposto entra com o sinal publicado pela CVM (despesa negativa). Capital
financiado bruto = patrimônio líquido + dívida bruta, usando o balanço mais
recente até o fim do TTM. É a fórmula do gabarito Fintz usado no backtest:
em 2025-11-03, nas ações líquidas, erro mediano de 0,00 p.p. e 92% até 1 p.p.
(149 comparáveis). A v2 usava alíquota efetiva e descartava quem tinha LAIR
negativo ou crédito fiscal, 54 ações líquidas que o backtest tinha. A
alíquota efetiva fica só como diagnóstico. Ausência ou base não positiva
produz NaN e motivo de quarentena.
"""

from __future__ import annotations

import math

import pandas as pd

from fontes import cvm


def _troca_de_exercicio(d: pd.DataFrame) -> pd.Series:
    """Linhas de emissor com duas datas-base diferentes no mesmo trimestre.

    Acontece quando a empresa muda o encerramento do exercício (DFP em 31/07
    e ITR em 30/09 de 2021). Mesma data repetida não entra: é erro de dados.
    """
    datas = d.groupby(['cnpj', 'periodo'])['dt_refer'].transform('nunique')
    return d['cnpj'].isin(d.loc[datas.gt(1), 'cnpj'])


def _ttm_contiguo(serie: pd.DataFrame, valor: str, as_of: pd.Timestamp) -> pd.DataFrame:
    """Soma os últimos quatro trimestres sem aceitar lacunas ou informação futura."""
    if serie.empty:
        return pd.DataFrame(columns=['cnpj', valor + '_ttm', 'dt_refer', 'dt_receb'])
    s = serie.copy()
    s = s[s['dt_receb'].lt(as_of) & s['dt_refer'].lt(as_of)].copy()
    s['periodo'] = s['dt_refer'].dt.to_period('Q')
    # Com troca de exercício não há quatro trimestres comparáveis: o emissor sai.
    s = s[~_troca_de_exercicio(s)]
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
            # Mesma versão recebida duas vezes: vale o último recebimento
            # antes do corte, como em `cvm._contas`.
            p = (p[p['dt_receb'].lt(corte)].sort_values('dt_receb')
                 .drop_duplicates(['cnpj', 'dt_refer', 'versao'], keep='last'))
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


def quantidade_acoes_cvm(anos: list[int], as_of: str | pd.Timestamp) -> pd.DataFrame:
    """Ações ON/PN integralizadas do último ITR/DFP antes do corte, em unidades.

    Parte das empresas informa a composição do capital em milhares de ações,
    sem dizer a unidade. O Formulário de Referência (FRE) informa sempre em
    unidades; a razão entre os dois (perto de 1 ou de 1.000) dá a escala de
    cada emissor. Sem FRE compatível nos três anos até o corte, o emissor fica
    de fora. A CVM só publica a composição no ITR/DFP desde 2020.
    """
    corte = pd.Timestamp(as_of).normalize()
    d = capital_cvm(anos, corte)
    total = d['on_integralizadas_bruto'] + d['pn_integralizadas_bruto']
    escala = pd.Series(float('nan'), index=d.index)
    for ano in range(corte.year, corte.year - 3, -1):
        try:
            fre = cvm.tabela('FRE', ano, 'capital_social')
        except Exception:
            continue
        # A TIM só declara capital subscrito; integralizado tem preferência.
        ordem = {'Capital Integralizado': 0, 'Capital Subscrito': 1, 'Capital Emitido': 2}
        fre = (fre.assign(_ordem=fre['Tipo_Capital'].map(ordem)).dropna(subset=['_ordem'])
               .sort_values(['_ordem', 'Versao'], ascending=[False, True])
               .drop_duplicates('CNPJ_Companhia', keep='last')
               .set_index('CNPJ_Companhia')['Quantidade_Total_Acoes'])
        # Potência de 1.000 mais próxima. O FRE pode estar antes de um
        # desdobramento (KEPL3 em 08/2022: razão 333 era milhar com split 3:1);
        # aceita eventos de até ~30:1 entre as duas fontes.
        log = (d['cnpj'].map(fre) / total).where(lambda r: r.gt(0)).map(math.log10)
        potencia = (log / 3).round()
        achada = (1000.0 ** potencia).where((log - 3 * potencia).abs().lt(1.5) & potencia.isin([0, 1]))
        escala = escala.fillna(achada)
    d = d.assign(escala=escala).dropna(subset=['escala'])
    return pd.DataFrame({'cnpj': d['cnpj'], 'dt_refer': d['dt_refer'], 'dt_receb': d['dt_receb'],
                         'acoes_on': d['on_integralizadas_bruto'] * d['escala'],
                         'acoes_pn': d['pn_integralizadas_bruto'] * d['escala'],
                         'escala': d['escala']}).reset_index(drop=True)


COLUNAS_DESDOBRAMENTO = [(f'Quantidade_{c}_Antes_Aprovacao', f'Quantidade_{c}_Depois_Aprovacao')
                         for c in ('Acoes_Ordinarias', 'Acoes_Preferenciais', 'Total_Acoes')]


def quantidade_acoes_fre(as_of: str | pd.Timestamp) -> pd.DataFrame:
    """Ações ON/PN do capital social do FRE, para datas anteriores a 2020.

    O ITR/DFP só traz a composição do capital desde 2020. O FRE traz o capital
    em unidades, mas os dados abertos guardam só a última versão de cada ano.
    Regras: vale o FRE do ano mais recente cuja primeira versão já tinha sido
    recebida no corte; capital aprovado depois do corte manda para o ano
    anterior. A última versão pode refletir desdobramento, grupamento ou
    bonificação posteriores ao corte (ou não refletir um anterior): com a
    tabela de desdobramentos do FRE, o evento posterior ao corte é desfeito se
    a quantidade guardada for a de depois dele, e o anterior é aplicado se for
    a de antes.
    """
    corte = pd.Timestamp(as_of).normalize()
    ordem = {'Capital Integralizado': 0, 'Capital Subscrito': 1, 'Capital Emitido': 2}
    candidatos, eventos = [], []
    for ano in range(corte.year - 3, corte.year + 2):
        try:
            principal = cvm.tabela('FRE', ano)
            capital = cvm.tabela('FRE', ano, 'capital_social')
            desdobra = cvm.tabela('FRE', ano, 'capital_social_desdobramento')
        except Exception:
            continue
        eventos.append(desdobra)
        if ano > corte.year:
            continue
        principal = principal.assign(dt_receb=pd.to_datetime(principal['DT_RECEB'], errors='coerce'))
        primeira = principal.groupby('CNPJ_CIA')['dt_receb'].min()
        c = capital.assign(_ordem=capital['Tipo_Capital'].map(ordem)).dropna(subset=['_ordem'])
        c = c.sort_values('_ordem').drop_duplicates('CNPJ_Companhia')
        c = c.assign(ano=ano, primeira=c['CNPJ_Companhia'].map(primeira),
                     aprovado=pd.to_datetime(c['Data_Autorizacao_Aprovacao'], errors='coerce'))
        candidatos.append(c[c['primeira'].lt(corte) & ~c['aprovado'].ge(corte)])
    colunas = ['cnpj', 'dt_refer', 'dt_receb', 'acoes_on', 'acoes_pn', 'escala']
    if not candidatos:
        return pd.DataFrame(columns=colunas)
    c = pd.concat(candidatos).sort_values('ano').drop_duplicates('CNPJ_Companhia', keep='last')
    on = pd.to_numeric(c['Quantidade_Acoes_Ordinarias'], errors='coerce').astype(float).to_numpy()
    pn = pd.to_numeric(c['Quantidade_Acoes_Preferenciais'], errors='coerce').astype(float).to_numpy()
    if eventos:
        e = pd.concat(eventos)
        e = e.assign(data=pd.to_datetime(e['Data_Aprovacao'], errors='coerce'))
        e = e.drop_duplicates(['CNPJ_Companhia', 'data', 'Tipo_Evento',
                               'Quantidade_Total_Acoes_Antes_Aprovacao'])
        e = e.set_index('CNPJ_Companhia')
        for i, cnpj in enumerate(c['CNPJ_Companhia']):
            if cnpj not in e.index:
                continue
            # Desfaz primeiro os posteriores ao corte, do mais novo ao mais antigo;
            # depois aplica os anteriores, do mais antigo ao mais novo.
            evs = e.loc[[cnpj]].sort_values('data')
            for depois_do_corte, ordem in ((True, evs.iloc[::-1]), (False, evs)):
                for _, ev in ordem.iterrows():
                    if (ev['data'] >= corte) != depois_do_corte:
                        continue
                    q = [pd.to_numeric(ev[col], errors='coerce') for par in COLUNAS_DESDOBRAMENTO for col in par]
                    antes, depois = q[4], q[5]
                    if not (antes > 0 and depois > 0):
                        continue
                    f_on = q[1] / q[0] if q[0] > 0 and q[1] > 0 else depois / antes
                    f_pn = q[3] / q[2] if q[2] > 0 and q[3] > 0 else depois / antes
                    # A versão guardada nem sempre foi atualizada depois do
                    # evento (TCSA3, TOTS3): decide pela quantidade, não pela data.
                    total = on[i] + pn[i]
                    if depois_do_corte and abs(total / depois - 1) < 0.02:
                        on[i], pn[i] = on[i] / f_on, pn[i] / f_pn
                    elif not depois_do_corte and abs(total / antes - 1) < 0.02:
                        on[i], pn[i] = on[i] * f_on, pn[i] * f_pn
    return pd.DataFrame({'cnpj': c['CNPJ_Companhia'].to_numpy(),
                         'dt_refer': pd.to_datetime(c['Data_Referencia']).to_numpy(),
                         'dt_receb': c['primeira'].to_numpy(),
                         'acoes_on': on, 'acoes_pn': pn, 'escala': 1.0}).dropna(subset=['acoes_on'])


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
    # Balanço é foto: na troca de exercício vale o mais recente do trimestre.
    troca = _troca_de_exercicio(b)
    b = pd.concat([b[~troca], b[troca].sort_values('dt_refer')
                   .drop_duplicates(['cnpj', 'periodo'], keep='last')])
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
    taxa_valida = out['imposto_ttm'].notna()
    cap_valido = out['capital_financiado_bruto'].gt(0)
    out['nopat_ttm'] = out['ebit_ttm'] + out['imposto_ttm']
    out['ROIC_liquido_medio_diagnostico'] = out['nopat_ttm'].div(
        out['capital_investido_medio'].where(out['capital_investido_medio'].gt(0)))
    out['ROIC'] = out['nopat_ttm'].div(out['capital_financiado_bruto'])
    out.loc[~(taxa_valida & cap_valido), 'ROIC'] = float('nan')
    out['metodo_roic'] = 'v3_ebit_mais_ir_dre_sobre_pl_mais_divida_bruta_atual'
    out['motivo_roic'] = 'ok'
    out.loc[~taxa_valida, 'motivo_roic'] = 'imposto_indisponivel'
    out.loc[~cap_valido, 'motivo_roic'] = 'capital_financiado_indisponivel_ou_nao_positivo'
    out.insert(0, 'as_of', corte)
    return out
