"""Exemplo sintetico: executa o indice do projeto sem API ou motor do curso."""

from pathlib import Path
import json
import sys

import pandas as pd

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT))

from carteira.indice import construir_indice_base_100

DATA = Path(__file__).resolve().parent


def load_inputs():
    return pd.read_csv(DATA / "carteiras.csv"), pd.read_csv(DATA / "precos.csv")


def run_demo():
    portfolios, prices = load_inputs()
    index = construir_indice_base_100(portfolios, prices)
    return {
        "classification": "synthetic_example",
        "dates": index["data"].dt.strftime("%Y-%m-%d").tolist(),
        "index": index["indice"].round(6).tolist(),
        "total_return": round(float(index["indice"].iloc[-1] / index["indice"].iloc[0] - 1), 6),
        "max_drawdown": round(float(index["drawdown"].min()), 6),
    }


if __name__ == "__main__":
    print(json.dumps(run_demo(), indent=2))
