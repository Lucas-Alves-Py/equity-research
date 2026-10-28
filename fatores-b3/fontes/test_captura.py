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


def test_cvm_preserva_versao_fixada_quando_arquivo_muda(tmp_path, monkeypatch):
    from fontes import retencao
    destino = tmp_path / 'itr_2026.zip'
    respostas = [zip_bytes('versao 1'), zip_bytes('versao 2')]
    monkeypatch.setattr(cvm.requests, 'get', lambda *a, **k: FakeResponse(respostas.pop(0)))
    monkeypatch.setattr(retencao, 'para_lixeira', lambda p: (_ for _ in ()).throw(AssertionError(p)))

    primeira = cvm._baixar_com_manifesto('https://cvm.test/itr.zip', str(destino), 0)
    retencao.fixar_versoes_em_uso([tmp_path])  # uma decisao usou a versao 1
    segunda = cvm._baixar_com_manifesto('https://cvm.test/itr.zip', str(destino), 0,
                                        atualizar=True)

    assert primeira != segunda
    assert open(primeira, 'rb').read() == zip_bytes('versao 1')
    assert open(segunda, 'rb').read() == zip_bytes('versao 2')


def test_cotahist_atualiza_por_hash_e_manda_versao_nao_fixada_para_lixeira(tmp_path, monkeypatch):
    from fontes import retencao
    destino = tmp_path / 'COTAHIST_A2026.ZIP'
    respostas = [zip_bytes('versao 1'), zip_bytes('versao 2')]
    monkeypatch.setattr(cotahist.requests, 'get', lambda *a, **k: FakeResponse(respostas.pop(0)))
    lixeira = []
    monkeypatch.setattr(retencao, 'para_lixeira', lixeira.append)

    primeira = cotahist.baixar(2026, str(destino), atualizar=True)
    segunda = cotahist.baixar(2026, str(destino), atualizar=True)

    assert primeira != segunda
    assert zipfile.is_zipfile(segunda)
    # Nenhuma decisao fixou a versao 1: ela sai; o arquivo do ano fica so com a atual.
    assert [str(p) for p in lixeira] == [primeira]


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


def test_q4_de_exercicio_que_fecha_em_marco(monkeypatch):
    # SMTO3/JALL3: exercicio abr-mar. ITR de dezembro traz 9 meses desde
    # abril; Q4 fiscal (jan-mar) = DFP anual menos esse acumulado.
    import pandas as pd

    def linha(doc, ref, ini, fim, valor):
        return {'cnpj': 'SMTO', 'dt_refer': pd.Timestamp(ref), 'versao': '1',
                'conta': cvm.EBIT, 'valor': valor, 'ini': pd.Timestamp(ini),
                'fim': pd.Timestamp(fim), 'doc': doc}
    itr = [linha('ITR', '2024-06-30', '2024-04-01', '2024-06-30', 100.0),
           linha('ITR', '2024-09-30', '2024-07-01', '2024-09-30', 110.0),
           linha('ITR', '2024-09-30', '2024-04-01', '2024-09-30', 210.0),
           linha('ITR', '2024-12-31', '2024-10-01', '2024-12-31', 120.0),
           linha('ITR', '2024-12-31', '2024-04-01', '2024-12-31', 330.0)]
    dfp = [linha('DFP', '2025-03-31', '2024-04-01', '2025-03-31', 460.0)]

    def contas(doc, ano, tab, codigos, as_of=None):
        fonte = itr if doc == 'ITR' else dfp
        return pd.DataFrame([x for x in fonte if x['dt_refer'].year == ano]).drop(
            columns='doc', errors='ignore')
    monkeypatch.setattr(cvm, '_contas', contas)

    q = cvm.ebit_trimestral([2024, 2025]).set_index('dt_refer')['ebit']

    assert q[pd.Timestamp('2025-03-31')] == 130.0
    assert q[pd.Timestamp('2024-12-31')] == 120.0


def test_proventos_consulta_nome_sem_barra(monkeypatch):
    # "AMBEV S/A", "CURY S/A" e "KLABIN S/A": com a barra a B3 devolvia zero
    # proventos, e o momentum ignorava os dividendos dessas empresas.
    from fontes import b3
    pedidos = []

    def chamar(rota, params, timeout=60):
        pedidos.append(params['tradingName'])
        return {'results': [], 'page': {'totalPages': 1}}
    monkeypatch.setattr(b3, '_chamar', chamar)
    b3.proventos('KLABIN S/A', pausa=0)
    assert pedidos == ['KLABIN SA']


