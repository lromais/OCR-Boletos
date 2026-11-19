"""Teste ponta a ponta sem rede: gera PDFs de boleto com código de barras ITF real,
monta .eml, roda parse -> leitura (zxing) -> planilha. O Indra é substituído por
um stub (os pesos vêm do Hugging Face), então aqui exercitamos o caminho do
código de barras na imagem + o caminho do texto via stub."""
import io
from email.message import EmailMessage
from types import SimpleNamespace

import pytest
from openpyxl import load_workbook

from boletos.barcode import barras_para_linha47
from boletos.classifier import Classificacao
from boletos.email_parser import parse_eml
from boletos.ocr import LeitorBoleto
from boletos.pipeline import linhas_planilha
from boletos.report import gerar_planilha
from test_barcode import gera_bancario

reportlab = pytest.importorskip("reportlab")
pytest.importorskip("zxingcpp")


def pdf_boleto(barras: str, com_barcode: bool = True) -> bytes:
    from reportlab.graphics.barcode.common import I2of5
    from reportlab.lib.units import mm
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(20 * mm, 270 * mm, "BANCO EXEMPLO S.A. - Recibo do Pagador")
    if com_barcode:
        # padrão FEBRABAN: ITF, barra fina 0,254 mm (aqui um pouco maior p/ teste), razão 1:3
        I2of5(barras, barWidth=0.33 * mm, ratio=3, barHeight=13 * mm, checksum=0,
              bearers=0, quiet=1, humanReadable=0).drawOn(c, 20 * mm, 200 * mm)
    c.save()
    return buf.getvalue()


def eml(assunto, corpo, anexos=()):
    m = EmailMessage()
    m["From"], m["To"], m["Subject"] = "cobranca@empresa.com.br", "eu@x.com", assunto
    m["Date"], m["Message-ID"] = "Tue, 10 Nov 2026 10:00:00 -0300", f"<{abs(hash(assunto))}@x>"
    m.set_content(corpo)
    for nome, dados in anexos:
        m.add_attachment(dados, maintype="application", subtype="pdf", filename=nome)
    return m.as_bytes()


class IndraFake:
    """Simula o Indra: devolve um texto fixo por página."""
    def __init__(self, texto=""):
        self.texto = texto

    def convert(self, path, max_pages=None):
        yield 0, SimpleNamespace(text=self.texto, source="ocr",
                                 lines=[SimpleNamespace(conf=0.97)])

    def ocr(self, img):
        return SimpleNamespace(text=self.texto, lines=[])


def test_barcode_na_imagem_vira_linha_da_planilha():
    barras = gera_bancario(fator="1630", valor="0000045990")  # vence em 2026-11-14
    e = parse_eml(eml("Seu boleto chegou", "Segue boleto em anexo.", [("boleto.pdf", pdf_boleto(barras))]))
    assert len(e.anexos) == 1

    leitor = LeitorBoleto()
    leitor.__dict__["indra"] = IndraFake("")  # OCR não achou nada; barcode tem que salvar
    leitura = leitor.ler(e.anexos[0].conteudo, e.anexos[0].mime, e.data)

    assert [b.codigo_barras for b in leitura.boletos] == [barras]
    assert leitura.origem[barras] == "barcode_imagem"

    linhas = linhas_planilha(e, Classificacao(True, 0.9, "zero_shot"), e.anexos[0], leitura)
    assert linhas[0]["status"] == "VALIDO"
    assert linhas[0]["valor"] == "459.9"
    assert linhas[0]["vencimento"] == "2026-11-14"


def test_ocr_texto_e_invalido_vao_para_planilha():
    barras = gera_bancario()
    linha = barras_para_linha47(barras)
    e_ok = parse_eml(eml("Fatura", "x", [("f.pdf", pdf_boleto(barras, com_barcode=False))]))
    leitor = LeitorBoleto()
    leitor.__dict__["indra"] = IndraFake(f"Linha: {linha[:5]}.{linha[5:10]} {linha[10:]}")
    l_ok = leitor.ler(e_ok.anexos[0].conteudo, "application/pdf", e_ok.data)
    assert l_ok.boletos and l_ok.origem[barras] == "indra_ocr" and l_ok.conf_ocr == pytest.approx(0.97)

    errada = linha[:20] + str((int(linha[20]) + 1) % 10) + linha[21:]
    e_bad = parse_eml(eml("Fatura 2", "x", [("g.pdf", pdf_boleto(barras, com_barcode=False))]))
    leitor.__dict__["indra"] = IndraFake(errada)
    l_bad = leitor.ler(e_bad.anexos[0].conteudo, "application/pdf", e_bad.data)
    assert not l_bad.boletos and l_bad.suspeito and not l_bad.suspeito.valido

    cls = Classificacao(True, 0.8, "zero_shot")
    linhas = (linhas_planilha(e_ok, cls, e_ok.anexos[0], l_ok)
              + linhas_planilha(e_bad, cls, e_bad.anexos[0], l_bad))
    wb = load_workbook(io.BytesIO(gerar_planilha(linhas)))
    assert wb.sheetnames == ["Boletos válidos", "Revisar", "Resumo"]
    assert wb["Boletos válidos"]["F2"].value == linha
    assert "DV do campo 2" in wb["Revisar"]["Q2"].value
