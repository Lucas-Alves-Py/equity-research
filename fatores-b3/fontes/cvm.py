"""Fundamentos direto da CVM -- sem fornecedor no meio.

Por que existir
---------------
A base em lote da Fintz parou em 2025-11-03 e o modelo nao consegue mais
ranquear. Mas a Fintz nao PRODUZ fundamento: ela revende o que as empresas
entregam a CVM. Aqui lemos a fonte.

Duas coisas que so a fonte tem
------------------------------
1. **DT_RECEB** -- a data em que o documento chegou a CVM. E o carimbo de
   quando o mercado PODIA saber. Com ele o point-in-time deixa de ser promessa
   de fornecedor e vira coisa verificavel: nenhum backtest usa um balanco antes
   da data em que ele foi protocolado.

2. **VERSAO** -- republicacao explicita. A empresa reapresenta o balanco e o
   numero final contradiz o que se sabia na epoca. Guardar as duas versoes e o
   que impede o backtest de enxergar a correcao antes de ela existir.

Plano de contas
---------------
Verificado contra os arquivos, nao suposto:

    3.05     Resultado Antes do Resultado Financeiro e dos Tributos  = EBIT
    1        Ativo Total
    1.01.01  Caixa e Equivalentes de Caixa
    1.01.02  Aplicacoes Financeiras
    2.01.04  Emprestimos e Financiamentos (circulante)
    2.02.01  Emprestimos e Financiamentos (nao circulante)
    2.03     Patrimonio Liquido Consolidado

LIMITE: esse plano vale para empresa NAO financeira. Banco usa outro -- nele
3.01 e "Receitas de Intermediacao Financeira" e 3.05 nao significa EBIT.
Financeiras precisam sair do universo, o que ja era pendencia do projeto e que
o cadastro da CVM (campo SETOR_ATIV) finalmente permite fazer.

Escala: VL_CONTA pode vir em MIL ou UNIDADE. A conversao usa ESCALA_MOEDA
linha a linha; multiplicar tudo por 1.000 distorce emissores em UNIDADE.
"""

from __future__ import annotations

import os
import hashlib
import json
import tempfile
import warnings
from datetime import datetime, timezone
import zipfile
from pathlib import Path

import pandas as pd
import requests

BASE = "https://dados.cvm.gov.br/dados/CIA_ABERTA"
AQUI = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(os.path.dirname(AQUI), 'dados', 'cvm')

EBIT = '3.05'
LUCRO_ANTES_TRIBUTOS = '3.07'
IMPOSTO_RENDA_CSLL = '3.08'
ATIVO_TOTAL = '1'
CAIXA = ['1.01.01', '1.01.02']
DIVIDA = ['2.01.04', '2.02.01']
PATRIMONIO = '2.03'

CORRENTE = 'ÚLTIMO'   # ORDEM_EXERC: exercicio corrente (vs PENULTIMO)


# --------------------------------------------------------------------------
# download com cache
# --------------------------------------------------------------------------

