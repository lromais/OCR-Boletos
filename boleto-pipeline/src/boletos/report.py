"""Gera a planilha .xlsx com todos os boletos."""
from __future__ import annotations

import io
from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Iterable

from openpyxl import Workbook
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

COLUNAS = [
    ("Status", "status", 12),
    ("Vencimento", "vencimento", 12),
    ("Valor (R$)", "valor", 14),
    ("Tipo", "tipo", 12),
    ("Banco", "banco", 8),
    ("Linha digitável", "linha_digitavel", 56),
    ("Código de barras", "codigo_barras", 50),
    ("Remetente", "remetente", 32),
    ("Assunto", "assunto", 40),
    ("Data e-mail", "data_email", 12),
    ("Anexo", "anexo_nome", 28),
    ("Página", "pagina", 7),
    ("Origem da leitura", "origem", 16),
    ("Conf. OCR", "conf_ocr", 10),
    ("Score classificador", "score_classificador", 12),
    ("Ocorrências", "ocorrencias", 11),
    ("Erros", "erros", 40),
    ("Arquivo S3", "anexo_s3", 40),
]
HEADER = PatternFill("solid", fgColor="1F3A5F")


def _converte(campo: str, v):
    if v is None:
        return None
    if campo in ("vencimento", "data_email") and isinstance(v, str):
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            return v
    if campo in ("valor", "conf_ocr", "score_classificador"):
        return float(v)
    if campo in ("pagina", "ocorrencias") and isinstance(v, Decimal):
        return int(v) + (1 if campo == "pagina" else 0)
    if isinstance(v, (list, set)):
        return "; ".join(map(str, v))
    return v


def _aba(wb: Workbook, titulo: str, linhas: list[dict]):
    ws = wb.create_sheet(titulo)
    for c, (nome, _, larg) in enumerate(COLUNAS, 1):
        cel = ws.cell(row=1, column=c, value=nome)
        cel.font, cel.fill = Font(bold=True, color="FFFFFF"), HEADER
        cel.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.column_dimensions[get_column_letter(c)].width = larg
    for r, item in enumerate(linhas, 2):
        for c, (_, campo, _) in enumerate(COLUNAS, 1):
            cel = ws.cell(row=r, column=c, value=_converte(campo, item.get(campo)))
            if campo in ("vencimento", "data_email"):
                cel.number_format = "DD/MM/YYYY"
            elif campo == "valor":
                cel.number_format = '"R$" #,##0.00'
            elif campo in ("linha_digitavel", "codigo_barras"):
                cel.number_format = "@"  # texto: Excel não pode virar 1,23E+46
    ws.freeze_panes = "A2"
    if linhas:
        ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUNAS))}{len(linhas) + 1}"
        # vencidos em vermelho, vencendo em 3 dias em amarelo
        fim = len(linhas) + 1
        ws.conditional_formatting.add(f"B2:B{fim}", FormulaRule(
            formula=["AND(ISNUMBER(B2),B2<TODAY())"], fill=PatternFill("solid", fgColor="F8CBAD")))
        ws.conditional_formatting.add(f"B2:B{fim}", FormulaRule(
            formula=["AND(ISNUMBER(B2),B2>=TODAY(),B2<=TODAY()+3)"], fill=PatternFill("solid", fgColor="FFE699")))
    return ws


def gerar_planilha(itens: Iterable[dict]) -> bytes:
    validos, revisar = [], []
    for it in itens:
        (validos if it.get("status") == "VALIDO" else revisar).append(it)
    validos.sort(key=lambda x: (x.get("vencimento") or "9999", x.get("remetente") or ""))

    wb = Workbook()
    wb.remove(wb.active)
    _aba(wb, "Boletos válidos", validos)
    _aba(wb, "Revisar", revisar)

    # Resumo por mês de vencimento
    ws = wb.create_sheet("Resumo")
    ws.append(["Mês de vencimento", "Qtd. boletos", "Total (R$)"])
    for c in ws[1]:
        c.font, c.fill = Font(bold=True, color="FFFFFF"), HEADER
    por_mes = defaultdict(lambda: [0, Decimal(0)])
    for it in validos:
        mes = (it.get("vencimento") or "sem vencimento")[:7]
        por_mes[mes][0] += 1
        por_mes[mes][1] += Decimal(str(it.get("valor") or 0))
    for mes in sorted(por_mes):
        ws.append([mes, por_mes[mes][0], float(por_mes[mes][1])])
        ws.cell(row=ws.max_row, column=3).number_format = '"R$" #,##0.00'
    n = ws.max_row
    ws.append(["TOTAL", f"=SUM(B2:B{n})", f"=SUM(C2:C{n})"])
    ws.cell(row=ws.max_row, column=3).number_format = '"R$" #,##0.00'
    ws.append([])
    ws.append(["Para revisão manual", len(revisar)])
    for col, w in zip("ABC", (22, 14, 18)):
        ws.column_dimensions[col].width = w

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
