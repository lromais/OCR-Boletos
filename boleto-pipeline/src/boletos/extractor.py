"""Encontra linhas digitáveis / códigos de barras em texto vindo de OCR.

O OCR quebra a linha em blocos ("00190.50095 40144.816069 ..."), troca O por 0,
I/l por 1 etc. A estratégia é: normalizar, pegar trechos longos de dígitos com
separadores e testar janelas de 44/47/48 dígitos contra os DVs. Só sobrevive
o que fecha a conta, então ruído não vira boleto.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Iterable, Optional

from .barcode import ResultadoBoleto, validar

_TRADUCAO_OCR = str.maketrans({"O": "0", "o": "0", "D": "0", "I": "1", "l": "1",
                               "|": "1", "S": "5", "B": "8", "Z": "2"})
# Trecho com pelo menos ~40 dígitos permeados de espaço/ponto/hífen
_TRECHO = re.compile(r"[0-9OoDIl|SBZ][0-9OoDIl|SBZ.\s\-]{38,90}[0-9]")


def _normaliza(trecho: str) -> str:
    return re.sub(r"\D", "", trecho.translate(_TRADUCAO_OCR))


def candidatos(texto: str) -> Iterable[str]:
    """Gera janelas de dígitos plausíveis.

    * 47/48 dígitos (linha digitável): janela deslizante dentro do trecho.
    * 44 dígitos (código de barras impresso): só se o trecho tiver exatamente 44
      dígitos -- uma janela de 44 dentro de uma linha de 47 valida por acaso
      ~10% das vezes (DV mod 11), então não deslizamos.
    """
    vistos: set[str] = set()
    texto = texto or ""
    for linha in texto.splitlines() + [" ".join(texto.split())]:
        for m in _TRECHO.finditer(linha):
            dig = _normaliza(m.group())
            janelas = [dig] if len(dig) == 44 else [
                dig[i:i + tam] for tam in (47, 48) for i in range(0, len(dig) - tam + 1)]
            for j in janelas:
                if j not in vistos:
                    vistos.add(j)
                    yield j


def extrair_boletos(texto: str, referencia: Optional[date] = None) -> list[ResultadoBoleto]:
    """Retorna boletos válidos (deduplicados pelo código de barras)."""
    achados: dict[str, ResultadoBoleto] = {}
    for c in candidatos(texto):
        r = validar(c, referencia)
        if r.valido and r.codigo_barras not in achados:
            achados[r.codigo_barras] = r
    return list(achados.values())


def melhor_candidato_invalido(texto: str) -> Optional[ResultadoBoleto]:
    """Quando nada valida, devolve o candidato mais 'parecido' para revisão humana."""
    for c in candidatos(texto):
        if len(c) in (47, 48):
            return validar(c)
    return None