def test_eventos_em_acoes_de_unit_usam_isin_cda():
    import pandas as pd
    from fontes import b3
    eventos = pd.DataFrame([
        {'ultimo_com': pd.Timestamp('2025-12-17'), 'aprovado_em': pd.Timestamp('2025-11-01'),
         'isin': 'BRKLBNCDAM18', 'tipo': 'BONIFICACAO', 'fator_b3': 1.0},
        {'ultimo_com': pd.Timestamp('2025-12-17'), 'aprovado_em': pd.Timestamp('2025-11-01'),
         'isin': 'BRKLBNACNOR2', 'tipo': 'BONIFICACAO', 'fator_b3': 1.0}])
    unit = b3.proventos_ativos('KLBN11', empresa_b3={'issuingCompany': 'KLBN'},
                               eventos_empresa=eventos)
    assert unit['isin'].tolist() == ['BRKLBNCDAM18']


def test_versao_substituida_vai_para_lixeira_salvo_se_fixada(tmp_path):
    from fontes import retencao
    versoes = tmp_path / 'versoes'
    versoes.mkdir()
    for nome in ('A2026.aaa.ZIP', 'A2026.bbb.ZIP', 'A2026.ccc.ZIP'):
        (versoes / nome).write_bytes(b'zip')
    (tmp_path / 'A2026.ZIP.meta.json').write_text(
        json.dumps({'arquivo_atual': 'versoes/A2026.bbb.ZIP'}), encoding='utf-8')
    lixo = []
    # Decisao do mes fixa a versao em uso (bbb); depois chega ccc.
    retencao.fixar_versoes_em_uso([tmp_path])
    assert not retencao.aposentar(versoes / 'A2026.bbb.ZIP', versoes / 'A2026.ccc.ZIP', lixo.append)
    assert retencao.aposentar(versoes / 'A2026.aaa.ZIP', versoes / 'A2026.ccc.ZIP', lixo.append)
    assert [p.name for p in lixo] == ['A2026.aaa.ZIP']
    assert retencao.fixadas(versoes) == {'A2026.bbb.ZIP'}


def test_provento_de_pna_e_pnb_casa_com_o_final_do_ticker():
    # A B3 publica a classe como PNA/PNB; o mapa antigo procurava 'PN' e a
    # UNIP6 ficou sem os dividendos de 12/2025 no momentum.
    import pandas as pd
    from fontes import b3
    prov = pd.DataFrame([
        {'ultimo_com': pd.Timestamp('2025-12-05'), 'aprovado_em': pd.Timestamp('2025-12-02'),
         'classe': classe, 'tipo': 'DIVIDENDO', 'pct': pct}
        for classe, pct in [('ON', 8.8), ('PNA', 8.4), ('PNB', 9.3)]])
    assert b3.fatores(prov, 'UNIP6', as_of='2025-12-10').iloc[0]['fator'] == 1 - 9.3 / 100
    assert b3.fatores(prov, 'UNIP5', as_of='2025-12-10').iloc[0]['fator'] == 1 - 8.4 / 100


def test_capital_cvm_com_mesma_versao_recebida_duas_vezes(monkeypatch):
    # A CVM registra às vezes a mesma versão do ITR com dois recebimentos
    # (02.291.077/0001-93, 2T21). Vale o último recebido antes do corte,
    # como em `_contas`; antes a junção quebrava por chave duplicada.
    import pandas as pd
    from fontes import cvm, fundamentos
    capital = pd.DataFrame([{'CNPJ_CIA': 'X', 'DT_REFER': '2021-06-30', 'VERSAO': 1,
                             'QT_ACAO_ORDIN_CAP_INTEGR': 100, 'QT_ACAO_PREF_CAP_INTEGR': 0,
                             'QT_ACAO_ORDIN_TESOURO': 0, 'QT_ACAO_PREF_TESOURO': 0}])
    protocolo = pd.DataFrame({'cnpj': 'X', 'dt_refer': pd.Timestamp('2021-06-30'), 'versao': 1,
                              'dt_receb': pd.to_datetime(['2021-08-13', '2021-11-10']), 'doc': 'ITR'})
    monkeypatch.setattr(cvm, 'tabela', lambda doc, ano, sufixo='': capital if doc == 'ITR' else capital.iloc[:0])
    monkeypatch.setattr(cvm, 'protocolo', lambda anos, strict=False: protocolo)
    d = fundamentos.capital_cvm([2021], '2021-09-30')
    assert d['dt_receb'].tolist() == [pd.Timestamp('2021-08-13')]
    assert fundamentos.capital_cvm([2021], '2021-12-31')['dt_receb'].tolist() == [pd.Timestamp('2021-11-10')]


