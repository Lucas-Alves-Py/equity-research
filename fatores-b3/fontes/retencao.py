"""Retenção das versões baixadas da B3 e da CVM.

Cada atualização do COTAHIST anual (~85 MB) e do ITR da CVM (~20 MB) virava
uma cópia inteira em `versoes/`: mais de 20 GB por ano, quase tudo repetido.
Ficam a versão atual e as fixadas por decisões mensais (`FIXADAS.txt`); uma
versão substituída e não fixada vai para a Lixeira do Windows, recuperável
até a Lixeira ser esvaziada.
"""

from __future__ import annotations

import ctypes
import json
import os
import warnings
from ctypes import wintypes
from pathlib import Path

FIXADAS = 'FIXADAS.txt'


def fixadas(pasta_versoes: Path) -> set[str]:
    arquivo = Path(pasta_versoes) / FIXADAS
    if not arquivo.exists():
        return set()
    return {linha.strip() for linha in arquivo.read_text(encoding='utf-8').splitlines() if linha.strip()}


def fixar_versoes_em_uso(pastas) -> list[str]:
    """Fixa a versão atual de cada fonte (o `arquivo_atual` dos manifestos)."""
    fixadas_agora = []
    for pasta in map(Path, pastas):
        versoes = pasta / 'versoes'
        atuais = set()
        for meta in pasta.glob('*.meta.json'):
            try:
                atual = json.loads(meta.read_text(encoding='utf-8')).get('arquivo_atual')
            except (OSError, ValueError):
                continue
            if atual and (pasta / atual).exists():
                atuais.add(Path(atual).name)
        novas = sorted(atuais - fixadas(versoes))
        if novas:
            versoes.mkdir(parents=True, exist_ok=True)
            with (versoes / FIXADAS).open('a', encoding='utf-8') as f:
                f.writelines(f'{nome}\n' for nome in novas)
        fixadas_agora += [str(versoes / nome) for nome in sorted(atuais)]
    return fixadas_agora


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [('hwnd', wintypes.HWND), ('wFunc', wintypes.UINT),
                ('pFrom', wintypes.LPCWSTR), ('pTo', wintypes.LPCWSTR),
                ('fFlags', ctypes.c_uint16), ('fAnyOperationsAborted', wintypes.BOOL),
                ('hNameMappings', ctypes.c_void_p), ('lpszProgressTitle', wintypes.LPCWSTR)]


def para_lixeira(caminho: Path) -> None:
    """Move para a Lixeira; nunca apaga em definitivo sem aviso do Windows."""
    if os.name != 'nt':
        # Fora do Windows só roda a rotina no GitHub Actions, cujo disco é
        # descartado ao fim de cada execução: não há Lixeira nem o que guardar.
        Path(caminho).unlink()
        return
    FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION = 3, 0x4, 0x10
    FOF_ALLOWUNDO, FOF_NOERRORUI, FOF_WANTNUKEWARNING = 0x40, 0x400, 0x4000
    op = _SHFILEOPSTRUCTW(None, FO_DELETE, str(Path(caminho).resolve()) + '\0', None,
                          FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT |
                          FOF_NOERRORUI | FOF_WANTNUKEWARNING, False, None, None)
    codigo = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if codigo != 0 or op.fAnyOperationsAborted:
        raise OSError(f'não foi possível mover para a Lixeira (código {codigo}): {caminho}')


def aposentar(versao_antiga, versao_nova, lixeira=None) -> bool:
    """Manda para a Lixeira a versão substituída, se nenhuma decisão a fixou."""
    antiga = Path(versao_antiga)
    if antiga.resolve() == Path(versao_nova).resolve() or not antiga.exists():
        return False
    if antiga.name in fixadas(antiga.parent):
        return False
    try:
        (lixeira or para_lixeira)(antiga)
        return True
    except OSError as exc:
        warnings.warn(str(exc), RuntimeWarning, stacklevel=2)
        return False
