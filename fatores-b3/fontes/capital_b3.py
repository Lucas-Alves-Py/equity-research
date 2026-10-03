"""Captura datada do capital social publicado pela B3.

O endpoint publico mostra o estado atual, nao um historico de revisoes. Uma
captura posterior nunca e usada para reconstruir um sinal anterior. Cada
resposta bruta e preservada com hash para auditoria.
"""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests


URL = "https://sistemaswebb3-listados.b3.com.br/shareCapitalProxy/ShareCapitalCall/GetList/"
FONTE = "B3 ShareCapitalCall/GetList"
DESTINO = Path(__file__).resolve().parents[1] / "dados" / "b3" / "capital_social"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}


def _pagina(numero: int, tamanho: int = 500) -> dict:
    parametros = {"pageNumber": numero, "pageSize": tamanho}
    token = base64.b64encode(json.dumps(parametros, separators=(",", ":")).encode()).decode()
    resposta = requests.get(URL + token, headers=HEADERS, timeout=45)
    resposta.raise_for_status()
    objeto = resposta.json()
    if isinstance(objeto, str):
        objeto = json.loads(objeto)
    if not isinstance(objeto, dict) or not isinstance(objeto.get("results"), list):
        raise ValueError("Resposta de capital social da B3 sem lista de resultados")
    return objeto


def _inteiro_brasileiro(valor) -> int | None:
    if valor is None or str(valor).strip() == "":
        return None
    texto = str(valor).strip().replace(".", "").replace(" ", "")
    if not texto.isdecimal():
        raise ValueError(f"Quantidade de acoes invalida: {valor!r}")
    return int(texto)


def _baixar() -> dict:
    primeira = _pagina(1)
    paginas = int(primeira.get("page", {}).get("totalPages") or 1)
    total = int(primeira.get("page", {}).get("totalRecords") or 0)
    if paginas < 1 or paginas > 100 or total <= 0:
        raise ValueError("Paginacao de capital social da B3 invalida")
    linhas = list(primeira["results"])
    for numero in range(2, paginas + 1):
        linhas.extend(_pagina(numero)["results"])
    if len(linhas) != total:
        raise ValueError(f"B3 retornou {len(linhas)} linhas de {total} anunciadas")
    return {"fonte": FONTE, "url_base": URL, "capturado_em_utc": datetime.now(timezone.utc).isoformat(),
            "total_registros": total, "registros": linhas}


def _gravar(snapshot: dict) -> tuple[dict, str]:
    bruto = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    digest = hashlib.sha256(bruto).hexdigest()
    DESTINO.mkdir(parents=True, exist_ok=True)
    caminho = DESTINO / f"{digest}.json"
    if not caminho.exists():
        caminho.write_bytes(bruto)
    return snapshot, digest


def _ler_snapshot_para(instante: pd.Timestamp, max_idade_horas: int) -> tuple[dict, str] | None:
    if not DESTINO.exists():
        return None
    validos = []
    for caminho in DESTINO.glob("*.json"):
        bruto = caminho.read_bytes()
        digest = hashlib.sha256(bruto).hexdigest()
        if caminho.stem != digest:
            raise ValueError("Snapshot de capital social com hash divergente")
        snapshot = json.loads(bruto)
        capturado = pd.Timestamp(snapshot["capturado_em_utc"])
        if (capturado.tz_convert("America/Sao_Paulo").date() <= instante.date()
                and abs((instante.tz_convert("UTC") - capturado).total_seconds())
                <= max_idade_horas * 3600):
            validos.append((capturado, snapshot, digest))
    if not validos:
        return None
    _, snapshot, digest = max(validos, key=lambda item: item[0])
    return snapshot, digest


def capital_social_b3(as_of, atualizar: bool = False, max_idade_horas: int = 24) -> pd.DataFrame:
    """Capital por prefixo para um instante de decisao, com trava temporal.

    `elegivel_pit` exige captura no maximo na data do sinal, aprovacao anterior
    ao sinal, quantidade coerente e snapshot recente. O sinal e diario: a
    captura feita durante sua preparacao no mesmo dia pertence a decisao.
    Para sinais historicos,
    usar apenas um snapshot efetivamente arquivado naquela epoca.
    """
    instante = pd.Timestamp(as_of)
    if instante.tzinfo is None:
        instante = instante.tz_localize("America/Sao_Paulo")
    instante_utc = instante.tz_convert("UTC")
    hoje = pd.Timestamp.now(tz="America/Sao_Paulo").date()
    if instante.date() > hoje:
        raise ValueError("Decisao futura sem captura B3 observavel")
    atual = None if atualizar else _ler_snapshot_para(instante, max_idade_horas)
    if atual is None and instante.date() < hoje:
        raise FileNotFoundError("Sem snapshot B3 capturado na data historica da decisao")
    if atual is None:
        atual = _gravar(_baixar())
    snapshot, digest = atual
    capturado = pd.Timestamp(snapshot["capturado_em_utc"])
    linhas = []
    for registro in snapshot["registros"]:
        prefixo = str(registro.get("issuingCompany") or "").strip().upper()
        if not prefixo:
            continue
        on = _inteiro_brasileiro(registro.get("commonShares"))
        pn = _inteiro_brasileiro(registro.get("preferredShares"))
        total = _inteiro_brasileiro(registro.get("totalQtyShares"))
        aprovado = pd.to_datetime(registro.get("approvedDate"), format="%d/%m/%Y", errors="coerce")
        consistente = on is not None and pn is not None and total is not None and on + pn == total and total > 0
        # O dado da B3 e um snapshot atual: a captura nao pode suceder a decisao.
        data_captura_local = capturado.tz_convert("America/Sao_Paulo").date()
        elegivel = bool(consistente and registro.get("typeCapital") == "Homologado"
                        and pd.notna(aprovado)
                        and aprovado.date() < instante.date()
                        and data_captura_local <= instante.date()
                        and abs((instante_utc - capturado).total_seconds()) <= max_idade_horas * 3600)
        linhas.append({"prefixo": prefixo, "acoes_on": on, "acoes_pn": pn,
                       "total_acoes": total, "data_aprovacao": aprovado,
                       "capturado_em_utc": capturado, "fonte": FONTE,
                       "sha256": digest, "quantidade_consistente": bool(consistente),
                       "elegivel_pit": elegivel, "tipo_capital": registro.get("typeCapital")})
    quadro = pd.DataFrame(linhas)
    if quadro.empty:
        raise ValueError("Nenhum registro de capital social da B3")
    # "Subscrito" e outra categoria de capital, nao uma segunda classe a somar.
    quadro = quadro[quadro["tipo_capital"].eq("Homologado")].copy()
    colunas_identidade = ["prefixo", "acoes_on", "acoes_pn", "total_acoes", "data_aprovacao"]
    quadro = quadro.drop_duplicates(subset=colunas_identidade)
    duplicado = quadro["prefixo"].duplicated(keep=False)
    quadro.loc[duplicado, "elegivel_pit"] = False
    return quadro
