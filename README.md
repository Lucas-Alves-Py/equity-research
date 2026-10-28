# Lucas Alves — Equity Research

<!--
ESCREVA VOCÊ, em primeira pessoa, 2 a 3 frases:
- quem você é (curso, momento, o que procura);
- por que equity research;
- o que um avaliador vai encontrar aqui.
Não precisa ser perfeito. Precisa soar como você.
-->

## Trabalhos

| Trabalho | O que é | Resultado |
|---|---|---|
| [Fatores B3](fatores-b3/) | Carteira de 10 ações por valor, qualidade e momentum, com dados públicos da CVM e da B3 montados point-in-time | 28,0% a.a. contra 7,4% do universo, abr/2012 a nov/2025, dentro da amostra e sem custos. Decisões registradas antes de cada compra desde 06/10/2026 |

## Como rodar

Python 3.12.

```bash
python -m venv .venv
.venv/Scripts/activate        # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
cd fatores-b3 && python -m pytest -q
```

A reprodução completa do backtest está no [README de Fatores B3](fatores-b3/README.md#como-reproduzir).
Usa só dados públicos e não precisa de chave.

Não é recomendação de investimento: ver [DISCLAIMER.md](DISCLAIMER.md).
O código está sob a licença MIT ([LICENSE](LICENSE)), exceto o motor de
backtest do curso, que é da Varos.