def test_quantidade_de_acoes_usa_fre_para_achar_a_unidade(monkeypatch):
    # O ITR não diz se a composição está em ações ou em milhares de ações;
    # o FRE está sempre em ações. Sem FRE compatível, o emissor sai.
    import pandas as pd
    from fontes import cvm, fundamentos
    capital = pd.DataFrame({'cnpj': ['MIL', 'UNI', 'SEM'], 'dt_refer': pd.Timestamp('2024-09-30'),
                            'dt_receb': pd.Timestamp('2024-11-10'),
                            'on_integralizadas_bruto': [1_000.0, 1_000_000.0, 5.0],
                            'pn_integralizadas_bruto': [500.0, 0.0, 0.0]})
    fre = pd.DataFrame({'CNPJ_Companhia': ['MIL', 'UNI', 'SEM'], 'Versao': 1,
                        'Tipo_Capital': 'Capital Integralizado',
                        'Quantidade_Total_Acoes': [1_500_000, 1_000_000, 999_999_999]})
    monkeypatch.setattr(fundamentos, 'capital_cvm', lambda anos, corte: capital)
    monkeypatch.setattr(cvm, 'tabela', lambda doc, ano, sufixo='': fre)
    d = fundamentos.quantidade_acoes_cvm([2024], '2024-12-30').set_index('cnpj')
    assert d.loc['MIL', ['acoes_on', 'acoes_pn']].tolist() == [1_000_000.0, 500_000.0]
    assert d.loc['UNI', 'acoes_on'] == 1_000_000.0
    assert 'SEM' not in d.index


def test_troca_de_exercicio_tira_a_empresa_do_ttm_sem_abortar_a_data():
    # 42.310.775/0001-03 entregou DFP em 31/07/2021 e ITR em 30/09/2021: duas
    # datas no mesmo trimestre. A empresa sai; as outras seguem.
    import pandas as pd
    import pytest
    from fontes.fundamentos import _ttm_contiguo
    datas = pd.to_datetime(['2021-03-31', '2021-06-30', '2021-09-30', '2021-12-31'])
    normal = pd.DataFrame({'cnpj': 'A', 'dt_refer': datas, 'dt_receb': datas + pd.Timedelta(days=40),
                           'ebit': [1.0, 2.0, 3.0, 4.0]})
    troca = pd.DataFrame({'cnpj': 'B', 'dt_refer': pd.to_datetime(['2021-07-31', '2021-09-30']),
                          'dt_receb': pd.to_datetime(['2021-11-04', '2022-03-16']), 'ebit': [5.0, 6.0]})
    ttm = _ttm_contiguo(pd.concat([normal, troca]), 'ebit', pd.Timestamp('2022-06-01'))
    assert ttm['cnpj'].tolist() == ['A'] and ttm['ebit_ttm'].iloc[0] == 10.0
    repetida = pd.concat([normal, normal.tail(1)])
    with pytest.raises(ValueError, match='duplicado'):
        _ttm_contiguo(repetida, 'ebit', pd.Timestamp('2022-06-01'))


def test_evento_inferido_exige_mudanca_de_distribuicao_e_salto_compativel():
    # KEPL3 06/05/2022: DISMES 054 -> 055 e preço de 55,15 para 19,04 (3:1).
    # Emissão de ações muda a quantidade, mas não faz o preço saltar.
    import pandas as pd
    from fontes import eventos_cvm
    datas = pd.bdate_range('2022-05-02', periods=6)
    precos = pd.DataFrame({'data': datas, 'ticker': 'KEPL3',
                           'fechamento': [57.5, 57.6, 58.5, 55.15, 19.04, 18.08],
                           'dismes': [54, 54, 54, 54, 55, 55]})
    candidato = lambda razao: pd.Series({'inicio': datas[0], 'fim': datas[-1], 'razao': razao})
    achado = eventos_cvm.localizar(precos, candidato(3.0))
    assert achado['ex'] == datas[4] and achado['ultimo_com'] == datas[3]
    assert eventos_cvm.localizar(precos, candidato(1.104)) is None
    sem_dismes = precos.assign(dismes=54)
    assert eventos_cvm.localizar(sem_dismes, candidato(3.0)) is None


def test_escala_aceita_fre_anterior_a_desdobramento_e_capital_subscrito(monkeypatch):
    # KEPL3 em 08/2022: ITR em milhares já com o split 3:1, FRE ainda com a
    # quantidade antiga (razão 333). TIM só declara capital subscrito no FRE.
    import pandas as pd
    from fontes import cvm, fundamentos
    capital = pd.DataFrame({'cnpj': ['KEPL', 'TIM'], 'dt_refer': pd.Timestamp('2022-06-30'),
                            'dt_receb': pd.Timestamp('2022-08-01'),
                            'on_integralizadas_bruto': [89_860.0, 2_420_804_398.0],
                            'pn_integralizadas_bruto': [0.0, 0.0]})
    fre = pd.DataFrame({'CNPJ_Companhia': ['KEPL', 'TIM'], 'Versao': 1,
                        'Tipo_Capital': ['Capital Integralizado', 'Capital Subscrito'],
                        'Quantidade_Total_Acoes': [29_953_355, 2_420_804_398]})
    monkeypatch.setattr(fundamentos, 'capital_cvm', lambda anos, corte: capital)
    monkeypatch.setattr(cvm, 'tabela', lambda doc, ano, sufixo='': fre)
    d = fundamentos.quantidade_acoes_cvm([2022], '2022-08-10').set_index('cnpj')
    assert d.loc['KEPL', 'acoes_on'] == 89_860_000.0
    assert d.loc['TIM', 'acoes_on'] == 2_420_804_398.0


