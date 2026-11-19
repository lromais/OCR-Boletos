"""Worker de OCR (ECS Fargate, CPU, escala por profundidade da fila).

SQS ocr-queue -> baixa anexo -> barcode ITF + Indra OCR -> valida DV -> DynamoDB
"""
import json
import logging
import os
from datetime import date

from boletos import storage
from boletos.classifier import Classificacao
from boletos.email_parser import Anexo, Email
from boletos.ocr import LeitorBoleto
from boletos.pipeline import linhas_planilha
from boletos.worker import rodar

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format='{"t":"%(asctime)s","lvl":"%(levelname)s","msg":"%(message)s"}')
log = logging.getLogger("ocr")
FILA = os.environ["OCR_QUEUE_URL"]
leitor = LeitorBoleto()


def processar_lote(msgs: list[dict]) -> set[str]:
    ok = set()
    for m in msgs:
        try:
            p = json.loads(m["Body"])
            ref = date.fromisoformat(p["data"]) if p.get("data") else None
            conteudo = storage.ler_s3(p["anexo_key"])
            leitura = leitor.ler(conteudo, p["mime"], referencia=ref)

            email = Email(p["message_id"], p["remetente"], p["assunto"], ref, "", [])
            anexo = Anexo(p["anexo_nome"], p["mime"], b"")
            cls = Classificacao(True, p["score"], p["motivo"])
            linhas = linhas_planilha(email, cls, anexo, leitura, anexo_s3=p["anexo_key"])
            for linha in linhas:
                storage.salvar_boleto(linha)
            log.info(json.dumps({"anexo": p["anexo_key"], "boletos": len(leitura.boletos),
                                 "status": [x["status"] for x in linhas]}))
            ok.add(m["MessageId"])
        except Exception:
            log.exception("Erro no OCR de %s", m.get("MessageId"))
    return ok


if __name__ == "__main__":
    _ = leitor.indra  # carrega modelos ONNX
    rodar(FILA, processar_lote, max_msgs=int(os.getenv("OCR_BATCH", "4")))
