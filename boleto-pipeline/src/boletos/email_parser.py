"""Parse de .eml (RFC 822) -> estrutura simples com texto e anexos."""
from __future__ import annotations

import hashlib
import html
import re
from dataclasses import dataclass, field
from datetime import date
from email import policy
from email.parser import BytesParser
from email.utils import parsedate_to_datetime
from typing import Optional

TIPOS_ANEXO = {"application/pdf", "image/png", "image/jpeg", "image/tiff", "image/webp", "image/bmp"}
EXT_ANEXO = (".pdf", ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp")


@dataclass
class Anexo:
    nome: str
    mime: str
    conteudo: bytes = field(repr=False)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.conteudo).hexdigest()


@dataclass
class Email:
    message_id: str
    remetente: str
    assunto: str
    data: Optional[date]
    corpo: str
    anexos: list[Anexo]

    def texto_para_embedding(self, max_chars: int = 6000) -> str:
        nomes = ", ".join(a.nome for a in self.anexos) or "nenhum"
        return (f"Assunto: {self.assunto}\nDe: {self.remetente}\n"
                f"Anexos: {nomes}\n\n{self.corpo}")[:max_chars]


def _html_para_texto(h: str) -> str:
    h = re.sub(r"(?is)<(script|style).*?</\1>", " ", h)
    h = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>", "\n", h)
    return html.unescape(re.sub(r"<[^>]+>", " ", h))


def parse_eml(raw: bytes) -> Email:
    msg = BytesParser(policy=policy.default).parsebytes(raw)
    corpo_txt, corpo_html, anexos = [], [], []

    for parte in msg.walk():
        if parte.is_multipart():
            continue
        mime = parte.get_content_type()
        nome = parte.get_filename() or ""
        disp = parte.get_content_disposition()
        eh_anexo = disp in ("attachment", "inline") and nome
        if eh_anexo or mime in TIPOS_ANEXO:
            if mime in TIPOS_ANEXO or nome.lower().endswith(EXT_ANEXO):
                payload = parte.get_payload(decode=True) or b""
                if payload:
                    anexos.append(Anexo(nome or f"anexo{len(anexos)}", mime, payload))
            continue
        if mime == "text/plain":
            corpo_txt.append(parte.get_content())
        elif mime == "text/html":
            corpo_html.append(_html_para_texto(parte.get_content()))

    corpo = "\n".join(corpo_txt) or "\n".join(corpo_html)
    corpo = re.sub(r"[ \t]+", " ", re.sub(r"\n{3,}", "\n\n", corpo)).strip()

    try:
        dt = parsedate_to_datetime(msg["Date"]).date() if msg["Date"] else None
    except (TypeError, ValueError):
        dt = None

    mid = (msg["Message-ID"] or "").strip("<> ") or hashlib.sha256(raw).hexdigest()
    return Email(mid, str(msg["From"] or ""), str(msg["Subject"] or ""), dt, corpo, anexos)
