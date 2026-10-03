# -*- coding: utf-8 -*-
"""
Consolida os dados brutos da coleta (coletar_sbfg3.py) em um unico arquivo
`analise.json` com os numeros que alimentam a cobertura.

Nao emite opiniao, recomendacao nem preco-alvo: so extrai, calcula e organiza.
Toda interpretacao fica na camada escrita pelo analista.

Uso:
    python analisar_sbfg3.py
"""
from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

DEST = Path(__file__).resolve().parent
ALVO = "SBFG3"

contabil = json.loads((DEST / "contabil.json").read_text(encoding="utf-8"))
indicadores = json.loads((DEST / "indicadores.json").read_text(encoding="utf-8"))
mercado = json.loads((DEST / "mercado.json").read_text(encoding="utf-8"))
comparaveis = json.loads((DEST / "comparaveis.json").read_text(encoding="utf-8"))
resumo = json.loads((DEST / "resumo.json").read_text(encoding="utf-8"))


# ------------------------------------------------------------- utilitarios

def periodo(linha) -> tuple[int, int]:
    return (int(linha.get("ano") or 0), int(linha.get("trimestre") or 0))


def rotulo(p: tuple[int, int]) -> str:
    return f"{p[1]}T{str(p[0])[2:]}"


def serie(item: str, tipo: str = "12M") -> dict[tuple[int, int], float]:
    """Serie {(ano, trimestre): valor} de um item contabil."""
    linhas = (contabil.get(item) or {}).get(tipo) or []
    saida = {}
    for l in linhas:
        v = l.get("valor")
        if isinstance(v, (int, float)):
            saida[periodo(l)] = float(v)
    return saida


def ultimo(mapa: dict) -> tuple[tuple[int, int] | None, float | None]:
    if not mapa:
        return None, None
    k = max(mapa)
    return k, mapa[k]


def ind_serie(nome: str) -> list[tuple[str, float]]:
    """Serie [(data, valor)] de um indicador, ordenada."""
    pts = []
    for l in indicadores.get(nome) or []:
        d, v = l.get("data"), l.get("valor")
        if d and isinstance(v, (int, float)):
            pts.append((str(d)[:10], float(v)))
    pts.sort()
    return pts


def ind_ultimo(nome: str):
    pts = ind_serie(nome)
    return pts[-1] if pts else (None, None)


def div(a, b):
    if a is None or b in (None, 0):
        return None
    return a / b


def pct_rank(valor, amostra: list[float]) -> float | None:
    """Percentil do valor dentro da amostra (0-100)."""
    xs = [x for x in amostra if isinstance(x, (int, float))]
    if valor is None or len(xs) < 3:
        return None
    return round(100 * sum(1 for x in xs if x <= valor) / len(xs), 1)


# ------------------------------------------------------ 1. serie historica

ITENS_FLUXO = ["ReceitaLiquida", "Custos", "ResultadoBruto", "EBITDA", "EBIT",
               "DespesasFinanceiras", "ReceitasFinanceiras", "ResultadoFinanceiro",
               "LAIR", "Impostos", "LucroLiquido", "LucroLiquidoSociosControladora",
               "DepreciacaoAmortizacao", "DespesasReceitasOperacionaisOuAdministrativas"]
ITENS_ESTOQUE = ["AtivoTotal", "AtivoCirculante", "AtivoNaoCirculante", "CaixaEquivalentes",
                 "Disponibilidades", "PassivoTotal", "PassivoCirculante", "PassivoNaoCirculante",
                 "PatrimonioLiquido", "DividaBruta", "DividaLiquida"]

ltm = {i: serie(i, "12M") for i in ITENS_FLUXO}
tri = {i: serie(i, "TRIMESTRAL") for i in ITENS_FLUXO}
bal = {}
for i in ITENS_ESTOQUE:
    s = serie(i, "TRIMESTRAL") or serie(i, "12M")
    bal[i] = s

corte, _ = ultimo(ltm["ReceitaLiquida"])

# Ano fiscal fechado = 4o trimestre de cada ano.
anos_fechados = sorted({a for (a, t) in ltm["ReceitaLiquida"] if t == 4})


