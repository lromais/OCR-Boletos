"""Lambda de ingestão via IMAP (Gmail, Outlook, Exchange...).

Disparada pelo EventBridge a cada N minutos. Lê mensagens não lidas da pasta
configurada, grava o .eml cru em s3://<bucket>/raw/AAAA/MM/DD/<sha>.eml e marca
como lida. O S3 notifica a fila de e-mails -> worker de classificação.

Alternativa sem polling: SES Receiving (regra "salvar no S3" no prefixo raw/)
para um endereço dedicado tipo boletos@suaempresa.com.br. Ambos caem no
mesmo fluxo.
"""
import hashlib
import imaplib
import json
import os
from datetime import datetime, timezone

import boto3

s3 = boto3.client("s3")
secrets = boto3.client("secretsmanager")
BUCKET = os.environ["BUCKET_NAME"]
SECRET_ID = os.environ["IMAP_SECRET_ID"]   # {"host","user","password","folder"}
MAX_POR_EXECUCAO = int(os.getenv("MAX_EMAILS", "200"))


def handler(event, context):
    cfg = json.loads(secrets.get_secret_value(SecretId=SECRET_ID)["SecretString"])
    gravados = 0
    with imaplib.IMAP4_SSL(cfg["host"], int(cfg.get("port", 993))) as imap:
        imap.login(cfg["user"], cfg["password"])
        imap.select(cfg.get("folder", "INBOX"))
        _, data = imap.uid("search", None, "UNSEEN")
        uids = data[0].split()[:MAX_POR_EXECUCAO]
        for uid in uids:
            if context and context.get_remaining_time_in_millis() < 15000:
                break  # deixa o resto para a próxima execução
            _, msg = imap.uid("fetch", uid, "(BODY.PEEK[])")
            raw = msg[0][1]
            sha = hashlib.sha256(raw).hexdigest()
            dia = datetime.now(timezone.utc).strftime("%Y/%m/%d")
            s3.put_object(Bucket=BUCKET, Key=f"raw/{dia}/{sha}.eml", Body=raw,
                          ContentType="message/rfc822", ServerSideEncryption="aws:kms")
            imap.uid("store", uid, "+FLAGS", "(\\Seen)")  # só marca depois de gravar
            gravados += 1
    return {"gravados": gravados, "pendentes": max(0, len(uids) - gravados)}
