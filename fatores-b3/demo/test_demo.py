import unittest

import pandas as pd

from run_demo import load_inputs, run_demo
from carteira.indice import construir_indice_base_100, versoes_publicadas


class SyntheticIndexTest(unittest.TestCase):
    def test_index_matches_hand_calculation(self):
        result = run_demo()
        self.assertEqual(result["index"], [100.0, 110.0, 120.0, 108.0])
        self.assertEqual(result["dates"], ["2024-01-03", "2024-01-04", "2024-01-05", "2024-01-08"])
        self.assertEqual(result["total_return"], 0.08)
        self.assertEqual(result["max_drawdown"], -0.1)

    def test_signal_day_price_does_not_change_execution_return(self):
        portfolios, prices = load_inputs()
        baseline = construir_indice_base_100(portfolios, prices)
        prices.loc[prices["data"].eq("2024-01-02"), "preco_fechamento_ajustado"] = 9999.0
        pd.testing.assert_frame_equal(construir_indice_base_100(portfolios, prices), baseline)

    def test_old_portfolio_earns_rebalance_day_return(self):
        portfolios, prices = load_inputs()
        # BBB3 cai de 40 para 20 antes de ser comprada; a queda nao e da carteira.
        index = construir_indice_base_100(portfolios, prices).set_index("data")
        self.assertAlmostEqual(index.loc["2024-01-05", "indice"], 120.0)
        # AAA3 sobe depois de ser vendida; essa alta tambem nao e da carteira.
        self.assertAlmostEqual(index.loc["2024-01-08", "indice"], 108.0)

    def test_missing_execution_price_fails_instead_of_shifting_entry(self):
        portfolios, prices = load_inputs()
        prices = prices[~(prices["ticker"].eq("BBB3") & prices["data"].eq("2024-01-05"))]
        with self.assertRaisesRegex(ValueError, "ausentes na execucao"):
            construir_indice_base_100(portfolios, prices)

    def test_pending_portfolio_does_not_change_history(self):
        portfolios, prices = load_inputs()
        baseline = construir_indice_base_100(portfolios, prices)
        pending = portfolios.iloc[[0]].assign(data_sinal="2024-01-08", data_execucao=None, versao=2)
        pd.testing.assert_frame_equal(
            construir_indice_base_100(pd.concat([portfolios, pending], ignore_index=True), prices), baseline
        )

    def test_new_execution_preserves_previous_leg_for_same_signal(self):
        portfolios, _ = load_inputs()
        portfolios["data_sinal"] = "2024-01-02"
        portfolios.loc[1, "versao"] = 2
        self.assertEqual(len(versoes_publicadas(portfolios)), 2)


if __name__ == "__main__":
    unittest.main()
