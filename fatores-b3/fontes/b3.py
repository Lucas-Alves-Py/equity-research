"""Eventos corporativos da B3 -- o que falta para reconstruir preco ajustado.

Por que existir
---------------
O COTAHIST da B3 traz preco BRUTO. Momento precisa de retorno TOTAL, e a
diferenca nao e cosmetica: medimos a correlacao de rank entre momento bruto e
momento ajustado numa janela de 6 meses e deu 0,858 -- **11 dos 31 nomes do
decil superior mudavam**. Um terco do topo do ranking. Com preco bruto o
momento penaliza sistematicamente quem paga dividendo.

Este modulo busca os proventos para reconstruir a serie ajustada.

O que a B3 entrega, e o que nao entrega
---------------------------------------
`GetListedCashDividends` cobre evento em DINHEIRO. A perna de ações usa
`GetListedSupplementCompany`, com último dia com direito, fator, classe ISIN
e data de aprovação. O detector `carteira/eventos.py` permanece como alerta;
ele não corrige preço por inferência.

Uma armadilha medida
--------------------
O `tradingName` NAO e derivavel do ticker. Buscar proventos da JHSF com
tradingName "JHSF" devolve zero eventos; o nome correto so vem do lookup de
empresas. Adivinhar o nome retorna lista vazia -- sem erro, sem aviso. Por isso
`empresa()` e obrigatorio antes de `proventos()`.

Fator de ajuste
---------------
A B3 entrega `corporateActionPrice`, provento bruto como percentual do
fechamento anterior ao ex. Para reproduzir a convencao historica do fator
Fintz, JCP entra no indicador com 85% desse percentual; os outros proventos
entram integralmente. O percentual bruto da B3 permanece preservado.
"""

from __future__ import annotations

import base64
import json
import time
import unicodedata

import pandas as pd
import requests

BASE = "https://sistemaswebb3-listados.b3.com.br/listedCompaniesProxy/CompanyCall"
CABECALHO = {'User-Agent': 'Mozilla/5.0', 'Accept': 'application/json'}

# classe da acao pelo sufixo do ticker
CLASSE = {'3': 'ON', '4': 'PN', '5': 'PN', '6': 'PN', '11': 'UNT'}


def _chamar(rota: str, params: dict, timeout: int = 60):
    """Os parametros viajam em base64 no caminho da URL. Resposta as vezes vem
    como JSON dentro de string -- duplo-codificada."""

    token = base64.b64encode(json.dumps(params).encode()).decode()
    r = requests.get(f"{BASE}/{rota}/{token}", timeout=timeout, headers=CABECALHO)
    # Falha de transporte/autenticacao nao pode parecer "empresa sem proventos".
    r.raise_for_status()
    if not r.text.strip():
        raise RuntimeError(f'resposta vazia da B3 em {rota}')
    d = r.json()
    return json.loads(d) if isinstance(d, str) else d


def empresa(ticker: str) -> dict | None:
    """Resolve o ticker para o registro da empresa na B3.

    Devolve `tradingName` (necessario para os proventos) e `codeCVM`, que e a
    chave limpa de juncao com o lado da CVM -- sem casar nome por aproximacao.
    """

    d = _chamar("GetInitialCompanies", {"language": "pt-br", "pageNumber": 1,
                                        "pageSize": 20, "company": ticker[:4]})
    for reg in (d or {}).get('results', []):
        if str(reg.get('issuingCompany', '')).upper() == ticker[:4].upper():
            return reg
    return None


