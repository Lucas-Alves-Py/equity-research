import io
import json
import zipfile

from carteira import cotahist
from fontes import cvm


class FakeResponse:
    def __init__(self, body=b'dados', status=200, headers=None):
        self.body = body
        self.content = body
        self.status_code = status
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f'HTTP {self.status_code}')

    def iter_content(self, tamanho):
        yield self.body


def zip_bytes(texto):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as arquivo:
        arquivo.writestr('amostra.txt', texto)
    return buffer.getvalue()


def test_cvm_guarda_snapshots_e_reutiliza_cache_dentro_do_ttl(tmp_path, monkeypatch):
    destino = tmp_path / 'itr_2026.zip'
    chamadas = []
    corpo = zip_bytes('versao 1')

    def get(url, headers, timeout):
        chamadas.append((url, headers))
        return FakeResponse(corpo, headers={'ETag': 'v1'})

    monkeypatch.setattr(cvm.requests, 'get', get)
    primeira = cvm._baixar_com_manifesto('https://cvm.test/itr.zip', str(destino), 3600)
    segunda = cvm._baixar_com_manifesto('https://cvm.test/itr.zip', str(destino), 3600)

    assert primeira == segunda
    assert len(chamadas) == 1
    assert json.loads(destino.with_suffix('.zip.meta.json').read_text())['sha256']


def test_cvm_preserva_versao_antiga_quando_arquivo_muda(tmp_path, monkeypatch):
    destino = tmp_path / 'itr_2026.zip'
    respostas = [zip_bytes('versao 1'), zip_bytes('versao 2')]
    monkeypatch.setattr(cvm.requests, 'get', lambda *a, **k: FakeResponse(respostas.pop(0)))

    primeira = cvm._baixar_com_manifesto('https://cvm.test/itr.zip', str(destino), 0)
    segunda = cvm._baixar_com_manifesto('https://cvm.test/itr.zip', str(destino), 0,
                                        atualizar=True)

    assert primeira != segunda
    assert open(primeira, 'rb').read() == zip_bytes('versao 1')
    assert open(segunda, 'rb').read() == zip_bytes('versao 2')


def test_cotahist_atualiza_por_hash_sem_apagar_snapshot_anterior(tmp_path, monkeypatch):
    destino = tmp_path / 'COTAHIST_A2026.ZIP'
    respostas = [zip_bytes('versao 1'), zip_bytes('versao 2')]
    monkeypatch.setattr(cotahist.requests, 'get', lambda *a, **k: FakeResponse(respostas.pop(0)))

    primeira = cotahist.baixar(2026, str(destino), atualizar=True)
    segunda = cotahist.baixar(2026, str(destino), atualizar=True)

    assert primeira != segunda
    assert zipfile.is_zipfile(primeira)
    assert zipfile.is_zipfile(segunda)
    assert len(list((tmp_path / 'versoes').glob('COTAHIST_A2026.*.ZIP'))) == 2


def test_cvm_usa_etag_para_confirmar_que_snapshot_nao_mudou(tmp_path, monkeypatch):
    destino = tmp_path / 'itr_2026.zip'
    chamadas = []
    corpo = zip_bytes('versao atual')

    def get(url, headers, timeout):
        chamadas.append(headers)
        if len(chamadas) == 1:
            return FakeResponse(corpo, headers={'ETag': 'v1'})
        return FakeResponse(b'', status=304)

    monkeypatch.setattr(cvm.requests, 'get', get)
    primeira = cvm._baixar_com_manifesto('https://cvm.test/itr.zip', str(destino), 0)
    segunda = cvm._baixar_com_manifesto('https://cvm.test/itr.zip', str(destino), 0)

    assert primeira == segunda
    assert chamadas[1]['If-None-Match'] == 'v1'
    meta = json.loads(destino.with_suffix('.zip.meta.json').read_text())
    assert meta['http_status_ultima_verificacao'] == 304


def test_cotahist_usa_last_modified_para_revalidar(tmp_path, monkeypatch):
    destino = tmp_path / 'COTAHIST_A2026.ZIP'
    corpo = zip_bytes('versao atual')
    chamadas = []

    def get(url, headers, timeout, stream):
        chamadas.append(headers)
        if len(chamadas) == 1:
            return FakeResponse(corpo, headers={'Last-Modified': 'Sat, 26 Sep 2026 00:53:06 GMT'})
        return FakeResponse(b'', status=304)

    monkeypatch.setattr(cotahist.requests, 'get', get)
    primeira = cotahist.baixar(2026, str(destino), atualizar=True)
    segunda = cotahist.baixar(2026, str(destino), atualizar=True)

    assert primeira == segunda
    assert 'If-Modified-Since' in chamadas[1]
    meta = json.loads(destino.with_suffix('.ZIP.meta.json').read_text())
    assert meta['http_status_ultima_verificacao'] == 304


def _dre(linhas):
    import pandas as pd
    return pd.DataFrame([{'CNPJ_CIA': cnpj, 'DT_REFER': '2024-09-30', 'VERSAO': '1',
                          'ORDEM_EXERC': cvm.CORRENTE, 'CD_CONTA': conta,
                          'VL_CONTA': valor, 'ESCALA_MOEDA': 'MIL',
                          'DT_INI_EXERC': '2024-07-01', 'DT_FIM_EXERC': '2024-09-30'}
                         for cnpj, conta, valor in linhas])


def test_consolidado_todo_zerado_cede_lugar_ao_individual(monkeypatch):
    # TIMS3 em 2024: formulario consolidado entregue com todas as contas
    # zeradas; os numeros reais estao so no individual.
    tabelas = {
        'DRE_con': _dre([('TIM', '3.01', 0.0), ('TIM', '3.05', 0.0),
                         ('REAL', '3.01', 900.0), ('REAL', '3.05', 0.0)]),
        'DRE_ind': _dre([('TIM', '3.01', 5000.0), ('TIM', '3.05', 1431.0),
                         ('REAL', '3.01', 700.0), ('REAL', '3.05', 50.0)]),
    }
    monkeypatch.setattr(cvm, 'tabela', lambda doc, ano, nome: tabelas[nome].copy())

    ebit = cvm._contas('ITR', 2024, 'DRE', [cvm.EBIT]).set_index('cnpj')['valor']

    assert ebit['TIM'] == 1_431_000.0
    # Consolidado com conteudo continua preferido, mesmo com EBIT zero.
    assert ebit['REAL'] == 0.0
