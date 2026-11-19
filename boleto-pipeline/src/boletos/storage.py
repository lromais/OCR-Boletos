"""Acesso a S3 / SQS / DynamoDB. Nomes vêm de variáveis de ambiente (CDK injeta)."""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from typing import Iterator, Optional

import boto3
from botocore.exceptions import ClientError

BUCKET = os.getenv("BUCKET_NAME", "")
TABELA = os.getenv("TABLE_NAME", "")
FILA_OCR = os.getenv("OCR_QUEUE_URL", "")

_s3 = boto3.client("s3")
_sqs = boto3.client("sqs")
_ddb = boto3.resource("dynamodb")


def agora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------- S3 ------------------------------------ #
def ler_s3(key: str, bucket: str = BUCKET) -> bytes:
    return _s3.get_object(Bucket=bucket, Key=key)["Body"].read()


def gravar_s3(key: str, data: bytes, content_type: str = "application/octet-stream",
              bucket: str = BUCKET) -> str:
    _s3.put_object(Bucket=bucket, Key=key, Body=data, ContentType=content_type,
                   ServerSideEncryption="aws:kms")
    return key


def url_assinada(key: str, expira: int = 3600, bucket: str = BUCKET) -> str:
    return _s3.generate_presigned_url("get_object", Params={"Bucket": bucket, "Key": key},
                                      ExpiresIn=expira)


def eventos_s3(body: str) -> Iterator[tuple[str, str]]:
    """Extrai (bucket, key) de notificação S3 entregue via SQS."""
    msg = json.loads(body)
    for rec in msg.get("Records", []):
        from urllib.parse import unquote_plus
        yield rec["s3"]["bucket"]["name"], unquote_plus(rec["s3"]["object"]["key"])


# ---------------------------------- SQS ----------------------------------- #
def enviar_ocr(payload: dict) -> None:
    _sqs.send_message(QueueUrl=FILA_OCR, MessageBody=json.dumps(payload))


def consumir(fila_url: str, max_msgs: int = 10, espera: int = 20) -> list[dict]:
    r = _sqs.receive_message(QueueUrl=fila_url, MaxNumberOfMessages=max_msgs,
                             WaitTimeSeconds=espera, VisibilityTimeout=900)
    return r.get("Messages", [])


def ack(fila_url: str, receipt: str) -> None:
    _sqs.delete_message(QueueUrl=fila_url, ReceiptHandle=receipt)


# ------------------------------- DynamoDB --------------------------------- #
def _tabela():
    return _ddb.Table(TABELA)


def _limpa(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if v is None or v == "":
            continue
        out[k] = Decimal(str(v)) if isinstance(v, float) else v
    return out


def salvar_boleto(item: dict) -> bool:
    """Grava boleto com idempotência pelo código de barras.

    pk = código de barras (válido) ou INVALIDO#<hash> (para revisão).
    Se o mesmo boleto chegar em outro e-mail, incrementa `ocorrencias`
    em vez de duplicar a linha da planilha. Retorna True se for novo.
    """
    item = _limpa(item)
    item.setdefault("processado_em", agora())
    try:
        _tabela().put_item(Item={**item, "ocorrencias": 1},
                           ConditionExpression="attribute_not_exists(pk)")
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] != "ConditionalCheckFailedException":
            raise
        _tabela().update_item(
            Key={"pk": item["pk"]},
            UpdateExpression="ADD ocorrencias :um, message_ids :mid",
            ExpressionAttributeValues={":um": 1, ":mid": {item.get("message_id", "?")}},
        )
        return False


def pk_invalido(*partes: str) -> str:
    return "INVALIDO#" + hashlib.sha256("|".join(partes).encode()).hexdigest()[:32]


def varrer_boletos(desde: Optional[str] = None) -> Iterator[dict]:
    kw = {}
    if desde:
        from boto3.dynamodb.conditions import Attr
        kw["FilterExpression"] = Attr("processado_em").gte(desde)
    t = _tabela()
    while True:
        r = t.scan(**kw)
        yield from r["Items"]
        if "LastEvaluatedKey" not in r:
            break
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]