def proventos(trading_name: str, pausa: float = 0.3) -> pd.DataFrame:
    """Historico completo de provento em dinheiro, paginado."""

    linhas, pagina = [], 1
    while True:
        d = _chamar("GetListedCashDividends",
                    {"language": "pt-br", "pageNumber": pagina,
                     "pageSize": 99, "tradingName": trading_name})
        if not isinstance(d, dict) or not isinstance(d.get('results'), list):
            raise RuntimeError(f'resposta inesperada de proventos B3: {trading_name}')
        linhas += d.get('results', [])
        if pagina >= d.get('page', {}).get('totalPages', 1):
            break
        pagina += 1
        time.sleep(pausa)

    if not linhas:
        return pd.DataFrame(columns=['ultimo_com', 'aprovado_em', 'classe', 'tipo', 'valor',
                                     'preco_anterior', 'pct'])

    d = pd.DataFrame(linhas)
    data_aprovacao = (d['dateApproval'] if 'dateApproval' in d.columns else
                      pd.Series(pd.NaT, index=d.index))
    saida = pd.DataFrame({
        'ultimo_com': pd.to_datetime(d['lastDateTimePriorEx'], errors='coerce'),
        'aprovado_em': pd.to_datetime(data_aprovacao, errors='coerce', dayfirst=True),
        'classe': d['typeStock'].astype(str).str.strip(),
        'tipo': d['corporateAction'].astype(str).str.strip(),
        'valor': _num(d['valueCash']),
        'preco_anterior': _num(d['closingPricePriorExDate']),
        'pct': _num(d['corporateActionPrice']),
    })
    if saida['ultimo_com'].isna().any():
        raise ValueError(f'provento B3 sem último pregão com direito: {trading_name}')
    # A CompanyCall pode repetir o mesmo provento literalmente (VLID3,
    # 13/03/2025, foi devolvido quatro vezes). Somá-lo quatro vezes fabricaria
    # retorno. Eventos diferentes no mesmo ex permanecem linhas distintas.
    return (saida.drop_duplicates()
            .sort_values('ultimo_com').reset_index(drop=True))


def eventos_ativos_empresa(empresa_b3: dict) -> pd.DataFrame:
    """Eventos em ações do emissor; a classe é filtrada por `proventos_ativos`."""
    dados = _chamar("GetListedSupplementCompany", {
        "language": "pt-br",
        "issuingCompany": empresa_b3['issuingCompany'],
    })
    if not isinstance(dados, list) or not dados:
        raise RuntimeError(f"resposta inesperada de eventos B3 para {empresa_b3['issuingCompany']}")
    linhas = dados[0].get('stockDividends', [])
    if not linhas:
        return pd.DataFrame(columns=['ultimo_com', 'aprovado_em', 'isin', 'tipo', 'fator_b3'])
    eventos = pd.DataFrame(linhas)
    data_aprovacao = (eventos['approvedOn'] if 'approvedOn' in eventos.columns else
                     pd.Series(pd.NaT, index=eventos.index))
    isin = (eventos['isinCode'] if 'isinCode' in eventos.columns else
            pd.Series('', index=eventos.index))
    saida = pd.DataFrame({
        'ultimo_com': pd.to_datetime(eventos.get('lastDatePrior'), errors='coerce', dayfirst=True),
        'aprovado_em': pd.to_datetime(data_aprovacao, errors='coerce', dayfirst=True),
        'isin': isin.astype(str).str.upper(),
        'tipo': eventos.get('label', pd.Series('', index=eventos.index)).astype(str).str.strip().str.upper(),
        'fator_b3': _num(eventos.get('factor', pd.Series(index=eventos.index, dtype=str))),
    })
    if saida[['ultimo_com', 'fator_b3']].isna().any().any():
        raise ValueError(f'evento em ações B3 sem data ou fator: {empresa_b3["issuingCompany"]}')
    return saida.sort_values('ultimo_com').reset_index(drop=True)


def proventos_ativos(ticker: str, empresa_b3: dict | None = None,
                    eventos_empresa: pd.DataFrame | None = None) -> pd.DataFrame:
    """Eventos em ações publicados na tela aberta de Empresas Listadas da B3.

    O endpoint oficial `GetListedSupplementCompany` inclui bonificações,
    desdobramentos e grupamentos, além de metadados de capital social. A B3
    fornece o fator e o último pregão com direito (`lastDatePrior`).
    """

    empresa_b3 = empresa_b3 or empresa(ticker)
    if not empresa_b3:
        raise ValueError(f"ticker sem cadastro na B3: {ticker}")
    saida = eventos_empresa.copy() if eventos_empresa is not None else eventos_ativos_empresa(empresa_b3)

    classe_isin = {'3': 'NOR', '4': 'NPR', '5': 'NPA', '6': 'NPB'}.get(ticker[-1:])
    if not classe_isin and not saida.empty:
        raise ValueError(f'classe de evento em ações não mapeada: {ticker}')
    if classe_isin:
        saida = saida[saida['isin'].str.contains(classe_isin, regex=False)]
    return saida.sort_values('ultimo_com').reset_index(drop=True)


