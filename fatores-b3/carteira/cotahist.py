"""COTAHIST da B3 -- universo completo de cotacoes, sem limite de requisicao.

Por que esta fonte
------------------
Para rebalancear e preciso ranquear ~460 tickers, o que exige preco e volume
do universo inteiro a cada data. A brapi cobra 1 ativo por requisicao e limita
a profundidade do historico por ticker, entao seriam centenas de chamadas.

O COTAHIST e o arquivo oficial da B3: um zip por ano com TODOS os papeis de
TODOS os pregoes. Uma requisicao resolve o ano inteiro.

Limitacao importante
--------------------
O COTAHIST traz **preco bruto**, sem ajuste de proventos, splits ou
bonificacoes. Serve direto para:
  - filtro de liquidez (volume financeiro)
  - universo de tickers negociaveis

NAO serve direto para momentum, que precisa de preco ajustado. Para isso e
preciso aplicar os eventos societarios (a Fintz expoe /proventos,
/desdobramentos e /bonificacoes) ou emendar com uma serie ja ajustada.

O campo FATCOT (fator de cotacao) indica se o preco e por unidade (1) ou por
lote de mil (1000) -- precisa ser considerado.

Layout do registro tipo 01 (posicoes 1-based, inclusivas)
---------------------------------------------------------
 01-02  TIPREG   tipo de registro ('01' = cotacao)
 03-10  DATA     AAAAMMDD
 11-12  CODBDI   '02' = lote padrao
 13-24  CODNEG   ticker
 25-27  TPMERC   '010' = mercado a vista
 57-69  PREABE   abertura      (2 decimais implicitos)
 70-82  PREMAX   maxima
 83-95  PREMIN   minima
 96-108 PREMED   media
109-121 PREULT   fechamento
148-152 TOTNEG   numero de negocios
153-170 QUATOT   quantidade de titulos
171-188 VOLTOT   volume financeiro (2 decimais implicitos)
211-217 FATCOT   fator de cotacao
"""

import io
import hashlib
import json
import os
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import requests

AQUI = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(AQUI, 'ledger')
URL = "https://bvmf.bmfbovespa.com.br/InstDados/SerHist/COTAHIST_A{ano}.ZIP"

# nome -> (inicio 0-based, fim exclusivo, divisor)
CAMPOS = {
    'tipreg':  (0, 2, None),
    'data':    (2, 10, None),
    'codbdi':  (10, 12, None),
    'ticker':  (12, 24, None),
    'tpmerc':  (24, 27, None),
    'abertura': (56, 69, 100),
    'maxima':   (69, 82, 100),
    'minima':   (82, 95, 100),
    'media':    (95, 108, 100),
    'fechamento': (108, 121, 100),
    'negocios': (147, 152, None),
    'quantidade': (152, 170, None),
    'volume_negociado': (170, 188, 100),
    'fatcot':   (210, 217, None),
}


