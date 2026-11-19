"""Validação de boletos (padrão FEBRABAN).

Suporta:
  * Boleto de cobrança bancária  -> código de barras 44 dígitos / linha digitável 47
  * Boleto de arrecadação/convênio (contas de consumo, tributos; começa com '8')
                                  -> código de barras 44 dígitos / linha digitável 48

Tudo puro Python, sem dependências, para rodar em Lambda/ECS/testes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Optional

# Fator de vencimento: base 07/10/1997. Chegou a 9999 em 21/02/2025 e
# reiniciou em 1000 no dia 22/02/2025 (novo ciclo FEBRABAN).
_BASE_CICLO_1 = date(1997, 10, 7)
_BASE_CICLO_2 = date(2025, 2, 22)  # fator 1000 do ciclo 2


# --------------------------------------------------------------------------- #
# Módulos de cálculo
# --------------------------------------------------------------------------- #
def mod10(num: str) -> int:
    total, peso = 0, 2
    for d in reversed(num):
        p = int(d) * peso
        total += p // 10 + p % 10
        peso = 1 if peso == 2 else 2
    return (10 - total % 10) % 10


def _mod11_soma(num: str) -> int:
    total, peso = 0, 2
    for d in reversed(num):
        total += int(d) * peso
        peso = 2 if peso == 9 else peso + 1
    return total % 11


def mod11_cobranca(num: str) -> int:
    """DV geral do boleto bancário: resultados 0, 10 e 11 viram 1."""
    dv = 11 - _mod11_soma(num)
    return 1 if dv in (0, 10, 11) else dv


def mod11_arrecadacao(num: str) -> int:
    """DV de arrecadação: restos 0 e 1 viram 0."""
    dv = 11 - _mod11_soma(num)
    return 0 if dv in (10, 11) else dv


# --------------------------------------------------------------------------- #
# Resultado
# --------------------------------------------------------------------------- #
@dataclass
class ResultadoBoleto:
    entrada: str
    valido: bool
    tipo: Optional[str] = None             # "bancario" | "arrecadacao"
    codigo_barras: Optional[str] = None    # 44 dígitos
    linha_digitavel: Optional[str] = None  # 47 ou 48 dígitos
    banco: Optional[str] = None
    valor: Optional[Decimal] = None
    vencimento: Optional[date] = None
    erros: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["valor"] = str(self.valor) if self.valor is not None else None
        d["vencimento"] = self.vencimento.isoformat() if self.vencimento else None
        return d


def somente_digitos(s: str) -> str:
    return re.sub(r"\D", "", s or "")


# --------------------------------------------------------------------------- #
# Vencimento
# --------------------------------------------------------------------------- #
def fator_para_data(fator: int, referencia: Optional[date] = None) -> Optional[date]:
    """Converte fator em data, escolhendo o ciclo mais próximo da referência
    (normalmente a data do e-mail)."""
    if fator == 0:
        return None
    referencia = referencia or date.today()
    candidatos = [_BASE_CICLO_1 + timedelta(days=fator)]
    if fator >= 1000:
        candidatos.append(_BASE_CICLO_2 + timedelta(days=fator - 1000))
    return min(candidatos, key=lambda d: abs((d - referencia).days))


# --------------------------------------------------------------------------- #
# Cobrança bancária
# --------------------------------------------------------------------------- #
def linha47_para_barras(linha: str) -> str:
    return linha[0:4] + linha[32] + linha[33:47] + linha[4:9] + linha[10:20] + linha[21:31]


def barras_para_linha47(barras: str) -> str:
    livre = barras[19:44]
    c1 = barras[0:4] + livre[0:5]
    c2 = livre[5:15]
    c3 = livre[15:25]
    return (c1 + str(mod10(c1)) + c2 + str(mod10(c2)) + c3 + str(mod10(c3))
            + barras[4] + barras[5:19])


def _validar_bancario(barras: str, res: ResultadoBoleto, ref: Optional[date]) -> None:
    res.tipo = "bancario"
    dv_calc = mod11_cobranca(barras[:4] + barras[5:])
    if int(barras[4]) != dv_calc:
        res.erros.append(f"DV geral inválido (esperado {dv_calc}, lido {barras[4]})")
    if barras[3] != "9":
        res.erros.append("Código de moeda diferente de 9 (Real)")
    res.banco = barras[0:3]
    res.valor = Decimal(int(barras[9:19])) / 100
    res.vencimento = fator_para_data(int(barras[5:9]), ref)


def _validar_linha47(linha: str, res: ResultadoBoleto) -> None:
    for nome, ini, fim in (("campo 1", 0, 9), ("campo 2", 10, 20), ("campo 3", 21, 31)):
        dv = mod10(linha[ini:fim])
        if int(linha[fim]) != dv:
            res.erros.append(f"DV do {nome} inválido (esperado {dv}, lido {linha[fim]})")


# --------------------------------------------------------------------------- #
# Arrecadação / convênios
# --------------------------------------------------------------------------- #
def _mod_arrecadacao(barras: str):
    ident = barras[2]
    if ident in "67":
        return mod10
    if ident in "89":
        return mod11_arrecadacao
    return None


def linha48_para_barras(linha: str) -> str:
    return "".join(linha[i * 12: i * 12 + 11] for i in range(4))


def barras_para_linha48(barras: str) -> str:
    fn = _mod_arrecadacao(barras) or mod10
    blocos = [barras[i * 11:(i + 1) * 11] for i in range(4)]
    return "".join(b + str(fn(b)) for b in blocos)


def _validar_arrecadacao(barras: str, res: ResultadoBoleto) -> None:
    res.tipo = "arrecadacao"
    fn = _mod_arrecadacao(barras)
    if fn is None:
        res.erros.append(f"Identificador de valor inválido: {barras[2]}")
        return
    dv = fn(barras[:3] + barras[4:])
    if int(barras[3]) != dv:
        res.erros.append(f"DV geral inválido (esperado {dv}, lido {barras[3]})")
    if barras[2] in "68":  # valor efetivo em reais
        res.valor = Decimal(int(barras[4:15])) / 100


def _validar_linha48(linha: str, res: ResultadoBoleto) -> None:
    fn = _mod_arrecadacao(linha48_para_barras(linha)) or mod10
    for i in range(4):
        bloco = linha[i * 12: i * 12 + 11]
        dv = fn(bloco)
        if int(linha[i * 12 + 11]) != dv:
            res.erros.append(f"DV do bloco {i + 1} inválido (esperado {dv})")


# --------------------------------------------------------------------------- #
# API pública
# --------------------------------------------------------------------------- #
def validar(codigo: str, referencia: Optional[date] = None) -> ResultadoBoleto:
    """Valida código de barras (44) ou linha digitável (47/48)."""
    dig = somente_digitos(codigo)
    res = ResultadoBoleto(entrada=dig, valido=False)

    if len(dig) == 44:
        res.codigo_barras = dig
        if dig[0] == "8":
            _validar_arrecadacao(dig, res)
            if res.tipo and not res.erros:
                res.linha_digitavel = barras_para_linha48(dig)
        else:
            _validar_bancario(dig, res, referencia)
            res.linha_digitavel = barras_para_linha47(dig)
    elif len(dig) == 47:
        res.linha_digitavel = dig
        _validar_linha47(dig, res)
        res.codigo_barras = linha47_para_barras(dig)
        _validar_bancario(res.codigo_barras, res, referencia)
    elif len(dig) == 48 and dig[0] == "8":
        res.linha_digitavel = dig
        res.codigo_barras = linha48_para_barras(dig)
        _validar_linha48(dig, res)
        _validar_arrecadacao(res.codigo_barras, res)
    else:
        res.erros.append(f"Tamanho inválido: {len(dig)} dígitos (esperado 44, 47 ou 48)")
        return res

    res.valido = not res.erros
    return res