def fatores_ativos(eventos: pd.DataFrame,
                   as_of: str | pd.Timestamp | None = None) -> pd.DataFrame:
    """Converte eventos em ações para fator retroativo de preço por data.

    Para desdobramento/bonificação, fator B3 é o aumento percentual de ações;
    em grupamento é a razão de ações remanescentes. Eventos ambíguos falham
    fechados para não transformar movimento de mercado em retorno artificial.
    """

    if eventos.empty:
        return pd.DataFrame(columns=['ultimo_com', 'fator'])
    if as_of is not None:
        corte = pd.Timestamp(as_of).normalize()
        if 'aprovado_em' not in eventos or eventos['aprovado_em'].isna().any():
            raise ValueError('evento em ações sem data de aprovação; PIT não verificável')
        eventos = eventos[eventos['aprovado_em'] < corte]
        if eventos.empty:
            return pd.DataFrame(columns=['ultimo_com', 'fator'])
    tipo = eventos['tipo'].astype(str).str.upper().map(
        lambda x: ''.join(c for c in unicodedata.normalize('NFKD', x)
                          if not unicodedata.combining(c)))
    proporcao = pd.to_numeric(eventos['fator_b3'], errors='coerce')
    grupamento = tipo.str.contains('GRUPAMENTO', regex=False)
    reconhecido = (grupamento | tipo.str.contains('DESDOBRAMENTO|BONIFICACAO', regex=True))
    if not reconhecido.all() or proporcao.isna().any():
        raise ValueError('evento em ações B3 ausente ou tipo/fator não suportado')
    multiplicador_acoes = proporcao.where(grupamento, 1 + proporcao / 100)
    if (multiplicador_acoes <= 0).any():
        raise ValueError('fator de evento em ações não positivo')
    fatores = 1 / multiplicador_acoes
    return (pd.DataFrame({'ultimo_com': eventos['ultimo_com'], 'fator': fatores})
              .groupby('ultimo_com', as_index=False)['fator'].prod())


def ajustar_total(ticker: str, precos: pd.Series,
                  as_of: str | pd.Timestamp | None = None,
                  empresa_b3: dict | None = None,
                  caixa: pd.DataFrame | None = None,
                  acoes: pd.DataFrame | None = None) -> pd.Series:
    """Ajusta preços por caixa e por eventos em ações publicados pela B3."""

    empresa_b3 = empresa_b3 or empresa(ticker)
    if not empresa_b3:
        raise ValueError(f"ticker sem cadastro na B3: {ticker}")
    caixa = fatores(caixa if caixa is not None else proventos(empresa_b3['tradingName']),
                    ticker, as_of=as_of)
    acoes = fatores_ativos(acoes if acoes is not None else
                           proventos_ativos(ticker, empresa_b3=empresa_b3), as_of=as_of)
    todos = pd.concat([caixa, acoes], ignore_index=True)
    if not todos.empty:
        todos = todos.groupby('ultimo_com', as_index=False)['fator'].prod()
    return ajustar(precos, todos)


def _num(serie: pd.Series) -> pd.Series:
    """Numero em formato brasileiro: milhar com ponto, decimal com virgula."""
    # A API alterna strings brasileiras e JSON numerico. Nao remover o ponto
    # decimal de um float ja desserializado (0.123 viraria 123).
    def converter(valor):
        if isinstance(valor, (int, float)):
            return valor
        bruto = str(valor).strip()
        if ',' in bruto:
            bruto = bruto.replace('.', '').replace(',', '.')
        elif bruto.count('.') > 1:
            # Agrupamento de milhares sem parte decimal (ex.: 2.417.135.038).
            bruto = bruto.replace('.', '')
        return bruto
    return pd.to_numeric(serie.map(converter), errors='coerce')