def baixar(ano, destino=None, atualizar=False):
    """Baixa/verifica o arquivo anual e guarda versoes imutaveis por hash.

    O COTAHIST do ano corrente e revalidado a cada 24h usando ETag ou
    Last-Modified quando o servidor oferece esses cabeçalhos. Anos fechados
    sao revalidados mensalmente. Uma resposta 304 reutiliza o snapshot local.
    """
    destino = destino or os.path.join(CACHE, f'COTAHIST_A{ano}.ZIP')
    destino = Path(destino)
    destino.parent.mkdir(parents=True, exist_ok=True)
    meta_path = destino.with_suffix(destino.suffix + '.meta.json')
    meta = {}
    if meta_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            meta = {}
    atual = destino.parent / meta.get('arquivo_atual', '') if meta.get('arquivo_atual') else None
    if atual is not None and not atual.exists():
        atual = None
    ttl = 24 * 60 * 60 if ano == datetime.now().year else 30 * 24 * 60 * 60
    if not atualizar and atual and meta.get('verificado_em_utc'):
        try:
            verificado = datetime.fromisoformat(meta['verificado_em_utc'])
            if 0 <= (datetime.now(timezone.utc) - verificado).total_seconds() < ttl:
                return str(atual)
        except (ValueError, TypeError):
            pass

    headers = {}
    if meta.get('etag'):
        headers['If-None-Match'] = meta['etag']
    if meta.get('last_modified'):
        headers['If-Modified-Since'] = meta['last_modified']
    url = URL.format(ano=ano)
    with requests.get(url, stream=True, timeout=900, headers=headers) as r:
        if r.status_code == 304 and atual is not None:
            meta['verificado_em_utc'] = datetime.now(timezone.utc).isoformat()
            meta['http_status_ultima_verificacao'] = 304
            tmp_meta = meta_path.with_suffix(meta_path.suffix + '.tmp')
            tmp_meta.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding='utf-8')
            os.replace(tmp_meta, meta_path)
            return str(atual)
        r.raise_for_status()
        if r.status_code != 200:
            raise RuntimeError(f"COTAHIST inesperado HTTP {r.status_code}")
        versoes = destino.parent / 'versoes'
        versoes.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=versoes, suffix=destino.suffix,
                                         delete=False) as tmp:
            temporario = Path(tmp.name)
        digest = hashlib.sha256()
        total = 0
        try:
            with temporario.open('wb') as f:
                for bloco in r.iter_content(1 << 20):
                    if bloco:
                        f.write(bloco)
                        digest.update(bloco)
                        total += len(bloco)
            if total == 0 or not zipfile.is_zipfile(temporario):
                raise RuntimeError("COTAHIST retornou arquivo vazio ou nao ZIP")
            hexdigest = digest.hexdigest()
            nome_versao = f"{destino.stem}.{hexdigest[:16]}{destino.suffix}"
            arquivo_versao = versoes / nome_versao
            if arquivo_versao.exists():
                temporario.unlink()
            else:
                os.replace(temporario, arquivo_versao)
        finally:
            temporario.unlink(missing_ok=True)

    agora = datetime.now(timezone.utc).isoformat()
    manifesto = {
        'fonte': 'B3 COTAHIST', 'url': url,
        'verificado_em_utc': agora, 'capturado_em_utc': agora,
        'http_status_ultima_verificacao': r.status_code,
        'etag': r.headers.get('ETag'), 'last_modified': r.headers.get('Last-Modified'),
        'bytes': total, 'sha256': hexdigest,
        'arquivo_atual': str(arquivo_versao.relative_to(destino.parent)),
    }
    tmp_meta = meta_path.with_suffix(meta_path.suffix + '.tmp')
    tmp_meta.write_text(json.dumps(manifesto, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp_meta, meta_path)
    return str(arquivo_versao)


def parsear(caminho, apenas_vista=True):
    """Le o zip e devolve DataFrame com as cotacoes."""

    with zipfile.ZipFile(caminho) as z:
        nome = z.namelist()[0]
        bruto = z.read(nome).decode('latin-1')

    linhas = []
    for linha in bruto.splitlines():
        if len(linha) < 245 or linha[0:2] != '01':
            continue                                    # header/trailer
        if apenas_vista and linha[24:27] != '010':
            continue                                    # so mercado a vista
        if linha[10:12] != '02':
            continue                                    # so lote padrao
        linhas.append(linha)

    dados = {}
    for nome_campo, (i, j, div) in CAMPOS.items():
        col = [l[i:j] for l in linhas]
        if div:
            dados[nome_campo] = pd.to_numeric(col, errors='coerce') / div
        elif nome_campo in ('data', 'ticker'):
            dados[nome_campo] = [c.strip() for c in col]
        else:
            dados[nome_campo] = pd.to_numeric(col, errors='coerce')

    df = pd.DataFrame(dados)
    df['data'] = pd.to_datetime(df['data'], format='%Y%m%d').dt.date

    # fator de cotacao: 1000 significa preco por lote de mil
    df['fatcot'] = df['fatcot'].fillna(1).replace(0, 1)
    for c in ['abertura', 'maxima', 'minima', 'media', 'fechamento']:
        df[c] = df[c] / df['fatcot']

    return df.drop(columns=['tipreg', 'codbdi', 'tpmerc'])


def parsear_periodo(caminho, data_inicio, data_fim):
    """Lê apenas o intervalo útil ao sinal, sem descompactar o ano na memória.

    As colunas são suficientes para preço de fechamento, liquidez e calendário.
    Mantém o mesmo filtro do leitor completo: lote padrão no mercado à vista.
    """
    inicio = pd.Timestamp(data_inicio).strftime('%Y%m%d')
    fim = pd.Timestamp(data_fim).strftime('%Y%m%d')
    linhas = []
    with zipfile.ZipFile(caminho) as arquivo:
        nome = arquivo.namelist()[0]
        with arquivo.open(nome) as fluxo:
            for bruto in fluxo:
                if len(bruto) < 245 or bruto[:2] != b'01':
                    continue
                data = bruto[2:10].decode('ascii')
                if not (inicio <= data <= fim) or bruto[10:12] != b'02' or bruto[24:27] != b'010':
                    continue
                fator = int(bruto[210:217]) or 1
                linhas.append((pd.Timestamp(data), bruto[12:24].decode('latin-1').strip(),
                               int(bruto[108:121]) / (100 * fator),
                               int(bruto[170:188]) / 100))
    return pd.DataFrame(linhas, columns=['data', 'ticker', 'fechamento', 'volume_negociado'])


if __name__ == "__main__":
    import sys
    ano = int(sys.argv[1]) if len(sys.argv) > 1 else 2026

    print(f"=== COTAHIST {ano} ===")
    caminho = baixar(ano)
    df = parsear(caminho)

    print(f"\nlinhas         : {len(df):,}")
    print(f"tickers        : {df['ticker'].nunique():,}")
    print(f"pregoes        : {df['data'].nunique()}")
    print(f"periodo        : {df['data'].min()} a {df['data'].max()}")

    ultima = df[df['data'] == df['data'].max()]
    liq = ultima[ultima['volume_negociado'] > 2_000_000]
    print(f"\nno ultimo pregao ({df['data'].max()}):")
    print(f"  tickers negociados        : {len(ultima):,}")
    print(f"  com volume > R$ 2 milhoes : {len(liq):,}")

    print(f"\nmaiores volumes do ultimo pregao:")
    top = ultima.nlargest(8, 'volume_negociado')[['ticker', 'fechamento', 'volume_negociado']]
    for _, r in top.iterrows():
        print(f"  {r['ticker']:8s} R$ {r['fechamento']:8.2f}   vol R$ {r['volume_negociado']:15,.2f}")