def test_cotahist_mantem_acoes_em_recuperacao(tmp_path):
    # Com o pedido de recuperação a B3 troca o CODBDI de '02' para '08'
    # (ETER3, FHER3); filtrar só '02' cortava a série no pedido.
    import zipfile
    import pandas as pd
    from carteira import cotahist

    def linha(codbdi, ticker):
        l = list(' ' * 245)
        campos = {0: '01', 2: '20240102', 10: codbdi, 12: ticker.ljust(12), 24: '010',
                  108: '0000000001000', 170: '000000000000100000', 210: '0000001'}
        for i, v in campos.items():
            l[i:i + len(v)] = v
        return ''.join(l)
    arquivo = tmp_path / 'COTAHIST_A2024.ZIP'
    with zipfile.ZipFile(arquivo, 'w') as z:
        z.writestr('COTAHIST_A2024.TXT', '\n'.join([linha('02', 'PETR4'), linha('08', 'ETER3'),
                                                     linha('12', 'HGLG11')]))
    d = cotahist.parsear_periodo(arquivo, '2024-01-01', '2024-01-31')
    assert sorted(d['ticker']) == ['ETER3', 'PETR4']
    assert sorted(cotahist.parsear(arquivo)['ticker']) == ['ETER3', 'PETR4']


def test_capital_do_fre_decide_desdobramento_pela_quantidade(monkeypatch):
    # O FRE guardado é a última versão do ano. A Tecnisa reenviou o FRE 2019
    # depois do grupamento de 05/05/2020 sem mudar o capital; a TOTVS também,
    # depois do desdobramento de 27/04/2020. Decidir pela data da versão
    # desfazia eventos que o número guardado não tinha.
    import pandas as pd
    from fontes.fundamentos import quantidade_acoes_fre

    def capital(cnpj, qtd):
        return {'CNPJ_Companhia': cnpj, 'Data_Referencia': '2019-01-01', 'Versao': 5,
                'Tipo_Capital': 'Capital Integralizado', 'Data_Autorizacao_Aprovacao': '2018-01-01',
                'Quantidade_Acoes_Ordinarias': qtd, 'Quantidade_Acoes_Preferenciais': 0}

    def evento(cnpj, data, antes, depois):
        return {'CNPJ_Companhia': cnpj, 'Data_Aprovacao': data, 'Tipo_Evento': 'X',
                'Quantidade_Acoes_Ordinarias_Antes_Aprovacao': antes,
                'Quantidade_Acoes_Preferenciais_Antes_Aprovacao': 0,
                'Quantidade_Total_Acoes_Antes_Aprovacao': antes,
                'Quantidade_Acoes_Ordinarias_Depois_Aprovacao': depois,
                'Quantidade_Acoes_Preferenciais_Depois_Aprovacao': 0,
                'Quantidade_Total_Acoes_Depois_Aprovacao': depois}

    tabelas = {
        '': pd.DataFrame({'CNPJ_CIA': ['A', 'B', 'C'], 'VERSAO': [5, 5, 5],
                          'DT_RECEB': ['2019-05-31', '2019-05-31', '2019-05-31']}),
        'capital_social': pd.DataFrame([capital('A', 736_192_307),      # antes do grupamento
                                        capital('B', 577_913_181),      # já depois do desdobramento
                                        capital('C', 17_637_203)]),     # antes de um desdobramento já ocorrido
        'capital_social_desdobramento': pd.DataFrame([
            evento('A', '2020-05-05', 736_192_307, 73_619_230),
            evento('B', '2020-04-27', 192_637_727, 577_913_181),
            evento('C', '2019-09-12', 17_637_203, 70_548_812)]),
    }
    monkeypatch.setattr(cvm, 'tabela', lambda doc, ano, sufixo='': tabelas[sufixo].copy())
    d = quantidade_acoes_fre('2019-12-05').set_index('cnpj')['acoes_on']
    assert d['A'] == 736_192_307          # evento depois do corte que o número não tinha: fica
    assert round(d['B']) == 192_637_727   # evento depois do corte que o número tinha: desfaz
    assert round(d['C']) == 70_548_812    # evento antes do corte que o número não tinha: aplica
