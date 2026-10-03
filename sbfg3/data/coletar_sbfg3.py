# -*- coding: utf-8 -*-
"""
Coletor de dados SBFG3 (Grupo SBF) - API Fintz/Varos.

Le a chave da env FINTZ_API_KEY ou do .env na raiz do repositorio.
Grava tudo em sbfg3/data/raw/ (cache em disco: re-rodar
nao repete requisicao) e um resumo compacto em ..\\resumo.json.

Uso:
    python coletar_sbfg3.py
"""
from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path

try:
    import requests
except ImportError:
    print("Falta a lib requests. Rode: pip install requests")
    sys.exit(1)

BASE = "https://api.fintz.com.br"
RAIZ = Path(__file__).resolve().parents[2]
DEST = Path(__file__).resolve().parent
RAW = DEST / "raw"
ALVO = "SBFG3"
PARES = ["LREN3", "GUAR3", "VIVA3", "AZZA3", "CEAB3", "ALPA4", "PETZ3", "SMFT3"]
INICIO = "2018-01-01"

RAW.mkdir(parents=True, exist_ok=True)


def ler_chave() -> str:
    chave = os.environ.get("FINTZ_API_KEY")
    if chave:
        return chave.strip()
    for nome in (".env", ".env.txt"):
        p = RAIZ / nome
        if p.exists():
            txt = p.read_text(encoding="utf-8", errors="ignore")
            m = re.search(r"(?:token_varos|FINTZ_API_KEY|VAROS_TOKEN)\s*=\s*['\"]?([^\s'\"]+)", txt)
            if m:
                return m.group(1)
    raise SystemExit("Chave nao encontrada em FINTZ_API_KEY nem no .env da raiz.")


CHAVE = ler_chave()
SESSAO = requests.Session()
SESSAO.headers.update({"x-api-key": CHAVE, "accept": "application/json"})

falhas: list[dict] = []
_contador = {"rede": 0, "cache": 0}