def fatores(prov: pd.DataFrame, ticker: str,
            as_of: str | pd.Timestamp | None = None) -> pd.DataFrame:
    """Fator de ajuste por data-ex, para a classe do ticker.

    `ultimo_com` e o ultimo pregao COM direito ao provento; a queda acontece no
    pregao SEGUINTE. Por isso o fator vale para todo preco ATE essa data,
    inclusive -- e nao ate a vespera. Dividendos e JCP no mesmo ex somam em
    dinheiro antes de calcular um único fator sobre o fechamento anterior.
    A convencao do indicador aplica 85% do percentual BRUTO apenas ao JCP.
    Essa constante foi conferida no gabarito historico e nao representa uma
    regra tributaria universal nem uma aliquota pessoal.
    """

    sufixo = ticker[4:]
    classe = CLASSE.get(sufixo)
    if classe is None and not prov.empty:
        raise ValueError(f'classe de provento em dinheiro não mapeada: {ticker}')
    if classe:
        prov = prov[prov['classe'].eq(classe)]

    if as_of is not None and not prov.empty:
        corte = pd.Timestamp(as_of).normalize()
        if 'aprovado_em' not in prov or prov['aprovado_em'].isna().any():
            raise ValueError('provento em dinheiro sem data de aprovação; PIT não verificável')
        prov = prov[prov['aprovado_em'] < corte]

    if prov.empty:
        return pd.DataFrame(columns=['ultimo_com', 'fator'])
    if (~prov['pct'].between(0, 100, inclusive='neither')).any():
        raise ValueError('provento em dinheiro com percentual inválido')

    tipo = prov['tipo'].astype(str).str.upper().map(
        lambda x: ''.join(c for c in unicodedata.normalize('NFKD', x)
                          if not unicodedata.combining(c)))
    jcp = tipo.str.contains(r'JRS CAP PROPRIO|JUROS.*CAP.*PROPRIO|\bJCP\b', regex=True)
    caixa_integral = tipo.isin({'DIVIDENDO', 'RENDIMENTO', 'REST CAP DIN'})
    if (~(jcp | caixa_integral)).any():
        desconhecidos = sorted(tipo[~(jcp | caixa_integral)].unique())
        raise ValueError(f'tipo de provento B3 não suportado: {desconhecidos}')
    prov = prov.copy()
    prov['pct_indicador'] = prov['pct'].where(~jcp, prov['pct'] * 0.85)
    total = prov.groupby('ultimo_com', as_index=False)['pct_indicador'].sum()
    if total['pct_indicador'].ge(100).any():
        raise ValueError('proventos em dinheiro somam 100% ou mais do preço anterior')
    total['fator'] = 1 - total['pct_indicador'] / 100
    return total[['ultimo_com', 'fator']]


def ajustar(precos: pd.Series, fat: pd.DataFrame) -> pd.Series:
    """Serie bruta -> serie ajustada por proventos (retroajuste padrao).

    O preco de HOJE nunca muda; o passado e que e reescalonado para baixo, de
    modo que a variacao entre dois dias vire retorno total. Um preco na data t
    leva o produto dos fatores de todo evento cujo ultimo dia COM direito seja
    t ou posterior -- inclusive t, porque naquele dia o papel ainda embutia o
    provento que cairia no pregao seguinte.
    """

    precos = precos.sort_index()
    if fat.empty:
        return precos.copy()

    fat = fat[fat['ultimo_com'] >= precos.index.min()].sort_values('ultimo_com')
    # produto acumulado dos eventos ainda por vir, visto de cada data
    acumulado = pd.Series(1.0, index=precos.index)
    for _, ev in fat.iterrows():
        acumulado.loc[precos.index <= ev['ultimo_com']] *= ev['fator']
    return precos * acumulado
