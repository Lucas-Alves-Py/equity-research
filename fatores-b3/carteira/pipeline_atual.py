"""Uniao datada das fontes para um sinal TRIO_ROIC de treino.

Fintz nunca alimenta esta rotina: seus arquivos historicos sao gabarito de
reconciliacao. Este modulo nao escreve no ledger nem executa ordens.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
import io
from pathlib import Path

import pandas as pd

from carteira.carteira import CarteiraSistematica, validar_painel_atual

METODOLOGIA = Path(__file__).resolve().parent / 'config' / 'metodologia_pit_v3.json'
# Para bancos a conta CVM 3.05 nao e EBIT; o backtest (Fintz) tambem nao os tinha.
SETORES_EXCLUIDOS = {'Bancos'}


def metodologia_de_treino() -> dict:
    with METODOLOGIA.open(encoding='utf-8') as arquivo:
        manifesto = json.load(arquivo)
    if manifesto.get('status') != 'tecnicamente_validada_para_treino':
        raise ValueError('metodologia PIT sem validação versionada para treino')
    if manifesto.get('id') != 'TRIO_ROIC_PIT_V3_2026-10-03':
        raise ValueError('versão de metodologia PIT inesperada')
    return manifesto


class CoberturaInsuficiente(ValueError):
    def __init__(self, mensagem: str, diagnostico: dict):
        super().__init__(mensagem)
        self.diagnostico = diagnostico


def _unico(df: pd.DataFrame, chave: str, nome: str) -> None:
    if chave not in df or df[chave].isna().any() or df[chave].duplicated().any():
        raise ValueError(f'{nome}: chave {chave} ausente, nula ou duplicada')


def montar_painel_atual(precos: pd.DataFrame, fundamentos: pd.DataFrame,
                       security_master: pd.DataFrame,
                       capitalizacao: pd.DataFrame,
                       as_of: dt.date | str,
                       liquidez_minima: float = 500_000,
                       cobertura_minima: float = 0.70) -> pd.DataFrame:
    """Une preco por ticker aos fundamentos/capital por CNPJ no sinal.

    `capitalizacao` precisa conter valor de mercado de TODAS as classes de
    acoes em circulacao, data de aprovacao e cobertura_classes=True. O gerador
    desses dados deve reconciliar escala e quantidade por classe antes daqui.
    """
    corte = pd.Timestamp(as_of).date()
    exigencias = (
        (precos, {'data', 'ticker', 'preco_bruto',
                   'preco_fechamento_ajustado', 'volume', 'momento_12_meses',
                   'status_eventos', 'fonte_preco', 'fonte_eventos'}, 'precos'),
        (fundamentos, {'cnpj', 'ebit_ttm', 'ROIC', 'caixa', 'divida_bruta',
                        'dt_receb_dre', 'dt_receb_balanco'}, 'fundamentos'),
        (security_master, {'cnpj', 'ticker', 'inicio', 'fim'}, 'security_master'),
        (capitalizacao, {'cnpj', 'valor_mercado', 'data_aprovacao',
                          'cobertura_classes', 'fonte_capital'}, 'capitalizacao'),
    )
    for df, cols, nome in exigencias:
        if df is None or df.empty:
            raise ValueError(f'{nome}: captura ausente/vazia')
        faltantes = cols - set(df.columns)
        if faltantes:
            raise ValueError(f'{nome}: colunas ausentes {sorted(faltantes)}')

    p = precos.copy()
    p['ticker'] = p['ticker'].astype(str).str.upper().str.strip()
    _unico(p, 'ticker', 'precos')
    datas = pd.to_datetime(p['data'], errors='coerce').dt.date
    if datas.isna().any() or datas.nunique() != 1:
        raise ValueError('precos devem ter uma unica data de sinal')
    sinal = datas.iloc[0]
    if sinal > corte:
        raise ValueError('preco posterior ao corte de consulta')
    p['data'] = sinal

    m = security_master.copy()
    m['ticker'] = m['ticker'].astype(str).str.upper().str.strip()
    m['inicio'] = pd.to_datetime(m['inicio'], errors='coerce')
    m['fim'] = pd.to_datetime(m['fim'], errors='coerce')
    m = m[(m['inicio'].isna() | m['inicio'].le(pd.Timestamp(sinal))) &
          (m['fim'].isna() | m['fim'].ge(pd.Timestamp(sinal)))].copy()
    _unico(m, 'ticker', 'security_master vigente')

    f = fundamentos.copy()
    _unico(f, 'cnpj', 'fundamentos')
    c = capitalizacao.copy()
    _unico(c, 'cnpj', 'capitalizacao')
    for df, cols, nome in (
            (f, ['dt_receb_dre', 'dt_receb_balanco'], 'fundamentos'),
            (c, ['data_aprovacao'], 'capitalizacao')):
        for col in cols:
            datas_fonte = pd.to_datetime(df[col], errors='coerce')
            if datas_fonte.dt.date.ge(sinal).any():
                raise ValueError(f'{nome}: {col} posterior ao sinal {sinal}')
            if nome == 'capitalizacao' and datas_fonte.isna().any():
                raise ValueError(f'{nome}: {col} ausente')
    if not c['cobertura_classes'].eq(True).all():
        raise ValueError('capitalizacao sem todas as classes precificadas')
    c['valor_mercado'] = pd.to_numeric(c['valor_mercado'], errors='coerce')
    if c['valor_mercado'].isna().any() or not c['valor_mercado'].map(math.isfinite).all():
        raise ValueError('valor de mercado ausente/invalido')
    if c['valor_mercado'].le(0).any():
        raise ValueError('valor de mercado nao positivo')

    universo = p.merge(m[['ticker', 'cnpj']], on='ticker', how='inner',
                       validate='one_to_one')
    if universo.empty:
        raise ValueError('nenhum ticker mapeado ao security master vigente')
    painel = universo.merge(f, on='cnpj', how='left', validate='many_to_one')
    painel = painel.merge(c, on='cnpj', how='left', validate='many_to_one')
    liquidos = painel[pd.to_numeric(painel['volume'], errors='coerce')
                      > liquidez_minima]
    if liquidos.empty:
        raise ValueError('nenhum ticker com liquidez suficiente')
    painel['EV_EBIT'] = (
        painel['valor_mercado'] + painel['divida_bruta'] - painel['caixa']
    ) / painel['ebit_ttm']
    painel['fonte_fundamentos'] = 'CVM-ITR-DFP-PIT'
    painel['data'] = sinal
    obrigatorias = ['preco_fechamento_ajustado', 'volume',
                    'momento_12_meses', 'ROIC', 'EV_EBIT', 'cnpj',
                    'fonte_preco', 'fonte_eventos', 'fonte_capital']
    valido = painel[obrigatorias].notna().all(axis=1)
    numericas = ['preco_fechamento_ajustado', 'volume', 'momento_12_meses',
                 'ROIC', 'EV_EBIT']
    for col in numericas:
        valores = pd.to_numeric(painel[col], errors='coerce')
        valido &= valores.map(lambda x: math.isfinite(x) if pd.notna(x) else False)
    # O modelo congelado usa EV/EBIT bruto, inclusive EBIT negativo. O filtro
    # EV/EBIT_LIMPO foi estudado mas nao adotado na configuracao vigente.
    valido &= painel['ebit_ttm'].notna() & painel['ebit_ttm'].ne(0)
    valido &= painel['valor_mercado'].gt(0)
    valido &= painel['status_eventos'].eq('ok')
    # Base comparavel: empresas com ROIC e EBIT calculaveis na CVM. Empresas
    # sem esses dados ficam fora do universo antes da auditoria de cobertura.
    base = (painel['ticker'].isin(liquidos['ticker']) &
            painel['ROIC'].notna() & painel['ebit_ttm'].notna() &
            painel['ebit_ttm'].ne(0))
    n_base = int(base.sum())
    n_validos = int((valido & base).sum())
    diagnostico = {'validos_liquidos': n_validos,
                   'base_cvm_liquida': n_base,
                   'liquidos_mapeados': len(liquidos),
                   'sem_eventos': int((base & ~painel['status_eventos'].eq('ok')).sum()),
                   'sem_capital': int((base & painel['valor_mercado'].isna()).sum()),
                   'sem_roic': int((painel['ticker'].isin(liquidos['ticker']) &
                                    painel['ROIC'].isna()).sum()),
                   'ebit_zero_ou_ausente': int((painel['ticker'].isin(liquidos['ticker']) &
                                                (painel['ebit_ttm'].isna() |
                                                 painel['ebit_ttm'].eq(0))).sum())}
    if n_base == 0 or n_validos / n_base < cobertura_minima:
        raise CoberturaInsuficiente(
            f'cobertura dos tres fatores insuficiente: {n_validos}/{n_base} '
            f'(< {cobertura_minima:.0%})', diagnostico)
    resultado = painel[valido].copy().reset_index(drop=True)
    resultado.attrs['cobertura_fatores'] = diagnostico
    metodo = metodologia_de_treino()
    resultado.attrs['fatores_reconciliados'] = True
    resultado.attrs['versao_metodologia'] = metodo['id']
    resultado.attrs['quarentena_capital'] = capitalizacao.attrs.get(
        'quarentena_capital', [])
    resultado.attrs['precificacao_capital'] = capitalizacao.attrs.get(
        'precificacao_capital', [])
    resultado.attrs['eventos_sha256_por_emissor'] = precos.attrs.get(
        'eventos_sha256_por_emissor', {})
    return resultado


def _fechamentos_recentes(sinal, sessoes: int = 21) -> pd.Series:
    """Último fechamento de cada ticker nas `sessoes` anteriores ao sinal, inclusive."""
    from carteira import cotahist
    fim = pd.Timestamp(sinal)
    inicio = fim - pd.Timedelta(days=60)
    partes = [cotahist.parsear_periodo(cotahist.baixar(ano), inicio, fim)
              for ano in range(inicio.year, fim.year + 1)]
    c = pd.concat(partes, ignore_index=True)
    c = c[pd.to_datetime(c['data']).le(fim)]
    janela = sorted(c['data'].unique())[-sessoes:]
    c = c[c['data'].isin(janela)].sort_values('data')
    return c.groupby('ticker')['fechamento'].last()


def _razoes_pela_unit(sinal, sessoes: int = 252) -> pd.Series:
    """Preço de cada classe sobre o da unit no último pregão em que as duas negociaram.

    Empresas que negociam de verdade só pela unit (TAEE11, ALUP11) têm ON e PN
    com negócios esparsos. A razão do último pregão em comum, aplicada ao preço
    atual da unit, dá o preço da classe sem supor a composição da unit, que muda
    (SANB11 tinha 105 ações até 2013) e sem supor ON e PN com o mesmo preço.
    """
    from carteira import cotahist
    fim = pd.Timestamp(sinal)
    inicio = fim - pd.Timedelta(days=int(sessoes * 1.6))
    c = pd.concat([cotahist.parsear_periodo(cotahist.baixar(ano), inicio, fim)
                   for ano in range(inicio.year, fim.year + 1)], ignore_index=True)
    c = c[pd.to_datetime(c['data']).le(fim) & c['fechamento'].gt(0)]
    c = c[c['data'].isin(sorted(c['data'].unique())[-sessoes:])]
    c = c.assign(prefixo=c['ticker'].str[:4])
    unit = c[c['ticker'].str[4:].eq('11')].set_index(['data', 'prefixo'])['fechamento']
    classes = c[c['ticker'].str[4:].isin(['3', '4', '5', '6', '7', '8'])]
    comum = classes.join(unit.rename('unit'), on=['data', 'prefixo'], how='inner').sort_values('data')
    ultimo = comum.groupby('ticker').last()
    return ultimo['fechamento'] / ultimo['unit']


def capitalizacao_b3(precos: pd.DataFrame, master: pd.DataFrame,
                     anos: list[int], sinal: dt.date,
                     instante_decisao=None,
                     fechamentos_recentes: pd.Series | None = None,
                     capital: pd.DataFrame | None = None) -> pd.DataFrame:
    """Calcula market cap pelas quantidades B3 aprovadas e precos por classe.

    A B3 agrega todas as preferenciais. Se ha mais de uma serie PN negociada,
    a quantidade por serie e desconhecida: com precos a ate 15% um do outro,
    todas as PN levam o preco da serie mais liquida; senao, quarentena.
    `capital` injeta quantidades de outra fonte no mesmo formato (o histórico
    usa a CVM, porque a B3 só publica o estado atual).
    """
    del anos  # Quantidades vêm da B3; anos pertence ao contrato do provedor.
    instante = pd.Timestamp(instante_decisao or pd.Timestamp.now(
        tz='America/Sao_Paulo'))
    if instante.tzinfo is None:
        instante = instante.tz_localize('America/Sao_Paulo')
    if instante.date() < pd.Timestamp(sinal).date():
        raise ValueError('decisao anterior ao ultimo preco')
    if capital is None:
        from fontes.capital_b3 import capital_social_b3
        capital = capital_social_b3(instante)
    capital = capital[capital['elegivel_pit'].eq(True)].copy()
    if capital.empty:
        raise ValueError('nenhum capital B3 com captura anterior a decisao')
    if capital['prefixo'].duplicated().any():
        raise ValueError('capital B3 com prefixos duplicados')

    p = precos.copy()
    p['ticker'] = p['ticker'].astype(str).str.upper().str.strip()
    p['prefixo'] = p['ticker'].str[:4]
    p['preco_bruto'] = pd.to_numeric(p['preco_bruto'], errors='coerce')
    if p['ticker'].duplicated().any():
        raise ValueError('precos por classe duplicados')
    m = master[['cnpj', 'ticker', 'inicio', 'fim']].copy()
    m['ticker'] = m['ticker'].astype(str).str.upper().str.strip()
    m['inicio'] = pd.to_datetime(m['inicio'], errors='coerce')
    m['fim'] = pd.to_datetime(m['fim'], errors='coerce')
    m = m[(m['inicio'].isna() | m['inicio'].le(pd.Timestamp(sinal))) &
          (m['fim'].isna() | m['fim'].ge(pd.Timestamp(sinal)))].copy()
    m['prefixo'] = m['ticker'].str[:4]
    # Prefixo com dois CNPJs vigentes (no histórico, o cadastro atual da B3
    # devolve o dono de hoje de um código antigo) fica de fora, não o painel.
    por_prefixo = m.groupby('prefixo')['cnpj'].nunique()
    ambiguos = sorted(por_prefixo[por_prefixo.gt(1)].index)
    mapa = m[~m['prefixo'].isin(ambiguos)].drop_duplicates('prefixo').set_index('prefixo')['cnpj']

    volumes = p.set_index('ticker')['volume'] if 'volume' in p else pd.Series(1.0, index=p['ticker'])
    pela_unit = None
    linhas = []
    quarentena = [{'prefixo': x, 'motivo': 'prefixo_com_mais_de_um_cnpj'} for x in ambiguos]
    precificacao = []
    for _, cap in capital.iterrows():
        prefixo = cap['prefixo']
        if prefixo not in mapa:
            continue
        # Quantidade aprovada no dia do sinal ou depois nao era conhecida no
        # fechamento; so este emissor fica de fora, o painel segue.
        if pd.Timestamp(cap['data_aprovacao']).date() >= pd.Timestamp(sinal).date():
            quarentena.append({'prefixo': prefixo,
                               'motivo': 'capital_aprovado_no_sinal_ou_depois'})
            continue
        cotacoes = p[p['prefixo'].eq(prefixo)].set_index('ticker')['preco_bruto']
        on, pn = int(cap['acoes_on']), int(cap['acoes_pn'])
        classes_pn = [prefixo + final for final in ('4', '5', '6', '7', '8')
                      if prefixo + final in cotacoes.index]
        regras = []
        if pn > 0 and len(classes_pn) > 1:
            # A B3 agrega as preferenciais; com duas séries negociadas não se
            # sabe quantas ações há em cada uma (USIM5/6, CPLE5/6). Se valem
            # quase o mesmo, o erro de usar a mais líquida é pequeno.
            series = cotacoes[classes_pn].astype(float)
            if not (series.gt(0).all() and series.max() / series.min() <= 1.15):
                quarentena.append({'prefixo': prefixo,
                                   'motivo': f'classes_pn_ambiguas:{len(classes_pn)}'})
                continue
            classes_pn = [volumes[classes_pn].astype(float).idxmax()]
            regras.append(f'pn:serie_mais_liquida_de_{len(series)}')
        candidatos = {'on': [prefixo + '3'],
                      'pn': classes_pn or [prefixo + f for f in ('4', '5', '6', '7', '8')]}
        quantidades = {'on': on, 'pn': pn}
        precos_classe = {}
        for classe, qtd in quantidades.items():
            if qtd <= 0:
                precos_classe[classe] = 0.0
                continue
            preco = next((float(cotacoes[t]) for t in candidatos[classe]
                          if t in cotacoes.index), None)
            if preco is None:
                if fechamentos_recentes is None:
                    fechamentos_recentes = _fechamentos_recentes(sinal)
                achados = [t for t in candidatos[classe] if t in fechamentos_recentes.index]
                if len(achados) == 1:
                    preco = float(fechamentos_recentes[achados[0]])
                    regras.append(f'{classe}:ultimo_negocio_21_pregoes')
            if preco is None and qtd / (on + pn) < 1e-4:
                # Golden share (12 PN da Vale, 1 da Copel): sem negociação e
                # sem peso econômico.
                preco = 0.0
                regras.append(f'{classe}:classe_irrelevante_sem_preco')
            precos_classe[classe] = preco
        for classe, outra in (('on', 'pn'), ('pn', 'on')):
            # Classe que não negocia na bolsa (ON da Schulz e da Track & Field)
            # recebe o preço da classe negociada da mesma empresa.
            if precos_classe[classe] is None and precos_classe[outra]:
                peso = PESO_ECONOMICO_ON.get(prefixo, 1.0) if classe == 'on' else 1.0
                precos_classe[classe] = precos_classe[outra] * peso
                regras.append(f'{classe}:preco_da_classe_{outra}' + (f'_vezes_{peso:.4f}' if peso != 1.0 else ''))
        unit = prefixo + '11'
        if None in precos_classe.values() and unit in cotacoes.index:
            if pela_unit is None:
                pela_unit = _razoes_pela_unit(sinal)
            for classe, valor in precos_classe.items():
                achados = [t for t in candidatos[classe] if t in pela_unit.index]
                if valor is None and len(achados) == 1:
                    precos_classe[classe] = float(cotacoes[unit]) * float(pela_unit[achados[0]])
                    regras.append(f'{classe}:pela_unit')
        if any(v is None or not math.isfinite(v) or v < 0 for v in precos_classe.values()):
            quarentena.append({'prefixo': prefixo, 'motivo': 'classe_sem_preco'})
            continue
        preco_on, preco_pn = precos_classe['on'], precos_classe['pn']
        if regras:
            precificacao.append({'prefixo': prefixo, 'regras': regras})
        valor = on * preco_on + pn * preco_pn
        if not math.isfinite(valor) or valor <= 0:
            quarentena.append({'prefixo': prefixo, 'motivo': 'valor_mercado_invalido'})
            continue
        linhas.append({'cnpj': mapa[prefixo], 'valor_mercado': valor,
                       'data_aprovacao': cap['data_aprovacao'],
                       'cobertura_classes': True,
                       'fonte_capital': cap['fonte'],
                       'hash_capital': cap['sha256'],
                       'capturado_em_utc_capital': cap['capturado_em_utc']})
    retorno = pd.DataFrame(linhas)
    if retorno.empty:
        raise ValueError('nenhum emissor com capital e precos de todas as classes')
    _unico(retorno, 'cnpj', 'capitalizacao B3')
    retorno.attrs['quarentena_capital'] = quarentena
    retorno.attrs['precificacao_capital'] = precificacao
    return retorno


INICIO_COMPOSICAO_ITR = pd.Timestamp('2020-06-01')
# ON que não negocia e não vale o mesmo que a PN, pelo estatuto: na Azul, 75 ON
# têm o direito econômico de 1 PN. Sem isso o valor de mercado saía 3,7 vezes maior.
PESO_ECONOMICO_ON = {'AZUL': 1 / 75}


def capitalizacao_cvm(precos: pd.DataFrame, master: pd.DataFrame,
                      anos: list[int], sinal: dt.date,
                      instante_decisao=None) -> pd.DataFrame:
    """Market cap histórico: quantidades da CVM, regras de preço da B3.

    A B3 só publica o capital atual; para datas passadas a quantidade vem do
    último ITR/DFP recebido antes do sinal, que só traz a composição do capital
    desde o 1T20. Antes disso, vem do capital social do FRE.
    """
    from fontes.fundamentos import quantidade_acoes_cvm, quantidade_acoes_fre

    corte = pd.Timestamp(sinal)
    if corte < INICIO_COMPOSICAO_ITR:
        qtd, fonte = quantidade_acoes_fre(corte), 'CVM FRE capital_social'
    else:
        qtd, fonte = quantidade_acoes_cvm([a for a in anos if a >= 2020], corte), 'CVM ITR/DFP composicao_capital'

    m = master.copy()
    m['inicio'] = pd.to_datetime(m['inicio'], errors='coerce')
    m['fim'] = pd.to_datetime(m['fim'], errors='coerce')
    m = m[(m['inicio'].isna() | m['inicio'].le(corte)) & (m['fim'].isna() | m['fim'].ge(corte))]
    prefixos = (m.assign(prefixo=m['ticker'].astype(str).str.upper().str[:4])
                [['cnpj', 'prefixo']].drop_duplicates())
    # O FCA pode deixar dois códigos vigentes para a mesma empresa (ESTC e
    # YDUQ); vale o que negociou. Ambiguidade restante fica de fora.
    negociados = set(precos['ticker'].astype(str).str.upper().str[:4])
    prefixos = prefixos[prefixos['prefixo'].isin(negociados)]
    capital = qtd.merge(prefixos, on='cnpj', how='inner')
    capital = capital[~capital['prefixo'].duplicated(keep=False) &
                      ~capital['cnpj'].duplicated(keep=False)]
    capital = capital.assign(data_aprovacao=capital['dt_receb'], elegivel_pit=True,
                             fonte=fonte, sha256='',
                             capturado_em_utc=pd.NaT)
    return capitalizacao_b3(precos, master, anos, sinal, instante_decisao, capital=capital)


def completar_master_b3(master: pd.DataFrame, precos: pd.DataFrame, sinal,
                        consulta, liquidez_minima: float = 500_000):
    """Liga à B3 o ticker líquido sem CNPJ vigente no FCA da CVM.

    O FCA tem erros das próprias empresas: CSNA3 declarada como "4030", C&A e
    Raízen com fim de negociação em ações que seguem negociadas, MBRF3 e
    CMIN3 sem código. `consulta(ticker)` é o cadastro de empresas da B3.
    """
    sinal = pd.Timestamp(sinal)
    m = master.copy()
    for col in ('inicio', 'fim'):
        m[col] = pd.to_datetime(m[col], errors='coerce')
    vigente = m[(m['inicio'].isna() | m['inicio'].le(sinal)) &
                (m['fim'].isna() | m['fim'].ge(sinal))]
    liquidos = precos.loc[pd.to_numeric(precos['volume'], errors='coerce')
                          .gt(liquidez_minima), 'ticker']
    linhas = []
    for ticker in sorted(set(liquidos) - set(vigente['ticker'])):
        # A B3 omite o zero à esquerda (CSN Mineração: 8902291000115).
        c = str((consulta(ticker) or {}).get('cnpj') or '').strip()
        c = c.zfill(14) if c.isdigit() else ''
        if len(c) == 14:
            linhas.append({'cnpj': f'{c[:2]}.{c[2:5]}.{c[5:8]}/{c[8:12]}-{c[12:]}',
                           'ticker': ticker, 'inicio': pd.NaT, 'fim': pd.NaT})
    if not linhas:
        return m, []
    return pd.concat([m, pd.DataFrame(linhas)], ignore_index=True), [x['ticker'] for x in linhas]


def excluir_setores(master: pd.DataFrame, cadastro: pd.DataFrame):
    """Tira do security master os emissores de SETORES_EXCLUIDOS na CVM."""
    fora = set(cadastro.loc[cadastro['setor'].isin(SETORES_EXCLUIDOS), 'cnpj'])
    excluido = master['cnpj'].isin(fora)
    return master[~excluido].copy(), sorted(master.loc[excluido, 'ticker'].unique())


def relatorio_da_decisao(resultado: dict):
    """PDF ao lado do registro (agenda-...pdf), commitado junto pela rotina diária.

    Só para seleção validada. Falha no PDF não bloqueia a decisão: fica no log.
    """
    if resultado.get('status') != 'selecao_tecnica_validada_para_treino':
        return None
    try:
        from carteira.relatorio_carteira import exportar_universo, gerar
        registro = Path(resultado['arquivo'])
        pdf = str(gerar(registro, registro.with_suffix('.pdf')))
        # Universo ranqueado para os gráficos do painel (dispersão, perfil, setores).
        exportar_universo(registro, registro.with_name(f'{registro.stem}-universo.json'))
        return pdf
    except Exception as exc:
        return f'falhou: {type(exc).__name__}: {exc}'


PAINEL_DIARIO = Path(__file__).resolve().parents[1] / 'artifacts' / 'painel_diario'


def _fixar_fontes():
    from carteira import cotahist
    from fontes import cvm
    from fontes.retencao import fixar_versoes_em_uso
    return fixar_versoes_em_uso([cotahist.CACHE, cvm.CACHE])


def _ultimo_pregao(instante) -> dt.date:
    from carteira import cotahist
    fim = pd.Timestamp(instante).tz_localize(None).normalize()
    inicio = fim - pd.Timedelta(days=15)
    partes = [cotahist.parsear_periodo(cotahist.baixar(ano), inicio, fim)
              for ano in range(inicio.year, fim.year + 1)]
    return pd.to_datetime(pd.concat(partes)['data']).max().date()


def registrar_painel_diario(pasta, instante=None, processar=None, pregao=None) -> dict:
    """Indicadores de todo o universo no último pregão: o dataset próprio.

    Um parquet por pregão, com fatores e origem de cada número, e um JSON com
    cobertura e regras aplicadas. A quantidade de ações da B3 só existe como
    foto do dia, então o histórico só se forma gravando todo dia. Rodar de
    novo no mesmo pregão não duplica; bloqueio fica registrado e é retentado.
    """
    pasta = Path(pasta)
    pasta.mkdir(parents=True, exist_ok=True)
    instante = pd.Timestamp(instante or pd.Timestamp.now(tz='America/Sao_Paulo'))
    pregao = pregao or _ultimo_pregao(instante)
    destino = pasta / f'painel-{pregao}.parquet'
    if destino.exists():
        return {'status': 'ja_registrado', 'arquivo': str(destino)}
    anos = list(range(instante.year - 2, instante.year + 1))
    meta = {'tipo': 'painel_diario', 'pregao': str(pregao),
            'capturado_em': str(instante), 'status': 'bloqueado'}
    try:
        painel, alvo = (processar or gerar_selecao_atual)(instante, anos)
        sinal = pd.Timestamp(painel['data'].iat[0]).date()
        if sinal != pregao:
            raise ValueError(f'painel do pregão {sinal}, esperado {pregao}')
        conteudo = painel.to_parquet(index=False)
        temporario = destino.with_suffix('.tmp')
        temporario.write_bytes(conteudo)
        os.replace(temporario, destino)
        meta.update({'status': 'registrado', 'acoes': len(painel),
                     'sha256_painel': hashlib.sha256(conteudo).hexdigest(),
                     'carteira_do_dia': alvo['ticker'].tolist(),
                     **{k: painel.attrs.get(k) for k in (
                         'cobertura_fatores', 'versao_metodologia', 'excluidos_por_setor',
                         'tickers_mapeados_pela_b3', 'trocas_ticker', 'quarentena_capital',
                         'precificacao_capital', 'eventos_sha256_por_emissor')}})
    except Exception as exc:
        meta['erro'] = f'{type(exc).__name__}: {exc}'
    (pasta / f'painel-{pregao}.json').write_text(
        json.dumps(meta, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    return {'arquivo': str(destino) if meta['status'] == 'registrado' else None, **{
        k: meta[k] for k in ('status', 'pregao') }, 'erro': meta.get('erro')}


def _empresa_b3():
    from fontes import b3
    return b3.empresa


def gerar_selecao_atual(as_of: dt.date | str | None, anos: list[int],
                       capital_provider=None, referencia: dt.date | str | None = None,
                       preco_provider=None, fundamento_provider=None,
                       master_provider=None, setor_provider=None, b3_consulta=None):
    """Executa as fontes, calcula fatores e devolve painel e alvo para exame.

    `capital_provider` recebe `(precos, master, anos, data_sinal, decisao)`.
    A implementação padrão usa snapshot B3 capturado até o instante decisório.
    """
    iniciada_em = pd.Timestamp.now(tz='America/Sao_Paulo')
    decisao = pd.Timestamp(as_of or iniciada_em)
    if decisao.tzinfo is None:
        # Uma data de hoje significa uma decisão agora; para datas antigas,
        # exige-se o snapshot arquivado até o instante fornecido.
        if decisao.date() == dt.date.today() and decisao.time() == dt.time():
            decisao = pd.Timestamp.now(tz='America/Sao_Paulo')
        else:
            decisao = decisao.tz_localize('America/Sao_Paulo')
    if capital_provider is None:
        capital_provider = capitalizacao_b3
    if preco_provider is None:
        from carteira.painel_precos import painel_precos
        preco_provider = painel_precos
    if fundamento_provider is None:
        from fontes.fundamentos import fundamentos_cvm
        fundamento_provider = fundamentos_cvm
    if master_provider is None:
        from fontes.cvm import security_master
        master_provider = security_master
    if setor_provider is None:
        from fontes.cvm import cadastro
        setor_provider = cadastro

    precos = preco_provider(decisao.date())
    hashes_eventos = precos.attrs.get('eventos_sha256_por_emissor', {})
    if precos.empty:
        raise ValueError('precos vazios')
    sinal = pd.to_datetime(precos['data'], errors='coerce').dt.date.max()
    if pd.isna(sinal):
        raise ValueError('precos sem data valida')
    master, mapeados_b3 = completar_master_b3(master_provider(anos), precos, sinal,
                                             b3_consulta or _empresa_b3())
    master, excluidos = excluir_setores(master, setor_provider())
    fundamentos = fundamento_provider(anos, sinal)
    capitalizacao = capital_provider(precos, master, anos, sinal, decisao)
    painel = montar_painel_atual(precos, fundamentos, master, capitalizacao,
                                 as_of=decisao.date())
    painel.attrs['eventos_sha256_por_emissor'] = hashes_eventos
    painel.attrs['excluidos_por_setor'] = excluidos
    painel.attrs['tickers_mapeados_pela_b3'] = mapeados_b3
    painel.attrs['trocas_ticker'] = precos.attrs.get('trocas_ticker', [])
    painel.attrs['iniciada_em'] = str(iniciada_em)
    painel.attrs['concluida_em'] = str(pd.Timestamp.now(tz='America/Sao_Paulo'))
    carteira = CarteiraSistematica(painel_atual=painel)
    validar_painel_atual(painel, carteira.indicadores,
                        carteira.liquidez, carteira.n_ativos,
                        referencia=referencia or decisao.date())
    return painel, carteira.carteira_alvo(referencia=referencia or decisao.date())


def rodar_se_devido(instante_decisao, sessoes_observadas, anos: list[int],
                   pasta_relatorios, reprocessar=False,
                   processar=gerar_selecao_atual,
                   cotahist_sha256=None, fixar_fontes=None) -> dict:
    """Tenta o sinal somente no primeiro pregao >= dia 5; execução é D+1.

    O marcador mensal torna uma execução automatizada idempotente. A função
    grava diagnóstico ou erro, sem alterar o ledger de posições.
    """
    instante = pd.Timestamp(instante_decisao)
    from carteira.agenda import mandato_mensal
    regra = mandato_mensal()
    if instante.tzinfo is None:
        instante = instante.tz_localize('America/Sao_Paulo')
    hoje = instante.date()
    sessoes = sorted({pd.Timestamp(d).date() for d in sessoes_observadas
                      if pd.Timestamp(d).date() <= hoje})
    if not sessoes:
        return {'status': 'aguardando_calendario_b3', 'data_decisao': str(hoje)}
    primeiro = min((d for d in sessoes if d.year == hoje.year and
                    d.month == hoje.month and d.day >= int(regra['dia_decisao'])),
                   default=None)
    if primeiro is None:
        return {'status': 'nao_devido', 'data_decisao': str(hoje),
                'primeiro_pregao_apos_dia_5': None}
    inicio_mandato = pd.Timestamp(regra['inicio_mandato']).date()
    if primeiro < inicio_mandato:
        return {'status': 'nao_devido_antes_do_mandato',
                'data_decisao': str(hoje),
                'primeiro_pregao_apos_dia_5': str(primeiro),
                'inicio_mandato': str(inicio_mandato)}
    if primeiro > hoje:
        return {'status': 'nao_devido', 'data_decisao': str(hoje),
                'primeiro_pregao_apos_dia_5': str(primeiro)}

    pasta = Path(pasta_relatorios)
    pasta.mkdir(parents=True, exist_ok=True)
    anteriores = sorted(pasta.glob(f'agenda-{hoje:%Y-%m}-*.json'))
    if anteriores and not reprocessar:
        ultimo = json.loads(anteriores[-1].read_text(encoding='utf-8'))
        if (ultimo.get('status') == 'selecao_tecnica_validada_para_treino' or
                ultimo.get('data_decisao') == str(hoje)):
            return {'status': 'ja_processado', 'arquivo': str(anteriores[-1]),
                    'data_decisao': str(hoje)}
    iniciada_em = pd.Timestamp.now(tz='America/Sao_Paulo')
    relatorio = {'tipo': 'agenda_dia_5', 'modelo': 'TRIO_ROIC',
                 'mandato': regra['id'],
                 'data_giro_planejada': str(primeiro),
                 'data_decisao': str(hoje), 'corte_consulta': str(instante),
                 'iniciada_em': str(iniciada_em),
                 'aporte_ficticio': float(regra['aporte_ficticio_brl']),
                 'execucao': 'pendente de D+1 observado',
                 'status': 'bloqueado'}
    painel = alvo = None
    try:
        painel, alvo = processar(instante, anos)
        relatorio.update({'status': 'selecao_tecnica_validada_para_treino',
                          'data_sinal': str(alvo['data'].iat[0]),
                          'cobertura': painel.attrs.get('cobertura_fatores'),
                          'versao_metodologia': painel.attrs.get('versao_metodologia'),
                          'excluidos_por_setor': painel.attrs.get('excluidos_por_setor'),
                          'tickers_mapeados_pela_b3': painel.attrs.get('tickers_mapeados_pela_b3'),
                          'trocas_ticker': painel.attrs.get('trocas_ticker'),
                          'quarentena_capital': painel.attrs.get('quarentena_capital'),
                          'precificacao_capital': painel.attrs.get('precificacao_capital'),
                          'tickers': alvo['ticker'].tolist(),
                          'cotahist_sha256': cotahist_sha256,
                          'capital_sha256': sorted(painel['hash_capital'].dropna().unique().tolist())
                          if 'hash_capital' in painel else [],
                          'eventos_sha256_por_emissor': painel.attrs.get(
                              'eventos_sha256_por_emissor', {}),
                          'metodologia_sha256': hashlib.sha256(
                              METODOLOGIA.read_bytes()).hexdigest()})
        if fixar_fontes is not None:
            # Versões das fontes em uso nesta decisão: a retenção não as descarta.
            relatorio['versoes_fontes_fixadas'] = fixar_fontes()
    except Exception as exc:
        relatorio['erro'] = f'{type(exc).__name__}: {exc}'
        if isinstance(exc, CoberturaInsuficiente):
            relatorio['cobertura'] = exc.diagnostico
    concluida_em = pd.Timestamp.now(tz='America/Sao_Paulo')
    relatorio['concluida_em'] = str(concluida_em)
    relatorio['instante_decisao'] = str(concluida_em)
    pendente = None
    if painel is not None:
        pendente = alvo[['ticker', 'peso']].copy()
        pendente.insert(0, 'data_decisao', hoje)
        pendente['data_execucao'] = None
        csv_alvo = pendente.to_csv(index=False).encode('utf-8')
        relatorio['alvo_sha256'] = hashlib.sha256(csv_alvo).hexdigest()
    bruto = json.dumps(relatorio, sort_keys=True, ensure_ascii=False,
                       default=str, indent=2).encode('utf-8')
    digest = hashlib.sha256(bruto).hexdigest()[:16]
    nome = f'agenda-{hoje:%Y-%m}-{instante:%Y%m%dT%H%M%S}-{digest}'
    arquivo = pasta / f'{nome}.json'
    if painel is not None:
        painel.to_parquet(pasta / f'{nome}.parquet', index=False)
        alvo.to_csv(pasta / f'{nome}-carteira.csv', index=False)
        (pasta / f'{nome}-alvo_pendente.csv').write_bytes(csv_alvo)
    with arquivo.open('xb') as saida:
        saida.write(bruto)
    return {'arquivo': str(arquivo), **relatorio}


def _cli():
    """Gera artefato imutavel de auditoria; jamais altera carteiras.parquet."""
    import argparse

    parser = argparse.ArgumentParser(description='Sinal atual TRIO_ROIC de treino')
    parser.add_argument('--as-of', default=None,
                        help='instante da decisão; padrão: agora em São Paulo')
    parser.add_argument('--saida', default=str(Path(__file__).resolve().parents[1] /
                                                 'artifacts' / 'selecao_atual'))
    parser.add_argument('--somente-agenda', action='store_true',
                        help='roda somente no primeiro pregão B3 a partir do dia 5')
    parser.add_argument('--reprocessar', action='store_true',
                        help='gera nova versão do diagnóstico mensal')
    parser.add_argument('--painel-diario', action='store_true',
                        help='grava os indicadores do último pregão (dataset próprio)')
    argumentos = parser.parse_args()
    if argumentos.painel_diario:
        resultado = registrar_painel_diario(PAINEL_DIARIO)
        print(json.dumps(resultado, ensure_ascii=False, default=str, indent=2))
        return 2 if resultado['status'] == 'bloqueado' else 0
    agora = pd.Timestamp.now(tz='America/Sao_Paulo')
    decisao = pd.Timestamp(argumentos.as_of) if argumentos.as_of else agora
    if decisao.tzinfo is None:
        if decisao.date() == agora.date() and decisao.time() == dt.time():
            decisao = agora
        else:
            decisao = decisao.tz_localize('America/Sao_Paulo')
    anos = list(range(decisao.year - 2, decisao.year + 1))
    if argumentos.somente_agenda:
        try:
            from carteira import cotahist
            inicio_mes = decisao.replace(day=1).date()
            # Revalida sempre: com agendamento diario o TTL de 24h reusaria
            # o arquivo da vespera por segundos de diferenca.
            arquivo_cotahist = cotahist.baixar(decisao.year, atualizar=True)
            cotacoes_mes = cotahist.parsear_periodo(
                arquivo_cotahist, inicio_mes, decisao.date())
            resultado = rodar_se_devido(
                decisao, cotacoes_mes['data'].unique(), anos, argumentos.saida,
                reprocessar=argumentos.reprocessar,
                cotahist_sha256=hashlib.sha256(
                    Path(arquivo_cotahist).read_bytes()).hexdigest(),
                fixar_fontes=_fixar_fontes)
            resultado['relatorio_pdf'] = relatorio_da_decisao(resultado)
        except Exception as exc:
            pasta = Path(argumentos.saida)
            pasta.mkdir(parents=True, exist_ok=True)
            resultado = {'status': 'bloqueado', 'tipo': 'agenda_dia_5',
                         'data_decisao': str(decisao.date()),
                         'erro': f'{type(exc).__name__}: {exc}',
                         'instante_decisao': str(pd.Timestamp.now(
                             tz='America/Sao_Paulo'))}
            bruto = json.dumps(resultado, sort_keys=True, ensure_ascii=False,
                               indent=2).encode('utf-8')
            digest = hashlib.sha256(bruto).hexdigest()[:16]
            caminho = pasta / f'agenda-{decisao:%Y-%m}-{decisao:%Y%m%dT%H%M%S}-{digest}.json'
            caminho.write_bytes(bruto)
            resultado['arquivo'] = str(caminho)
        if list(Path(argumentos.saida).glob('agenda-*-alvo_pendente.csv')):
            from carteira.execucao_mensal import atualizar_fundo_mensal
            fundo = atualizar_fundo_mensal(argumentos.saida, as_of=decisao)
            resultado['fundo'] = fundo
        try:
            from carteira.resumo_fundo import exportar_resumo
            resultado['resumo_app'] = exportar_resumo(
                argumentos.saida, Path(argumentos.saida).parent / 'fundo-ficticio.json')
        except Exception as exc:
            resultado['resumo_app'] = f'falhou: {type(exc).__name__}: {exc}'
        print(json.dumps(resultado, ensure_ascii=False, default=str, indent=2))
        return (2 if resultado['status'] == 'bloqueado' or
                resultado.get('fundo', {}).get('status') == 'nav_bloqueado'
                else 0)
    iniciada_em = pd.Timestamp.now(tz='America/Sao_Paulo')
    relatorio = {'modelo': 'TRIO_ROIC', 'corte_consulta': str(decisao),
                 'iniciada_em': str(iniciada_em),
                 'anos_cvm': anos, 'tipo': 'simulacao_educacional',
                 'status': 'bloqueado'}
    painel = None
    try:
        painel, alvo = gerar_selecao_atual(decisao, anos)
        relatorio.update({'status': 'selecao_tecnica_validada_para_treino',
                          'data_sinal': str(alvo['data'].iat[0]),
                          'cobertura': painel.attrs.get('cobertura_fatores'),
                          'versao_metodologia': painel.attrs.get('versao_metodologia'),
                          'quarentena_capital': painel.attrs.get('quarentena_capital'),
                          'precificacao_capital': painel.attrs.get('precificacao_capital'),
                          'excluidos_por_setor': painel.attrs.get('excluidos_por_setor'),
                          'tickers_mapeados_pela_b3': painel.attrs.get('tickers_mapeados_pela_b3'),
                          'trocas_ticker': painel.attrs.get('trocas_ticker'),
                          'tickers': alvo['ticker'].tolist(),
                          'ebit_negativo_no_top': painel.loc[
                              painel['ticker'].isin(alvo['ticker']) &
                              painel['ebit_ttm'].lt(0), 'ticker'].tolist(),
                          'pesos': alvo['peso'].tolist()})
    except Exception as exc:
        relatorio['erro'] = f'{type(exc).__name__}: {exc}'
        if isinstance(exc, CoberturaInsuficiente):
            relatorio['cobertura'] = exc.diagnostico
    concluida_em = pd.Timestamp.now(tz='America/Sao_Paulo')
    relatorio['concluida_em'] = str(concluida_em)
    relatorio['instante_decisao'] = str(concluida_em)
    destino = Path(argumentos.saida)
    destino.mkdir(parents=True, exist_ok=True)
    bruto = json.dumps(relatorio, sort_keys=True, ensure_ascii=False,
                       default=str, indent=2).encode('utf-8')
    digest = hashlib.sha256(bruto).hexdigest()[:16]
    nome = f"{decisao.strftime('%Y%m%dT%H%M%S')}-{digest}"
    caminho = destino / f'{nome}.json'
    caminho.write_bytes(bruto)
    if painel is not None:
        painel.to_parquet(destino / f'{nome}.parquet', index=False)
        alvo.to_csv(destino / f'{nome}-carteira.csv', index=False)
    resumo = {'arquivo': str(caminho), 'status': relatorio['status'],
              'data_sinal': relatorio.get('data_sinal'),
              'cobertura': relatorio.get('cobertura'),
              'tickers': relatorio.get('tickers'),
              'ebit_negativo_no_top': relatorio.get('ebit_negativo_no_top'),
              'erro': relatorio.get('erro')}
    print(json.dumps(resumo, ensure_ascii=False, default=str, indent=2))
    return 0 if painel is not None else 2


if __name__ == '__main__':
    raise SystemExit(_cli())
