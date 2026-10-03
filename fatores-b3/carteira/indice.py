"""Encadeamento base 100; o calculo nao certifica a origem temporal da carteira."""

from __future__ import annotations

import pandas as pd


def versoes_publicadas(
    carteiras: pd.DataFrame, apenas_executadas: bool = True
) -> pd.DataFrame:
    """Seleciona a versao vigente de cada publicacao sem apagar as anteriores.

    A identidade de uma publicacao e o par (data_sinal, data_execucao), nao a
    data do sinal sozinha. Uma versao nova sobre a MESMA execucao e uma
    CORRECAO: o registro anterior descrevia errado o que aconteceu, e so o
    ultimo vale. Uma execucao nova e uma DECISAO nova, e as duas convivem --
    a carteira antiga rendeu ate a nova entrar.

    Agrupar apenas por data_sinal misturava as duas coisas: uma mudanca de
    modelo decidida hoje, registrada sobre o mesmo sinal, apagaria a carteira
    que esteve efetivamente viva nos meses anteriores e reescreveria o
    historico com uma decisao retrospectiva.

    Com ``apenas_executadas=False`` tambem devolve publicacoes sem data de
    execucao -- decisao ja registrada, aguardando o proximo pregao.
    """

    # reset_index: o ledger chega de concatenacoes sucessivas e pode trazer
    # rotulos repetidos, que quebram o alinhamento das mascaras abaixo
    df = carteiras.reset_index(drop=True)
    df["data_sinal"] = pd.to_datetime(df["data_sinal"])
    df["data_execucao"] = pd.to_datetime(df.get("data_execucao"), errors="coerce")
    df["versao_normalizada"] = df.get("versao", 1)
    df["versao_normalizada"] = df["versao_normalizada"].fillna(1).astype(int)

    ultima_versao = df.groupby(
        ["data_sinal", "data_execucao"], dropna=False
    )["versao_normalizada"].transform("max")
    vigentes = df[df["versao_normalizada"].eq(ultima_versao)]
    if apenas_executadas:
        vigentes = vigentes.dropna(subset=["data_execucao"])
    else:
        # Uma publicacao pendente so vale enquanto for a ultima palavra sobre
        # aquele sinal. Assim que a execucao e registrada, ela deixa de ser
        # "o que vem a seguir" e sai da vitrine -- o ledger continua com tudo.
        maxima_do_sinal = df.groupby("data_sinal")["versao_normalizada"].transform("max")
        obsoleta = vigentes["data_execucao"].isna() & vigentes[
            "versao_normalizada"
        ].ne(maxima_do_sinal.loc[vigentes.index])
        vigentes = vigentes[~obsoleta]
    return (
        vigentes.sort_values(["data_execucao", "posicao_ranking"])
        .reset_index(drop=True)
    )


def construir_indice_base_100(
    carteiras: pd.DataFrame,
    precos: pd.DataFrame,
    base: float = 100.0,
) -> pd.DataFrame:
    """Encadeia as carteiras publicadas usando retornos de precos ajustados.

    A carteira passa a valer ``base`` no fechamento da primeira execucao D+1.
    Em rebalanceamentos posteriores, a carteira antiga rende ate o fechamento
    da execucao e os novos pesos passam a valer a partir dali.
    """

    publicadas = versoes_publicadas(carteiras)
    if publicadas.empty:
        return pd.DataFrame(columns=["data", "indice", "drawdown"])

    cotacoes = precos.copy()
    cotacoes["data"] = pd.to_datetime(cotacoes["data"])
    cotacoes = cotacoes[cotacoes["preco_fechamento_ajustado"] > 0]
    painel = cotacoes.pivot_table(
        index="data",
        columns="ticker",
        values="preco_fechamento_ajustado",
        aggfunc="last",
    ).sort_index()

    execucoes = sorted(publicadas["data_execucao"].unique())
    partes: list[pd.Series] = []
    nivel = float(base)

    for indice_execucao, data_execucao in enumerate(execucoes):
        grupo = publicadas[publicadas["data_execucao"].eq(data_execucao)].copy()
        tickers = grupo["ticker"].astype(str).tolist()
        pesos = grupo.set_index("ticker")["peso_alvo"].astype(float)
        pesos = pesos / pesos.sum()

        proxima_execucao = (
            execucoes[indice_execucao + 1]
            if indice_execucao + 1 < len(execucoes)
            else None
        )
        fim = proxima_execucao if proxima_execucao is not None else painel.index.max()
        trecho = painel.loc[(painel.index >= data_execucao) & (painel.index <= fim), tickers]
        trecho = trecho.dropna(how="any")
        if trecho.empty or trecho.index[0] != data_execucao:
            raise ValueError(f"precos ajustados ausentes na execucao {data_execucao.date()}")

        relativos = trecho.divide(trecho.iloc[0])
        serie = relativos.mul(pesos, axis="columns").sum(axis="columns") * nivel

        if partes:
            serie = serie.iloc[1:]
        partes.append(serie)

        if proxima_execucao is not None:
            nivel_execucao = relativos.mul(pesos, axis="columns").sum(axis="columns").iloc[-1]
            nivel *= float(nivel_execucao)

    indice = pd.concat(partes).rename("indice").reset_index()
    indice["drawdown"] = indice["indice"] / indice["indice"].cummax() - 1
    return indice