def linha_anual(item: str, fonte=ltm) -> dict[str, float | None]:
    s = fonte.get(item) or {}
    saida = {str(a): s.get((a, 4)) for a in anos_fechados}
    if corte and corte[1] != 4:
        saida[f"LTM {rotulo(corte)}"] = s.get(corte)
    return saida


dre_anual = {i: linha_anual(i) for i in
             ["ReceitaLiquida", "ResultadoBruto", "EBITDA", "EBIT", "LucroLiquido"]}
bal_anual = {i: linha_anual(i, bal) for i in
             ["AtivoTotal", "PatrimonioLiquido", "DividaBruta", "DividaLiquida", "CaixaEquivalentes"]}

colunas = list(dre_anual["ReceitaLiquida"].keys())


def margem(num_item: str, den_item="ReceitaLiquida"):
    return {c: div(dre_anual[num_item][c], dre_anual[den_item][c]) for c in colunas}


margens = {
    "MargemBruta": margem("ResultadoBruto"),
    "MargemEBITDA": margem("EBITDA"),
    "MargemEBIT": margem("EBIT"),
    "MargemLiquida": margem("LucroLiquido"),
}

crescimento = {}
for c_ant, c in zip(colunas, colunas[1:]):
    a, b = dre_anual["ReceitaLiquida"][c_ant], dre_anual["ReceitaLiquida"][c]
    crescimento[c] = div(b, a) - 1 if (a and b) else None

# CAGR de receita do primeiro ao ultimo ano fechado.
cagr_receita = None
if len(anos_fechados) >= 2:
    ini = dre_anual["ReceitaLiquida"][str(anos_fechados[0])]
    fim = dre_anual["ReceitaLiquida"][str(anos_fechados[-1])]
    n = anos_fechados[-1] - anos_fechados[0]
    if ini and fim and n:
        cagr_receita = (fim / ini) ** (1 / n) - 1


# ---------------------------------------------------- 2. retornos e capital

def capital_investido(p) -> float | None:
    pl = (bal["PatrimonioLiquido"] or {}).get(p)
    db = (bal["DividaBruta"] or {}).get(p)
    cx = (bal["CaixaEquivalentes"] or {}).get(p)
    if pl is None or db is None:
        return None
    return pl + db - (cx or 0)


ci_atual = capital_investido(corte) if corte else None
ebit_ltm = ltm["EBIT"].get(corte) if corte else None
lucro_ltm = ltm["LucroLiquido"].get(corte) if corte else None
receita_ltm = ltm["ReceitaLiquida"].get(corte) if corte else None
ebitda_ltm = ltm["EBITDA"].get(corte) if corte else None
pl_atual = (bal["PatrimonioLiquido"] or {}).get(corte) if corte else None

retornos_calc = {
    "capital_investido": ci_atual,
    "ROIC_pre_imposto_calculado": div(ebit_ltm, ci_atual),
    "ROE_calculado": div(lucro_ltm, pl_atual),
    "giro_capital_investido": div(receita_ltm, ci_atual),
}

# ROIC/ROE reportados pela API (base propria do provedor).
retornos_api = {
    "ROIC": ind_ultimo("ROIC"),
    "ROE": ind_ultimo("ROE"),
    "ROA": ind_ultimo("ROA"),
    "GiroAtivos": ind_ultimo("GiroAtivos"),
}

# --------------------------------------------------------- 3. alavancagem

alavancagem = {
    "DividaBruta": (bal["DividaBruta"] or {}).get(corte) if corte else None,
    "DividaLiquida": (bal["DividaLiquida"] or {}).get(corte) if corte else None,
    "CaixaEquivalentes": (bal["CaixaEquivalentes"] or {}).get(corte) if corte else None,
    "DividaLiquida_EBITDA_api": ind_ultimo("DividaLiquida_EBITDA"),
    "DividaLiquida_EBITDA_calculado": div((bal["DividaLiquida"] or {}).get(corte) if corte else None, ebitda_ltm),
    "DividaLiquida_PatrimonioLiquido": ind_ultimo("DividaLiquida_PatrimonioLiquido"),
    "EBIT_DespesasFinanceiras": ind_ultimo("EBIT_DespesasFinanceiras"),
    "EBITDA_DespesasFinanceiras": ind_ultimo("EBITDA_DespesasFinanceiras"),
    "LiquidezCorrente": ind_ultimo("LiquidezCorrente"),
    "DespesasFinanceiras_LTM": ltm["DespesasFinanceiras"].get(corte) if corte else None,
    "ResultadoFinanceiro_LTM": ltm["ResultadoFinanceiro"].get(corte) if corte else None,
}