def slug(texto: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", texto).strip("_")[:120]


def get(path: str, params: dict | None = None, rotulo: str | None = None):
    """GET com cache em disco. Devolve lista de dicts (ou None se falhou)."""
    params = {k: v for k, v in (params or {}).items() if v is not None}
    nome = slug((rotulo or path.strip("/").replace("/", "_")) + "__" +
                "_".join(f"{k}-{v}" for k, v in sorted(params.items())))
    arq = RAW / f"{nome}.json"
    if arq.exists():
        _contador["cache"] += 1
        try:
            return json.loads(arq.read_text(encoding="utf-8"))
        except Exception:
            arq.unlink(missing_ok=True)

    ultimo = None
    for tentativa in range(4):
        try:
            r = SESSAO.get(BASE + path, params=params, timeout=45)
        except requests.RequestException as e:
            ultimo = f"rede: {e}"
            time.sleep(2 ** tentativa)
            continue
        if r.status_code == 429:
            ultimo = "429 rate limit"
            time.sleep(5 * (tentativa + 1))
            continue
        if r.status_code >= 500:
            ultimo = f"{r.status_code} servidor"
            time.sleep(2 ** tentativa)
            continue
        if r.status_code == 401 or r.status_code == 403:
            raise SystemExit(f"Chave rejeitada ({r.status_code}) em {path}: {r.text[:300]}")
        if not r.ok:
            ultimo = f"{r.status_code}: {r.text[:200]}"
            break
        try:
            dados = r.json()
        except ValueError:
            ultimo = "resposta nao-JSON"
            break
        if isinstance(dados, dict):
            dados = [dados]
        _contador["rede"] += 1
        arq.write_text(json.dumps(dados, ensure_ascii=False), encoding="utf-8")
        time.sleep(0.25)
        return dados

    falhas.append({"path": path, "params": params, "erro": ultimo})
    print(f"  ! falhou {path} {params} -> {ultimo}")
    return None


# --------------------------------------------------------------- coleta

print("== 1. Busca de ativos")
busca = get("/bolsa/b3/avista/busca", {"q": "SBF"}, "busca_SBF") or []
busca_todos = get("/bolsa/b3/avista/busca", {"q": "SBFG"}, "busca_SBFG") or []
print(f"   {len(busca)} resultados para 'SBF'")

print("== 2. Itens contabeis e indicadores disponiveis para", ALVO)
itens_disp = get("/bolsa/b3/avista/itens-contabeis/por-ticker", {"ticker": ALVO}, "itens_por_ticker") or []
ind_disp = get("/bolsa/b3/avista/indicadores/por-ticker", {"ticker": ALVO}, "indicadores_por_ticker") or []


def codigos(lista, chaves=("item", "indicador", "codigo", "id", "nome", "sigla")):
    saida = []
    for linha in lista:
        if isinstance(linha, str):
            saida.append(linha)
            continue
        if not isinstance(linha, dict):
            continue
        for c in chaves:
            if c in linha and isinstance(linha[c], str):
                saida.append(linha[c])
                break
    # preserva ordem, sem duplicar
    vistos, res = set(), []
    for c in saida:
        if c not in vistos:
            vistos.add(c)
            res.append(c)
    return res


cods_itens = codigos(itens_disp)
cods_ind = codigos(ind_disp)
print(f"   {len(cods_itens)} itens contabeis, {len(cods_ind)} indicadores")

print("== 3. Historico contabil (TRIMESTRAL e 12M)")
contabil: dict[str, dict] = {}
for i, item in enumerate(cods_itens, 1):
    for periodo in ("TRIMESTRAL", "12M"):
        d = get("/bolsa/b3/avista/itens-contabeis/historico",
                {"item": item, "ticker": ALVO, "tipoPeriodo": periodo},
                f"item_{slug(item)}_{periodo}")
        if d:
            contabil.setdefault(item, {})[periodo] = d
    if i % 15 == 0:
        print(f"   {i}/{len(cods_itens)}")

print("== 4. Indicadores historicos")
indicadores: dict[str, list] = {}
for ind in cods_ind:
    d = get("/bolsa/b3/avista/indicadores/historico",
            {"indicador": ind, "ticker": ALVO}, f"ind_{slug(ind)}")
    if d:
        indicadores[ind] = d

print("== 5. Cotacoes, proventos e eventos")
cot = get("/bolsa/b3/avista/cotacoes/historico",
          {"ticker": ALVO, "dataInicio": INICIO}, "cotacoes_SBFG3") or []
prov = get("/bolsa/b3/avista/proventos", {"ticker": ALVO, "dataInicio": INICIO}, "proventos") or []
desd = get("/bolsa/b3/avista/desdobramentos", {"ticker": ALVO, "dataInicio": INICIO}, "desdobramentos") or []
bon = get("/bolsa/b3/avista/bonificacoes", {"ticker": ALVO, "dataInicio": INICIO}, "bonificacoes") or []
print(f"   {len(cot)} pregoes, {len(prov)} proventos")

print("== 6. Indices e taxas")
indices = {}
for idx in ("IBOV", "SMLL", "IBXX", "ICON"):
    d = get("/indices/historico", {"indice": idx, "dataInicio": INICIO}, f"indice_{idx}")
    if d:
        indices[idx] = d
cdi = get("/taxas/historico", {"codigo": 12, "dataInicio": INICIO}, "taxa_CDI")

print("== 7. Comparaveis")
pares_cot, pares_ind = {}, {}
IND_PARES = [c for c in cods_ind if c.upper().replace("_", "") in
             {"PL", "PVP", "EVEBITDA", "EVEBIT", "ROE", "ROIC", "DIVIDENDYIELD",
              "MARGEMEBITDA", "MARGEMLIQUIDA", "DIVIDALIQUIDAEBITDA"}]
for p in PARES:
    d = get("/bolsa/b3/avista/cotacoes/historico", {"ticker": p, "dataInicio": INICIO}, f"cotacoes_{p}")
    if d:
        pares_cot[p] = d
    for ind in IND_PARES:
        di = get("/bolsa/b3/avista/indicadores/historico", {"indicador": ind, "ticker": p},
                 f"ind_{slug(ind)}_{p}")
        if di:
            pares_ind.setdefault(p, {})[ind] = di

# ------------------------------------------------------------- derivados

def num(v):
    if isinstance(v, (int, float)) and not isinstance(v, bool) and not (isinstance(v, float) and math.isnan(v)):
        return float(v)
    return None


def serie_preco(linhas, campo="precoFechamentoAjustado"):
    pts = []
    for l in linhas:
        d = l.get("data") or l.get("dataPregao")
        v = num(l.get(campo)) or num(l.get("precoFechamento"))
        if d and v:
            pts.append((str(d)[:10], v))
    pts.sort()
    return pts


def retornos(pts):
    return [pts[i][1] / pts[i - 1][1] - 1 for i in range(1, len(pts)) if pts[i - 1][1]]


def desvio(xs):
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def retorno_janela(pts, dias):
    if len(pts) <= dias:
        return None
    return pts[-1][1] / pts[-1 - dias][1] - 1


def max_drawdown(pts):
    pico, mdd = None, 0.0
    for _, v in pts:
        pico = v if pico is None else max(pico, v)
        mdd = min(mdd, v / pico - 1)
    return mdd


px = serie_preco(cot)
bench = serie_preco(indices.get("IBOV", []), "valor") or serie_preco(indices.get("IBOV", []), "fechamento")

beta = None
if px and bench:
    mapa = dict(bench)
    comum = [(d, v) for d, v in px if d in mapa]
    if len(comum) > 60:
        ra = retornos(comum)
        rb = retornos([(d, mapa[d]) for d, _ in comum])
        n = min(len(ra), len(rb))
        ra, rb = ra[-n:], rb[-n:]
        ma, mb = sum(ra) / n, sum(rb) / n
        cov = sum((ra[i] - ma) * (rb[i] - mb) for i in range(n)) / (n - 1)
        var = sum((r - mb) ** 2 for r in rb) / (n - 1)
        beta = cov / var if var else None

r = retornos(px)
resumo = {
    "gerado_em": datetime.now().isoformat(timespec="seconds"),
    "ticker": ALVO,
    "fonte": "API Fintz/Varos",
    "cobertura_precos": {"de": px[0][0] if px else None, "ate": px[-1][0] if px else None,
                         "pregoes": len(px)},
    "preco_atual": px[-1][1] if px else None,
    "mercado": {
        "retorno_3M": retorno_janela(px, 63),
        "retorno_6M": retorno_janela(px, 126),
        "retorno_12M": retorno_janela(px, 252),
        "momentum_12_1M": (retorno_janela(px, 252) - retorno_janela(px, 21))
        if retorno_janela(px, 252) is not None and retorno_janela(px, 21) is not None else None,
        "vol_anualizada_252d": (desvio(r[-252:]) * math.sqrt(252)) if len(r) > 60 else None,
        "beta_ibov": beta,
        "max_drawdown_desde_2018": max_drawdown(px),
        "volume_medio_60d": None,
    },
    "itens_contabeis_disponiveis": cods_itens,
    "indicadores_disponiveis": cods_ind,
    "falhas": falhas,
    "chamadas": _contador,
}

vols = [num(l.get("volumeFinanceiro")) or num(l.get("volume")) for l in cot[-60:]]
vols = [v for v in vols if v]
if vols:
    resumo["mercado"]["volume_medio_60d"] = sum(vols) / len(vols)

(DEST / "resumo.json").write_text(json.dumps(resumo, ensure_ascii=False, indent=2), encoding="utf-8")
(DEST / "contabil.json").write_text(json.dumps(contabil, ensure_ascii=False), encoding="utf-8")
(DEST / "indicadores.json").write_text(json.dumps(indicadores, ensure_ascii=False), encoding="utf-8")
(DEST / "mercado.json").write_text(json.dumps(
    {"cotacoes": cot, "proventos": prov, "desdobramentos": desd, "bonificacoes": bon,
     "indices": indices, "cdi": cdi, "busca": busca + busca_todos},
    ensure_ascii=False), encoding="utf-8")
(DEST / "comparaveis.json").write_text(json.dumps(
    {"cotacoes": pares_cot, "indicadores": pares_ind}, ensure_ascii=False), encoding="utf-8")

print()
print("== PRONTO ==")
print(f"   requisicoes de rede: {_contador['rede']} | do cache: {_contador['cache']}")
print(f"   itens contabeis: {len(contabil)} | indicadores: {len(indicadores)}")
print(f"   falhas: {len(falhas)}")
print(f"   saida em: {DEST}")
