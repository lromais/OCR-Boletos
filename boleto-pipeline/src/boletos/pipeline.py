"""Lógica de negócio sem AWS: transforma um e-mail + leitura em linhas da planilha.
Usada pelos workers e pelo modo local (scripts/run_local.py)."""
from __future__ import annotations

import hashlib
from typing import Optional

from .barcode import ResultadoBoleto
from .classifier import Classificacao
from .email_parser import Anexo, Email
from .ocr import LeituraAnexo


def _pk_invalido(*partes: str) -> str:
    return "INVALIDO#" + hashlib.sha256("|".join(partes).encode()).hexdigest()[:32]


def linhas_planilha(email: Email, cls: Classificacao, anexo: Optional[Anexo],
                    leitura: Optional[LeituraAnexo], anexo_s3: str = "") -> list[dict]:
    base = {
        "message_id": email.message_id,
        "remetente": email.remetente,
        "assunto": email.assunto,
        "data_email": email.data.isoformat() if email.data else None,
        "anexo_nome": anexo.nome if anexo else "(corpo do e-mail)",
        "anexo_s3": anexo_s3,
        "score_classificador": round(cls.score, 4),
        "conf_ocr": round(leitura.conf_ocr, 4) if leitura and leitura.conf_ocr is not None else None,
    }

    def de_resultado(r: ResultadoBoleto, origem: str, pagina: Optional[int]) -> dict:
        d = r.to_dict()
        return {**base, "pk": r.codigo_barras if r.valido else _pk_invalido(email.message_id, base["anexo_nome"], r.entrada),
                "status": "VALIDO" if r.valido else "INVALIDO",
                "tipo": d["tipo"], "banco": d["banco"], "valor": d["valor"],
                "vencimento": d["vencimento"], "linha_digitavel": d["linha_digitavel"],
                "codigo_barras": d["codigo_barras"], "erros": "; ".join(r.erros) or None,
                "origem": origem, "pagina": pagina}

    if leitura is None:
        return []
    if leitura.boletos:
        return [de_resultado(r, leitura.origem.get(r.codigo_barras, "?"), leitura.pagina.get(r.codigo_barras))
                for r in leitura.boletos]
    if leitura.suspeito:
        return [de_resultado(leitura.suspeito, "indra_ocr", None)]
    # Classificado como boleto, mas nada legível: vai para revisão
    return [{**base, "pk": _pk_invalido(email.message_id, base["anexo_nome"]),
             "status": "NAO_ENCONTRADO", "erros": "Nenhum código de barras/linha digitável legível"}]
