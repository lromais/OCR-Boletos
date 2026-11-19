"""Worker de classificação (ECS Fargate).

SQS (eventos S3 de raw/*.eml) -> parse -> EmbeddingGemma 2 -> se boleto:
  * salva anexos em attachments/<sha>.<ext>
  * publica 1 mensagem por anexo na fila de OCR
  * se o boleto estiver só no corpo do e-mail, grava direto no DynamoDB
"""
import json
import logging
import os

from boletos import storage
from boletos.classifier import ClassificadorBoleto
from boletos.email_parser import parse_eml
from boletos.extractor import extrair_boletos
from boletos.ocr import LeituraAnexo
from boletos.pipeline import linhas_planilha
from boletos.worker import rodar

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                    format='{"t":"%(asctime)s","lvl":"%(levelname)s","msg":"%(message)s"}')
log = logging.getLogger("classifier")
FILA = os.environ["EMAIL_QUEUE_URL"]
clf = ClassificadorBoleto()


def processar_lote(msgs: list[dict]) -> set[str]:
    itens = []  # (MessageId, key, Email)
    for m in msgs:
        for bucket, key in storage.eventos_s3(m["Body"]):
            itens.append((m["MessageId"], key, parse_eml(storage.ler_s3(key, bucket))))

    ok = {m["MessageId"] for m in msgs}  # mensagens sem Records (ex.: s3:TestEvent) são descartadas
    if not itens:
        return ok

    for (mid, key, email), cls in zip(itens, clf.classificar([e for _, _, e in itens])):
        try:
            log.info(json.dumps({"key": key, "assunto": email.assunto[:80], "boleto": cls.eh_boleto,
                                 "score": round(cls.score, 3), "motivo": cls.motivo}))
            if not cls.eh_boleto:
                continue
            ctx = {"message_id": email.message_id, "remetente": email.remetente, "assunto": email.assunto,
                   "data": email.data.isoformat() if email.data else None, "corpo": email.corpo[:20000],
                   "score": cls.score, "motivo": cls.motivo, "raw_key": key}
            if email.anexos:
                for a in email.anexos:
                    ext = os.path.splitext(a.nome)[1].lower() or ".bin"
                    akey = storage.gravar_s3(f"attachments/{a.sha256}{ext}", a.conteudo, a.mime)
                    storage.enviar_ocr({**ctx, "anexo_key": akey, "anexo_nome": a.nome, "mime": a.mime})
            else:
                leitura = LeituraAnexo(boletos=extrair_boletos(email.corpo, email.data))
                for r in leitura.boletos:
                    leitura.origem[r.codigo_barras] = "corpo_email"
                for linha in linhas_planilha(email, cls, None, leitura):
                    storage.salvar_boleto(linha)
        except Exception:
            log.exception("Erro processando %s", key)
            ok.discard(mid)
    return ok


if __name__ == "__main__":
    _ = clf.prototipos  # aquece o modelo antes de consumir
    rodar(FILA, processar_lote, max_msgs=10)