def _baixar_com_manifesto(url: str, destino: str, validade_segundos: int,
                          atualizar: bool = False) -> str:
    """Busca um arquivo com cache condicional e guarda snapshots por hash."""
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    meta_path = destino.with_suffix(destino.suffix + '.meta.json')
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            meta = {}

    atual = destino.parent / meta.get('arquivo_atual', '') if meta.get('arquivo_atual') else None
    if atual is not None and not atual.exists():
        atual = None
    if not atualizar and atual and meta.get('verificado_em_utc'):
        try:
            verificado = datetime.fromisoformat(meta['verificado_em_utc'])
            idade = (datetime.now(timezone.utc) - verificado).total_seconds()
            if 0 <= idade < validade_segundos:
                return str(atual)
        except (ValueError, TypeError):
            pass

    headers = {}
    if meta.get('etag'):
        headers['If-None-Match'] = meta['etag']
    if meta.get('last_modified'):
        headers['If-Modified-Since'] = meta['last_modified']
    resposta = requests.get(url, headers=headers, timeout=300)
    agora = datetime.now(timezone.utc).isoformat()
    if resposta.status_code == 304 and atual is not None:
        meta['verificado_em_utc'] = agora
        meta['http_status_ultima_verificacao'] = 304
        tmp_meta = meta_path.with_suffix(meta_path.suffix + '.tmp')
        tmp_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
        os.replace(tmp_meta, meta_path)
        return str(atual)
    resposta.raise_for_status()
    conteudo = resposta.content
    if not conteudo:
        raise RuntimeError(f"arquivo CVM vazio: {url}")

    digest = hashlib.sha256(conteudo).hexdigest()
    versoes = destino.parent / 'versoes'
    versoes.mkdir(parents=True, exist_ok=True)
    nome_versao = f"{destino.stem}.{digest[:16]}{destino.suffix}"
    arquivo_versao = versoes / nome_versao
    if not arquivo_versao.exists():
        with tempfile.NamedTemporaryFile(dir=versoes, suffix=destino.suffix,
                                         delete=False) as tmp:
            temporario = Path(tmp.name)
        try:
            temporario.write_bytes(conteudo)
            os.replace(temporario, arquivo_versao)
        finally:
            temporario.unlink(missing_ok=True)

    meta_atual = {
        'fonte': 'CVM dados abertos',
        'url': url,
        'verificado_em_utc': agora,
        'capturado_em_utc': agora,
        'http_status_ultima_verificacao': resposta.status_code,
        'etag': resposta.headers.get('ETag'),
        'last_modified': resposta.headers.get('Last-Modified'),
        'bytes': len(conteudo),
        'sha256': digest,
        'arquivo_atual': str(arquivo_versao.relative_to(destino.parent)),
    }
    tmp_meta = meta_path.with_suffix(meta_path.suffix + '.tmp')
    tmp_meta.write_text(json.dumps(meta_atual, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp_meta, meta_path)
    if atual is not None:
        from fontes.retencao import aposentar
        aposentar(atual, arquivo_versao)
    return str(arquivo_versao)


def baixar(doc: str, ano: int, atualizar: bool = False) -> str:
    """Arquivo anual CVM; o ano corrente atualiza diariamente por padrão.

    Cada conteúdo novo fica preservado em `dados/cvm/versoes/` por hash. Para
    anos fechados a conferência é mensal; para o ano anterior, semanal.
    """
    nome = f"{doc.lower()}_cia_aberta_{ano}.zip"
    validade = (24 * 60 * 60 if ano == datetime.now().year else
                7 * 24 * 60 * 60 if ano == datetime.now().year - 1 else
                30 * 24 * 60 * 60)
    url = f"{BASE}/DOC/{doc.upper()}/DADOS/{nome}"
    return _baixar_com_manifesto(url, os.path.join(CACHE, nome), validade, atualizar)


def tabela(doc: str, ano: int, sufixo: str = '') -> pd.DataFrame:
    """Uma das tabelas de dentro do zip anual."""

    caminho = baixar(doc, ano)
    meio = f"_{sufixo}" if sufixo else ""
    interno = f"{doc.lower()}_cia_aberta{meio}_{ano}.csv"
    with zipfile.ZipFile(caminho) as z, z.open(interno) as f:
        return pd.read_csv(f, sep=';', encoding='latin-1', low_memory=False)


# --------------------------------------------------------------------------
# security master -- CNPJ para ticker, com validade
# --------------------------------------------------------------------------

def security_master(anos) -> pd.DataFrame:
    """CNPJ -> codigo de negociacao, com janela de validade.

    Uniao de varios anos de proposito: cada empresa declara o FCA uma vez por
    ano e nem toda empresa aparece em todo ano. Medimos: com o FCA de 2026
    sozinho, so 9 dos 10 papeis da carteira apareciam.

    As datas de inicio e fim de negociacao evitam o anacronismo de atribuir um
    ticker a empresa errada depois de uma troca de codigo.
    """

    partes = []
    for ano in anos:
        try:
            partes.append(tabela('FCA', ano, 'valor_mobiliario').assign(ano_fca=ano))
        except Exception:
            continue

    vm = pd.concat(partes, ignore_index=True)
    vm = vm[vm['Codigo_Negociacao'].notna()].copy()
    vm['ticker'] = vm['Codigo_Negociacao'].astype(str).str.strip().str.upper()
    # Prefixo pode ter dígito (B3SA3); o filtro antigo [A-Z]{4} descartava a B3.
    vm = vm[vm['ticker'].str.fullmatch(r'[A-Z][A-Z0-9]{3}\d{1,2}')]

    d = vm.rename(columns={'CNPJ_Companhia': 'cnpj', 'Nome_Empresarial': 'nome',
                           'Data_Inicio_Negociacao': 'inicio',
                           'Data_Fim_Negociacao': 'fim'})
    for c in ('inicio', 'fim'):
        d[c] = pd.to_datetime(d[c], errors='coerce')

    # Uma janela aberta é NaT. `max(fim)` ignoraria NaT e transformaria um
    # ticker vigente em encerrado quando existe um registro histórico fechado.
    # A situação mais recente é a do FCA mais recente, com ano exposto para
    # auditoria; o início da negociação pode estar em um FCA anterior.
    d = d.sort_values(['cnpj', 'ticker', 'ano_fca', 'inicio'])
    chaves = ['cnpj', 'ticker']
    ultimo_ano = d.groupby(chaves)['ano_fca'].transform('max')
    vigentes = d[d['ano_fca'].eq(ultimo_ano)].copy()
    fim_recente = (vigentes.groupby(chaves)['fim']
                   .agg(lambda x: pd.NaT if x.isna().any() else x.max())
                   .rename('fim'))
    dados = d.groupby(chaves, as_index=True).agg(
        nome=('nome', 'last'), inicio=('inicio', 'min'), ano_fca=('ano_fca', 'max'))
    return dados.join(fim_recente).reset_index()


def cadastro(atualizar: bool = False) -> pd.DataFrame:
    """Cadastro das companhias -- traz o SETOR, que a Fintz nao expunha."""

    destino = os.path.join(CACHE, 'cad_cia_aberta.csv')
    caminho = _baixar_com_manifesto(
        f"{BASE}/CAD/DADOS/cad_cia_aberta.csv", destino,
        validade_segundos=30 * 24 * 60 * 60, atualizar=atualizar)
    d = pd.read_csv(caminho, sep=';', encoding='latin-1', low_memory=False)
    return (d.rename(columns={'CNPJ_CIA': 'cnpj', 'SETOR_ATIV': 'setor',
                              'SIT': 'situacao', 'DENOM_SOCIAL': 'nome'})
             [['cnpj', 'nome', 'setor', 'situacao']].drop_duplicates('cnpj'))


# --------------------------------------------------------------------------
# demonstracoes
# --------------------------------------------------------------------------

def protocolo(anos, strict: bool = False) -> pd.DataFrame:
    """DT_RECEB por (empresa, trimestre, versao) -- quando o mercado soube."""

    partes = []
    for doc in ('ITR', 'DFP'):
        for ano in anos:
            try:
                d = tabela(doc, ano)
            except Exception:
                if strict:
                    raise
                continue
            d = d.rename(columns={'CNPJ_CIA': 'cnpj', 'DT_REFER': 'dt_refer',
                                  'DT_RECEB': 'dt_receb', 'VERSAO': 'versao'})
            partes.append(d[['cnpj', 'dt_refer', 'versao', 'dt_receb']].assign(doc=doc))

    d = pd.concat(partes, ignore_index=True)
    for c in ('dt_refer', 'dt_receb'):
        d[c] = pd.to_datetime(d[c], errors='coerce')
    return d.drop_duplicates()


def _contas(doc: str, ano: int, tab: str, codigos,
            as_of: str | pd.Timestamp | None = None) -> pd.DataFrame:
    """Linhas das contas escolhidas: consolidado, exercicio corrente.

    Cai para o individual quando a empresa nao publica consolidado. Nao e caso
    raro: a ASAI3 (Sendas) so tem DRE_ind, e sem esse fallback ela sumia do
    universo inteiro em silencio -- o tipo de buraco que nao levanta erro.
    Preferencia sempre do consolidado; o individual so cobre quem falta.
    """

    # DT_RECEB nao existe nos CSVs das contas: deve ser anexado pelo manifesto
    # de documentos da CVM. Com `as_of`, selecionamos a ultima versao recebida
    # antes da data de corte ANTES de filtrar contas. Como a CVM so fornece a
    # data (sem hora), a desigualdade estrita evita assumir que uma entrega no
    # proprio dia ja estava disponivel no fechamento.
    protocolo_doc = None
    if as_of is not None:
        corte = pd.Timestamp(as_of).normalize()
        protocolo_doc = protocolo([ano], strict=True)
        protocolo_doc = protocolo_doc[protocolo_doc['doc'].eq(doc.upper())]
        protocolo_doc = protocolo_doc[protocolo_doc['dt_receb'] < corte]
        protocolo_doc = protocolo_doc.dropna(subset=['cnpj', 'dt_refer', 'versao', 'dt_receb'])
        protocolo_doc['versao'] = protocolo_doc['versao'].astype(str)
        protocolo_doc['_versao_ordem'] = pd.to_numeric(protocolo_doc['versao'], errors='coerce')
        protocolo_doc = (protocolo_doc
            .sort_values(['dt_receb', '_versao_ordem', 'versao'])
            .drop_duplicates(['cnpj', 'dt_refer', 'doc'], keep='last'))
        protocolo_doc = protocolo_doc.drop(columns='_versao_ordem')

    def ler_lado(sufixo):
        x = tabela(doc, ano, f'{tab}_{sufixo}')
        x = x[x['ORDEM_EXERC'].eq(CORRENTE)].copy()
        # Alguns arquivos abertos da CVM repetem linhas idênticas. Removemos
        # só duplicatas byte a byte; duas observações diferentes no mesmo
        # grão contábil são ambiguidade, não algo para resolver com `last`.
        x = x.drop_duplicates()
        x = x.rename(columns={'CNPJ_CIA': 'cnpj', 'DT_REFER': 'dt_refer',
                              'VERSAO': 'versao'})
        x['cnpj'] = x['cnpj'].astype(str).str.strip()
        x['dt_refer'] = pd.to_datetime(x['dt_refer'], errors='coerce')
        x['versao'] = x['versao'].astype(str)
        if protocolo_doc is not None:
            x = x.merge(protocolo_doc[['cnpj', 'dt_refer', 'versao', 'dt_receb']],
                        on=['cnpj', 'dt_refer', 'versao'], how='inner',
                        validate='many_to_one')
        if sufixo == 'con':
            # Emissor sem controladas pode entregar o consolidado com todas
            # as contas zeradas (TIMS3 em 2024, GEPA3/4): tratar como ausente
            # para o individual cobrir. Do contrario o EBIT TTM sai zerado.
            soma = (pd.to_numeric(x['VL_CONTA'], errors='coerce').abs()
                    .groupby([x['cnpj'], x['dt_refer'], x['versao']])
                    .transform('sum'))
            x = x[soma.ne(0)].copy()
        declaracoes = pd.MultiIndex.from_frame(
            x[['cnpj', 'dt_refer', 'versao']].drop_duplicates())
        x = x[x['CD_CONTA'].isin(codigos)].copy()
        grao = ['cnpj', 'dt_refer', 'versao', 'CD_CONTA']
        if tab == 'DRE':
            grao += ['DT_INI_EXERC', 'DT_FIM_EXERC']
        ambiguas = x.duplicated(grao, keep=False)
        if ambiguas.any():
            exemplo = x.loc[ambiguas, grao + ['VL_CONTA', 'ESCALA_MOEDA']].head(3)
            warnings.warn(
                f"{ambiguas.sum()} linhas CVM ambíguas excluídas no grão "
                f"{doc}/{ano}/{tab}/{sufixo}: {exemplo.to_dict('records')}",
                RuntimeWarning, stacklevel=2)
            # Não escolher arbitrariamente `last`: uma soma fiscal conflitante
            # contaminaria TTM/ROIC. O emissor fica sem essa janela.
            x = x.loc[~ambiguas].copy()
        return x, declaracoes

    d, con_declaracoes = ler_lado('con')
    ind, _ = ler_lado('ind')
    # A preferência por consolidado é por emissor e período, não por linha de
    # conta. O fallback individual só atende quem não tem essa demonstração.
    # A escolha do lado vale por declaração, não por emissor inteiro: um
    # emissor pode ter consolidado em um trimestre e só individual em outro.
    chave_lado = ['cnpj', 'dt_refer', 'versao']
    ind = ind[~pd.MultiIndex.from_frame(ind[chave_lado]).isin(con_declaracoes)]
    d = pd.concat([d, ind], ignore_index=True)

    d = d.rename(columns={'CD_CONTA': 'conta', 'VL_CONTA': 'valor'})
    fatores_escala = {'UNIDADE': 1.0, 'MIL': 1000.0}
    escala = d['ESCALA_MOEDA'].astype(str).str.strip().str.upper()
    desconhecidas = sorted(set(escala.dropna()) - set(fatores_escala))
    if desconhecidas or escala.isna().any():
        raise ValueError(
            f"ESCALA_MOEDA ausente ou nao suportada em {doc}/{ano}/{tab}: "
            f"{desconhecidas or 'valor ausente'}"
        )
    d['valor'] = (pd.to_numeric(d['valor'], errors='coerce')
                  * escala.map(fatores_escala))

    cols = ['cnpj', 'dt_refer', 'versao', 'conta', 'valor']
    if 'dt_receb' in d.columns:
        cols.append('dt_receb')
    if 'DT_INI_EXERC' in d.columns:
        d['ini'] = pd.to_datetime(d['DT_INI_EXERC'], errors='coerce')
        d['fim'] = pd.to_datetime(d['DT_FIM_EXERC'], errors='coerce')
        cols += ['ini', 'fim']
    return d[cols]


def balanco(anos, as_of: str | pd.Timestamp | None = None) -> pd.DataFrame:
    """Divida bruta, caixa, patrimonio liquido e ativo total por trimestre.

    Balanco e foto: o valor vale para a data de referencia, sem acumulo.
    """

    partes = []
    for doc in ('ITR', 'DFP'):
        for ano in anos:
            try:
                bpa = _contas(doc, ano, 'BPA', [ATIVO_TOTAL] + CAIXA, as_of=as_of)
                bpp = _contas(doc, ano, 'BPP', DIVIDA + [PATRIMONIO], as_of=as_of)
            except Exception:
                if as_of is not None:
                    raise
                continue
            partes.append(pd.concat([bpa, bpp], ignore_index=True).assign(doc=doc))

    chaves = ['cnpj', 'dt_refer', 'versao', 'doc']
    if as_of is not None:
        chaves.append('dt_receb')
    largo = (pd.concat(partes, ignore_index=True)
               .pivot_table(index=chaves,
                            columns='conta', values='valor', aggfunc='last')
               .reset_index())

    def soma(cols):
        presentes = [c for c in cols if c in largo.columns]
        return largo[presentes].sum(axis=1, min_count=1)

    resultado = pd.DataFrame({
        'cnpj': largo['cnpj'],
        'dt_refer': largo['dt_refer'],
        'versao': largo['versao'],
        'doc': largo['doc'],
        **({'dt_receb': largo['dt_receb']} if 'dt_receb' in largo else {}),
        'ativo_total': largo.get(ATIVO_TOTAL),
        'caixa': soma(CAIXA),
        'divida_bruta': soma(DIVIDA),
        'patrimonio_liquido': largo.get(PATRIMONIO),
    })
    if as_of is not None:
        # ITR e DFP podem retratar a mesma data-base. Usa a última declaração
        # recebida antes do corte e mantém a sua proveniência.
        resultado = (resultado.sort_values(['dt_receb', 'versao', 'doc'])
                     .drop_duplicates(['cnpj', 'dt_refer'], keep='last'))
    return resultado.reset_index(drop=True)


def dre_trimestral(anos, codigo: str, nome: str,
                  as_of: str | pd.Timestamp | None = None) -> pd.DataFrame:
    """Conta DRE isolada por trimestre, com proveniência do documento.

    Prioriza a janela trimestral publicada no ITR. Quando ela falta, subtrai
    acumulados consecutivos do mesmo ano conhecidos no corte. Uma versão nova
    em Q3 não obriga a versão do ITR de Q2 a coincidir; cada data-base possui
    versão e protocolo próprios. Q4 requer DFP anual e ITR de nove meses.
    """
    if codigo not in {EBIT, LUCRO_ANTES_TRIBUTOS, IMPOSTO_RENDA_CSLL}:
        raise ValueError(f'conta DRE não suportada: {codigo}')
    partes = []
    for doc in ('ITR', 'DFP'):
        for ano in anos:
            try:
                partes.append(_contas(doc, ano, 'DRE', [codigo], as_of=as_of).assign(doc=doc))
            except Exception:
                if as_of is not None:
                    raise
                continue
    if not partes:
        raise ValueError(f'nenhum documento DRE encontrado para {codigo}')
    d = pd.concat(partes, ignore_index=True)
    if d.empty:
        return pd.DataFrame(columns=['cnpj', 'dt_refer', 'versao', 'doc',
                                     'dt_receb', nome, 'origem'])
    d = d[d['ini'].notna() & d['fim'].notna() & d['valor'].notna()].copy()
    d['meses'] = ((d['fim'] - d['ini']).dt.days / 30.44).round()
    d['ano'] = d['dt_refer'].dt.year
    d['trimestre'] = d['dt_refer'].dt.quarter
    # Um único valor por janela e data-base. Se as_of foi informado, _contas
    # já filtrou pelo protocolo/versão; a checagem impede seleção silenciosa.
    grao = ['cnpj', 'dt_refer', 'doc', 'ini', 'fim']
    if as_of is not None and d.duplicated(grao).any():
        raise ValueError(f'DRE ambígua no corte {as_of} para {codigo}')
    if as_of is None:
        d = (d.sort_values('versao').drop_duplicates(grao, keep='last'))

    cols = ['cnpj', 'dt_refer', 'versao', 'doc']
    if 'dt_receb' in d.columns:
        cols.append('dt_receb')
    itr = d[d['doc'].eq('ITR')].copy()
    # Janela de 3 meses que termina na data-base, em qualquer calendário
    # fiscal (CAML3 fecha trimestres em maio, agosto, novembro e fevereiro).
    trimestre_publicado = itr[
        itr['meses'].eq(3) & itr['fim'].eq(itr['dt_refer'])
    ].copy()
    trimestre_publicado[nome] = trimestre_publicado['valor']
    trimestre_publicado['origem'] = 'ITR-trimestre-publicado'
    saidas = [trimestre_publicado[cols + [nome, 'origem']]]

    # Acumulados só valem quando começam no primeiro trimestre fiscal. Uma
    # subtração sem Q anterior ficaria numericamente plausível e errada.
    # O exercício é identificado pelo início da janela, não pelo ano civil:
    # SMTO3/JALL3 fecham em março e AGRO3 em junho.
    acumulado = itr[itr['meses'].isin([3, 6, 9])].copy()
    acumulado['q_fiscal'] = (acumulado['meses'] // 3).astype(int)
    derivados = []
    publicados = set(zip(trimestre_publicado['cnpj'],
                         trimestre_publicado['dt_refer']))
    for (cnpj, _), grupo in acumulado.groupby(['cnpj', 'ini']):
        por_q = {int(linha['q_fiscal']): linha
                 for _, linha in grupo.iterrows()}
        for q in (1, 2, 3):
            atual = por_q.get(q)
            if atual is None or (cnpj, atual['dt_refer']) in publicados:
                continue
            anterior = por_q.get(q - 1) if q > 1 else None
            if q > 1 and anterior is None:
                continue
            registro = {c: atual[c] for c in cols}
            registro[nome] = atual['valor'] - (anterior['valor'] if q > 1 else 0)
            registro['origem'] = 'ITR-acumulado-diferenca' if q > 1 else 'ITR-acumulado-Q1'
            # Os dois acumulados devem estar conhecidos; dt_receb do sinal é
            # o máximo dos protocolos envolvidos.
            if q > 1 and 'dt_receb' in cols:
                registro['dt_receb'] = max(atual['dt_receb'], anterior['dt_receb'])
            derivados.append(registro)

    dfp = d[d['doc'].eq('DFP') & d['meses'].eq(12)].copy()
    itr9 = acumulado[acumulado['q_fiscal'].eq(3)].copy()
    if not dfp.empty and not itr9.empty:
        pares = dfp.merge(itr9, on=['cnpj', 'ini'], how='inner',
                          suffixes=('_dfp', '_itr'), validate='one_to_one')
        for _, linha in pares.iterrows():
            registro = {
                'cnpj': linha['cnpj'], 'dt_refer': linha['dt_refer_dfp'],
                'versao': linha['versao_dfp'], 'doc': 'DFP',
                nome: linha['valor_dfp'] - linha['valor_itr'],
                'origem': 'DFP-anual-menos-ITR-9M',
            }
            if 'dt_receb' in cols:
                registro['dt_receb'] = max(linha['dt_receb_dfp'],
                                           linha['dt_receb_itr'])
            derivados.append(registro)
    if derivados:
        saidas.append(pd.DataFrame(derivados))
    saida = pd.concat(saidas, ignore_index=True).drop_duplicates()
    # Alguns emissores entregam também Q4 isolado em ITR. A DFP anual
    # recebida depois é a demonstração anual autoritativa para esse período.
    q4_dfp = set(zip(saida.loc[saida['doc'].eq('DFP'), 'cnpj'],
                     saida.loc[saida['doc'].eq('DFP'), 'dt_refer']))
    itr_q4_substituido = (saida['doc'].eq('ITR') &
                         pd.Series(list(zip(saida['cnpj'], saida['dt_refer'])),
                                   index=saida.index).isin(q4_dfp))
    saida = saida.loc[~itr_q4_substituido].copy()
    ordem = ['cnpj', 'dt_refer', 'dt_receb' if 'dt_receb' in saida else 'versao']
    saida = saida.sort_values(ordem).reset_index(drop=True)
    if as_of is not None and saida.duplicated(['cnpj', 'dt_refer']).any():
        dup = saida[saida.duplicated(['cnpj', 'dt_refer'], keep=False)]
        raise ValueError(f'mais de um trimestre DRE para {codigo} no corte {as_of}: '
                         f"{dup.head(8).to_dict('records')}")
    return saida


def ebit_trimestral(anos, as_of: str | pd.Timestamp | None = None) -> pd.DataFrame:
    """EBIT trimestral conhecido até as_of."""
    return dre_trimestral(anos, EBIT, 'ebit', as_of)


def impostos_trimestral(anos, as_of: str | pd.Timestamp | None = None) -> pd.DataFrame:
    """Imposto de renda e CSLL da DRE (3.08), com sinal publicado pela CVM."""
    return dre_trimestral(anos, IMPOSTO_RENDA_CSLL, 'imposto', as_of)


def lucro_antes_tributos_trimestral(
        anos, as_of: str | pd.Timestamp | None = None) -> pd.DataFrame:
    """Lucro antes de tributos da DRE (3.07), para taxa efetiva."""
    return dre_trimestral(anos, LUCRO_ANTES_TRIBUTOS, 'lair', as_of)
