"""Leitura de anexos: código de barras na imagem + Indra OCR no texto.

Ordem de tentativa por página (para no primeiro boleto válido):
  1. Camada de texto do PDF (Indra mode="auto" usa quando existe; grátis).
  2. Decodificação do código de barras ITF-25 na imagem renderizada (zxing-cpp).
     É a fonte mais confiável: o próprio código de barras, sem OCR.
  3. Texto do Indra OCR -> extrator de linha digitável -> validação de DV.
"""
from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import date
from functools import cached_property
from typing import Optional

import numpy as np

from .barcode import ResultadoBoleto, validar
from .extractor import extrair_boletos, melhor_candidato_invalido

log = logging.getLogger(__name__)
OCR_THREADS = int(os.getenv("OCR_THREADS", str(os.cpu_count() or 2)))
MAX_PAGINAS = int(os.getenv("OCR_MAX_PAGES", "5"))  # boleto quase sempre está na pág. 1-2


@dataclass
class LeituraAnexo:
    boletos: list[ResultadoBoleto] = field(default_factory=list)
    origem: dict[str, str] = field(default_factory=dict)       # codigo -> origem
    pagina: dict[str, int] = field(default_factory=dict)
    conf_ocr: Optional[float] = None
    suspeito: Optional[ResultadoBoleto] = None                 # p/ revisão humana
    texto: str = ""


class LeitorBoleto:
    @cached_property
    def indra(self):
        import indra_ocr
        return indra_ocr.load(threads=OCR_THREADS)  # modelos ~76 MB, embutidos na imagem

    # ---------------------------- código de barras ------------------------- #
    @staticmethod
    def _ler_barras(img_gray: np.ndarray) -> list[str]:
        try:
            import zxingcpp
        except ImportError:
            return []
        out = []
        for b in zxingcpp.read_barcodes(img_gray, formats=zxingcpp.BarcodeFormat.ITF):
            dig = "".join(ch for ch in b.text if ch.isdigit())
            if len(dig) == 44:
                out.append(dig)
        return out

    @staticmethod
    def _paginas_imagem(conteudo: bytes, mime: str):
        """Gera (indice, imagem_gray uint8) para PDF ou imagem."""
        import cv2
        if mime == "application/pdf":
            import pypdfium2 as pdfium
            pdf = pdfium.PdfDocument(conteudo)
            for i in range(min(len(pdf), MAX_PAGINAS)):
                pil = pdf[i].render(scale=300 / 72).to_pil().convert("L")  # 300 dpi p/ barras
                yield i, np.asarray(pil, dtype=np.uint8)
        else:
            arr = cv2.imdecode(np.frombuffer(conteudo, np.uint8), cv2.IMREAD_GRAYSCALE)
            if arr is not None:
                yield 0, arr

    # ---------------------------------- API -------------------------------- #
    def ler(self, conteudo: bytes, mime: str, referencia: Optional[date] = None) -> LeituraAnexo:
        res = LeituraAnexo()
        achados: dict[str, ResultadoBoleto] = {}

        def add(r: ResultadoBoleto, origem: str, pag: int):
            if r.valido and r.codigo_barras not in achados:
                achados[r.codigo_barras] = r
                res.origem[r.codigo_barras] = origem
                res.pagina[r.codigo_barras] = pag

        textos, confs = [], []

        # 1 + 3) Indra: usa camada de texto se houver, senão OCR
        if mime == "application/pdf":
            with tempfile.NamedTemporaryFile(suffix=".pdf") as f:
                f.write(conteudo)
                f.flush()
                for i, page in self.indra.convert(f.name, max_pages=MAX_PAGINAS):
                    textos.append((i, page.text, page.source))
                    confs += [ln.conf for ln in page.lines if getattr(ln, "conf", None) is not None]
        imagens = list(self._paginas_imagem(conteudo, mime))
        if mime != "application/pdf":
            for i, img in imagens:
                page = self.indra.ocr(img)
                textos.append((i, page.text, "ocr"))
                confs += [ln.conf for ln in page.lines if getattr(ln, "conf", None) is not None]

        # 2) código de barras na imagem (mais confiável)
        for i, img in imagens:
            for cod in self._ler_barras(img):
                add(validar(cod, referencia), "barcode_imagem", i)

        # 3) linha digitável no texto
        for i, txt, source in textos:
            for r in extrair_boletos(txt, referencia):
                add(r, "pdf_texto" if source == "text" else "indra_ocr", i)

        res.boletos = list(achados.values())
        res.texto = "\n".join(t for _, t, _ in textos)
        res.conf_ocr = float(np.mean(confs)) if confs else None
        if not res.boletos:
            res.suspeito = melhor_candidato_invalido(res.texto)
        return res
