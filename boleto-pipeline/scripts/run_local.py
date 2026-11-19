"""Roda o pipeline inteiro na sua máquina, sem AWS.

    python scripts/run_local.py --emails ./caixa/ --saida boletos.xlsx

--emails: pasta com arquivos .eml (exporte do Gmail/Outlook ou use o IMAP abaixo)
--imap-host/--imap-user: baixa os não lidos direto (senha via env IMAP_PASSWORD)
--classificador palavras: pula o EmbeddingGemma (útil p/ testar sem baixar o modelo)
"""
import argparse
import imaplib
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from boletos.classifier import Classificacao, ClassificadorBoleto  # noqa: E402
from boletos.email_parser import parse_eml  # noqa: E402
from boletos.extractor import extrair_boletos  # noqa: E402
from boletos.ocr import LeituraAnexo, LeitorBoleto  # noqa: E402
from boletos.pipeline import linhas_planilha  # noqa: E402
from boletos.report import gerar_planilha  # noqa: E402

PALAVRAS = ("boleto", "fatura", "vencimento", "linha digitável", "2ª via", "segunda via", "cobrança")


def classificador_palavras(emails):
    out = []
    for e in emails:
        t = (e.assunto + " " + e.corpo + " " + " ".join(a.nome for a in e.anexos)).lower()
        hit = any(p in t for p in PALAVRAS) or bool(extrair_boletos(e.corpo))
        out.append(Classificacao(hit, 1.0 if hit else 0.0, "palavras_chave"))
    return out


def baixar_imap(host, user, senha, pasta="INBOX", limite=200):
    with imaplib.IMAP4_SSL(host) as imap:
        imap.login(user, senha)
        imap.select(pasta, readonly=True)
        _, d = imap.uid("search", None, "ALL")
        for uid in d[0].split()[-limite:]:
            _, m = imap.uid("fetch", uid, "(BODY.PEEK[])")
            yield m[0][1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--emails", type=Path)
    ap.add_argument("--imap-host")
    ap.add_argument("--imap-user")
    ap.add_argument("--saida", default="boletos.xlsx")
    ap.add_argument("--classificador", choices=["gemma", "palavras"], default="gemma")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    if a.imap_host:
        brutos = list(baixar_imap(a.imap_host, a.imap_user, os.environ["IMAP_PASSWORD"]))
    else:
        brutos = [p.read_bytes() for p in sorted(a.emails.glob("*.eml"))]
    emails = [parse_eml(b) for b in brutos]
    logging.info("%d e-mails carregados", len(emails))

    clss = (ClassificadorBoleto().classificar(emails) if a.classificador == "gemma"
            else classificador_palavras(emails))
    leitor = LeitorBoleto()
    linhas = []
    for e, c in zip(emails, clss):
        logging.info("%-5s %.2f  %s", "BOLETO" if c.eh_boleto else "-", c.score, e.assunto[:70])
        if not c.eh_boleto:
            continue
        if not e.anexos:
            lt = LeituraAnexo(boletos=extrair_boletos(e.corpo, e.data))
            for r in lt.boletos:
                lt.origem[r.codigo_barras] = "corpo_email"
            linhas += linhas_planilha(e, c, None, lt)
        for an in e.anexos:
            linhas += linhas_planilha(e, c, an, leitor.ler(an.conteudo, an.mime, e.data))

    # dedup por código de barras, como o DynamoDB faz
    unicos = {}
    for ln in linhas:
        if ln["pk"] in unicos:
            unicos[ln["pk"]]["ocorrencias"] = unicos[ln["pk"]].get("ocorrencias", 1) + 1
        else:
            unicos[ln["pk"]] = {**ln, "ocorrencias": 1}
    Path(a.saida).write_bytes(gerar_planilha(unicos.values()))
    validos = sum(1 for x in unicos.values() if x.get("status") == "VALIDO")
    logging.info("Planilha: %s (%d válidos, %d p/ revisar)", a.saida, validos, len(unicos) - validos)


if __name__ == "__main__":
    main()
