import unittest

import pandas as pd

from carteira.indice import construir_indice_base_100


class IndiceBase100Test(unittest.TestCase):
    def test_comeca_em_100_e_encadeia_rebalanceamento(self):
        carteiras = pd.DataFrame(
            [
                {"data_sinal": "2026-01-01", "data_execucao": "2026-01-02", "ticker": "AAA3", "peso_alvo": 1.0, "posicao_ranking": 1, "versao": 1},
                {"data_sinal": "2026-01-03", "data_execucao": "2026-01-04", "ticker": "BBB3", "peso_alvo": 1.0, "posicao_ranking": 1, "versao": 1},
            ]
        )
        precos = pd.DataFrame(
            [
                {"data": "2026-01-02", "ticker": "AAA3", "preco_fechamento_ajustado": 10.0},
                {"data": "2026-01-03", "ticker": "AAA3", "preco_fechamento_ajustado": 11.0},
                {"data": "2026-01-04", "ticker": "AAA3", "preco_fechamento_ajustado": 12.0},
                {"data": "2026-01-04", "ticker": "BBB3", "preco_fechamento_ajustado": 20.0},
                {"data": "2026-01-05", "ticker": "BBB3", "preco_fechamento_ajustado": 18.0},
            ]
        )

        indice = construir_indice_base_100(carteiras, precos)

        self.assertEqual(indice["indice"].round(6).tolist(), [100.0, 110.0, 120.0, 108.0])
        self.assertEqual(indice["data"].dt.strftime("%Y-%m-%d").tolist(), ["2026-01-02", "2026-01-03", "2026-01-04", "2026-01-05"])


if __name__ == "__main__":
    unittest.main()