# ----------------------------------------------------------- 4. multiplos

MULTIPLOS = ["P_L", "EV_EBITDA", "EV_EBIT", "P_VP", "P_SR", "P_EBITDA", "P_EBIT",
             "DividendYield", "EBITDA_EV", "EBIT_EV", "EV", "ValorDeMercado",
             "LPA", "VPA", "QuantidadeAcoesTotais"]
multiplos = {m: ind_ultimo(m) for m in MULTIPLOS}


def historico_multiplo(nome: str, anos: int = 5):
    """Media, mediana, min, max e percentil atual do multiplo nos ultimos anos."""
    pts = ind_serie(nome)
    if not pts:
        return None
    limite = f"{date.fromisoformat(pts[-1][0]).year - anos}-{pts[-1][0][5:]}"
    janela = [v for d, v in pts if d >= limite]
    if len(janela) < 8:
        janela = [v for _, v in pts]
    if not janela:
        return None
    ordenado = sorted(janela)
    n = len(ordenado)
    atual = pts[-1][1]
    return {
        "atual": atual,
        "media": sum(janela) / n,
        "mediana": ordenado[n // 2] if n % 2 else (ordenado[n // 2 - 1] + ordenado[n // 2]) / 2,
        "min": ordenado[0],
        "max": ordenado[-1],
        "percentil_atual": pct_rank(atual, janela),
        "observacoes": n,
        "de": pts[0][0] if len(janela) == len(pts) else limite,
        "ate": pts[-1][0],
    }


percentis_historicos = {m: historico_multiplo(m) for m in
                        ["P_L", "EV_EBITDA", "EV_EBIT", "P_VP", "MargemEBITDA", "ROE", "ROIC", "DividendYield"]}

# ------------------------------------------------------------- 5. mercado

cotacoes = sorted(mercado.get("cotacoes") or [], key=lambda l: str(l.get("data") or l.get("dataPregao") or ""))


def preco_serie(linhas, campos=("precoFechamentoAjustado", "precoFechamento", "fechamento", "valor")):
    pts = []
    for l in linhas:
        d = l.get("data") or l.get("dataPregao")
        v = None
        for c in campos:
            if isinstance(l.get(c), (int, float)):
                v = float(l[c])
                break
        if d and v:
            pts.append((str(d)[:10], v))
    pts.sort()
    return pts


px = preco_serie(cotacoes)


def retornos(pts):
    return [pts[i][1] / pts[i - 1][1] - 1 for i in range(1, len(pts)) if pts[i - 1][1]]


def desvio(xs):
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def ret_janela(pts, dias):
    if len(pts) <= dias:
        return None
    return pts[-1][1] / pts[-1 - dias][1] - 1


def drawdown(pts):
    pico, mdd, fundo = None, 0.0, None
    for d, v in pts:
        pico = v if pico is None else max(pico, v)
        atual = v / pico - 1
        if atual < mdd:
            mdd, fundo = atual, d
    return mdd, fundo


r = retornos(px)
r252 = r[-252:]
downside = [x for x in r252 if x < 0]

mdd, mdd_data = drawdown(px)
mdd_3a, _ = drawdown(px[-756:]) if len(px) > 756 else (None, None)

mercado_calc = {
    "preco_atual": px[-1][1] if px else None,
    "data_preco": px[-1][0] if px else None,
    "inicio_serie": px[0][0] if px else None,
    "pregoes": len(px),
    "maxima_52s": max(v for _, v in px[-252:]) if len(px) >= 60 else None,
    "minima_52s": min(v for _, v in px[-252:]) if len(px) >= 60 else None,
    "retorno_1M": ret_janela(px, 21),
    "retorno_3M": ret_janela(px, 63),
    "retorno_6M": ret_janela(px, 126),
    "retorno_12M": ret_janela(px, 252),
    "momentum_12_1M": (ret_janela(px, 252) - ret_janela(px, 21))
    if ret_janela(px, 252) is not None and ret_janela(px, 21) is not None else None,
    "vol_anualizada_252d": desvio(r252) * math.sqrt(252) if len(r252) > 60 else None,
    "downside_deviation_252d": desvio(downside) * math.sqrt(252) if len(downside) > 20 else None,
    "beta_ibov": resumo.get("mercado", {}).get("beta_ibov"),
    "beta_ibov_252d": None,
    "max_drawdown_serie": mdd,
    "max_drawdown_data": mdd_data,
    "max_drawdown_3a": mdd_3a,
    "volume_medio_60d": None,
    "quantidade_media_60d": None,
}

vol60 = [l.get("volumeNegociado") for l in cotacoes[-60:] if isinstance(l.get("volumeNegociado"), (int, float))]
qtd60 = [l.get("quantidadeNegociada") for l in cotacoes[-60:] if isinstance(l.get("quantidadeNegociada"), (int, float))]
if vol60:
    mercado_calc["volume_medio_60d"] = sum(vol60) / len(vol60)
if qtd60:
    mercado_calc["quantidade_media_60d"] = sum(qtd60) / len(qtd60)

# Retorno relativo ao IBOV nas mesmas janelas.
ibov = preco_serie((mercado.get("indices") or {}).get("IBOV") or [])
if ibov:
    mapa = dict(ibov)
    comum = [(d, v) for d, v in px if d in mapa]
    if len(comum) > 120:
        ra = retornos(comum)[-252:]
        rb = retornos([(d, mapa[d]) for d, _ in comum])[-252:]
        n = min(len(ra), len(rb))
        ra, rb = ra[-n:], rb[-n:]
        ma, mb = sum(ra) / n, sum(rb) / n
        cov = sum((ra[i] - ma) * (rb[i] - mb) for i in range(n)) / (n - 1)
        var = sum((x - mb) ** 2 for x in rb) / (n - 1)
        mercado_calc["beta_ibov_252d"] = cov / var if var else None
    for janela, dias in (("3M", 63), ("6M", 126), ("12M", 252)):
        a, b = ret_janela(px, dias), ret_janela(ibov, dias)
        mercado_calc[f"excesso_vs_ibov_{janela}"] = (a - b) if (a is not None and b is not None) else None
    mercado_calc["ibov_data"] = ibov[-1][0]

proventos = mercado.get("proventos") or []
mercado_calc["proventos_registrados"] = len(proventos)
if proventos:
    datas = [str(p.get("dataCom") or p.get("data") or "")[:10] for p in proventos]
    mercado_calc["provento_mais_recente"] = max(d for d in datas if d)

# ------------------------------------ 5b. multiplos no preco mais recente

# Os multiplos que a API entrega tem data propria (ultimo dado em `multiplos`),
# que nao coincide com o ultimo pregao. Aqui os mesmos multiplos sao refeitos
# com o preco corrente e os numeros contabeis LTM da data de corte, para que o
# tear sheet nao misture duas fotos de datas diferentes.
preco = mercado_calc["preco_atual"]
acoes = multiplos["QuantidadeAcoesTotais"][1]
divida_liquida = alavancagem["DividaLiquida"]

mkt_cap = preco * acoes if (preco and acoes) else None
ev = mkt_cap + divida_liquida if (mkt_cap is not None and divida_liquida is not None) else None

multiplos_no_preco = {
    "preco": preco,
    "data_preco": mercado_calc["data_preco"],
    "acoes_totais": acoes,
    "data_acoes": multiplos["QuantidadeAcoesTotais"][0],
    "valor_de_mercado": mkt_cap,
    "divida_liquida_usada": divida_liquida,
    "enterprise_value": ev,
    "P_L": div(mkt_cap, lucro_ltm),
    "P_VP": div(mkt_cap, pl_atual),
    "EV_EBITDA": div(ev, ebitda_ltm),
    "EV_EBIT": div(ev, ebit_ltm),
    "P_SR": div(mkt_cap, receita_ltm),
    "posicao_na_faixa_52s": div(preco - mercado_calc["minima_52s"],
                                mercado_calc["maxima_52s"] - mercado_calc["minima_52s"])
    if mercado_calc["minima_52s"] and mercado_calc["maxima_52s"] else None,
    "observacao": (
        f"Preco de {mercado_calc['data_preco']} combinado com LTM {rotulo(corte) if corte else 'n/d'} "
        f"e balanco da mesma data. Os multiplos do bloco `multiplos` sao de "
        f"{multiplos['P_L'][0]} e nao refletem este preco."
    ),
}

# --------------------------------------------------------- 6. comparaveis

pares_ind = comparaveis.get("indicadores") or {}
pares_cot = comparaveis.get("cotacoes") or {}
IND_PARES = ["P_L", "EV_EBITDA", "EV_EBIT", "P_VP", "ROE", "ROIC",
             "MargemEBITDA", "MargemLiquida", "DividaLiquida_EBITDA", "DividendYield"]


def ultimo_par(par: str, ind: str):
    linhas = (pares_ind.get(par) or {}).get(ind) or []
    pts = [(str(l.get("data"))[:10], float(l["valor"])) for l in linhas
           if isinstance(l.get("valor"), (int, float)) and l.get("data")]
    pts.sort()
    return pts[-1] if pts else (None, None)


tabela_pares = {}
for par in list(pares_ind.keys()):
    linha = {}
    for ind in IND_PARES:
        d, v = ultimo_par(par, ind)
        linha[ind] = {"valor": v, "data": d}
    p = preco_serie(pares_cot.get(par) or [])
    linha["retorno_12M"] = {"valor": ret_janela(p, 252), "data": p[-1][0] if p else None}
    tabela_pares[par] = linha

# Linha do alvo para o mesmo conjunto de indicadores.
linha_alvo = {}
for ind in IND_PARES:
    d, v = ind_ultimo(ind)
    linha_alvo[ind] = {"valor": v, "data": d}
linha_alvo["retorno_12M"] = {"valor": mercado_calc["retorno_12M"], "data": mercado_calc["data_preco"]}
tabela_pares[ALVO] = linha_alvo

# Posicao do alvo dentro do grupo (percentil e mediana dos pares).
posicao_relativa = {}
for ind in IND_PARES + ["retorno_12M"]:
    amostra = [tabela_pares[p][ind]["valor"] for p in tabela_pares if p != ALVO]
    amostra = [x for x in amostra if isinstance(x, (int, float))]
    valor = tabela_pares[ALVO][ind]["valor"]
    mediana = None
    if amostra:
        s = sorted(amostra)
        n = len(s)
        mediana = s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2
    posicao_relativa[ind] = {
        "alvo": valor,
        "mediana_pares": mediana,
        "premio_desconto_vs_mediana": div(valor, mediana) - 1 if (valor is not None and mediana) else None,
        "percentil_no_grupo": pct_rank(valor, amostra + ([valor] if valor is not None else [])),
        "pares_com_dado": len(amostra),
    }

# ------------------------------------------------------------- 7. lacunas

# O que a cobertura exige e esta API nao entrega. Precisa vir do RI / ITR / DFP.
lacunas = [
    "Demonstracao de fluxo de caixa (CFO, capex, FCF, dividendos pagos, recompras): "
    "nao ha itens de fluxo de caixa na API. Fonte: ITR/DFP no RI.",
    "Abertura de receita por canal (fisico x digital) e por bandeira (Centauro, Nike, Fisia): "
    "so no release e na apresentacao de resultados.",
    "Estoques, contas a receber e fornecedores em linha propria (capital de giro): "
    "a API entrega apenas AtivoCirculante/PassivoCirculante agregados.",
    "Numero de lojas, area de vendas, SSS (same-store sales) e vendas por m2: release trimestral.",
    "Free float, composicao acionaria e concentracao: Formulario de Referencia.",
    "Estimativas de consenso, revisoes e surpresa de resultados: nao disponiveis nesta API.",
]

# ------------------------------------------------------------- 8. gravacao

saida = {
    "gerado_em": date.today().isoformat(),
    "ticker": ALVO,
    "fonte": "API Fintz (api.fintz.com.br)",
    "data_corte_contabil": rotulo(corte) if corte else None,
    "data_corte_mercado": mercado_calc["data_preco"],
    "dre_anual": dre_anual,
    "margens": margens,
    "crescimento_receita": crescimento,
    "cagr_receita": cagr_receita,
    "balanco_anual": bal_anual,
    "retornos_calculados": retornos_calc,
    "retornos_api": retornos_api,
    "alavancagem": alavancagem,
    "multiplos": multiplos,
    "multiplos_no_preco_atual": multiplos_no_preco,
    "percentis_historicos": percentis_historicos,
    "mercado": mercado_calc,
    "comparaveis": tabela_pares,
    "posicao_relativa": posicao_relativa,
    "lacunas_de_dados": lacunas,
}

(DEST / "analise.json").write_text(json.dumps(saida, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


# ------------------------------------------------------------ 9. relatorio

def fmt(v, tipo="num"):
    if v is None:
        return "n/d"
    if tipo == "pct":
        return f"{v * 100:.1f}%"
    if tipo == "bi":
        return f"{v / 1e9:.2f}"
    if tipo == "x":
        return f"{v:.2f}x"
    return f"{v:,.2f}"


print(f"== SBFG3 | corte contabil {saida['data_corte_contabil']} | preco {saida['data_corte_mercado']}")
print()
print("-- DRE (R$ bi)")
cab = " | ".join(f"{c:>10}" for c in colunas)
print(f"{'':<16} | {cab}")
for item in ["ReceitaLiquida", "ResultadoBruto", "EBITDA", "EBIT", "LucroLiquido"]:
    vals = " | ".join(f"{fmt(dre_anual[item][c], 'bi'):>10}" for c in colunas)
    print(f"{item:<16} | {vals}")
print()
print("-- Margens")
for nome, m in margens.items():
    vals = " | ".join(f"{fmt(m[c], 'pct'):>10}" for c in colunas)
    print(f"{nome:<16} | {vals}")
print()
print("-- Crescimento de receita")
for c, v in crescimento.items():
    print(f"   {c:<12} {fmt(v, 'pct')}")
print(f"   CAGR {anos_fechados[0]}-{anos_fechados[-1]}: {fmt(cagr_receita, 'pct')}")
print()
print("-- Balanco (R$ bi)")
for item, m in bal_anual.items():
    vals = " | ".join(f"{fmt(m[c], 'bi'):>10}" for c in colunas)
    print(f"{item:<20} | {vals}")
print()
print("-- Retornos e alavancagem")
print(json.dumps({**retornos_calc, **{k: v for k, v in retornos_api.items()}}, ensure_ascii=False, indent=2, default=str))
print(json.dumps(alavancagem, ensure_ascii=False, indent=2, default=str))
print()
print("-- Multiplos (ultimo dado)")
print(json.dumps(multiplos, ensure_ascii=False, indent=2, default=str))
print()
print("-- Multiplos recalculados no preco mais recente")
print(json.dumps(multiplos_no_preco, ensure_ascii=False, indent=2, default=str))
print()
print("-- Percentis historicos")
print(json.dumps(percentis_historicos, ensure_ascii=False, indent=2, default=str))
print()
print("-- Mercado")
print(json.dumps(mercado_calc, ensure_ascii=False, indent=2, default=str))
print()
print("-- Comparaveis (ultimo dado por par)")
print(json.dumps(tabela_pares, ensure_ascii=False, indent=2, default=str))
print()
print("-- Posicao relativa")
print(json.dumps(posicao_relativa, ensure_ascii=False, indent=2, default=str))
print()
print(f"analise.json gravado em {DEST / 'analise.json'}")
