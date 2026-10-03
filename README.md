# Lucas Alves — Equity Research

<!--
ESCREVA VOCÊ, em primeira pessoa, 2 a 3 frases:
- quem você é (curso, momento, o que procura);
- por que equity research;
- o que um avaliador vai encontrar aqui.
Não precisa ser perfeito. Precisa soar como você.
-->

## Trabalhos

| Trabalho | O que é | Situação |
|---|---|---|
| [Grupo SBF (SBFG3)](sbfg3/) | Cobertura de varejo esportivo: recomendação **___**, preço-alvo **R$ ___** | <!-- ex.: publicada em dd/mm/aaaa --> |
| [Fatores B3](fatores-b3/) | Seleção de ações por valor, qualidade e momentum com dados públicos (CVM e B3) | <!-- ex.: em acompanhamento desde dd/mm/aaaa --> |

## Como rodar

Python 3.12.

```bash
python -m venv .venv
.venv/Scripts/activate        # Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
```

Testes:

```bash
cd fatores-b3 && python -m pytest -q
cd sbfg3 && python -m pytest -q test_valuation.py
```

O pipeline de fatores usa apenas dados públicos (CVM e COTAHIST da B3) e não precisa de chave. A coleta de mercado de SBFG3 usa a API da Fintz (dado licenciado, não versionado); veja `.env.example`.
