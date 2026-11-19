"""Lambda que gera a planilha a partir do DynamoDB.

Disparo: EventBridge (ex.: todo dia 7h) ou chamada manual / API Gateway.
Evento opcional: {"desde": "2026-11-01", "enviar_para": "financeiro@empresa.com"}
"""
import os
from datetime import datetime, timezone

import boto3

from boletos import storage
from boletos.report import gerar_planilha


def handler(event, context):
    event = event or {}
    itens = list(storage.varrer_boletos(desde=event.get("desde")))
    xlsx = gerar_planilha(itens)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    key = storage.gravar_s3(f"reports/boletos-{ts}.xlsx", xlsx,
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    url = storage.url_assinada(key, expira=int(os.getenv("URL_EXPIRA", "86400")))

    if dest := event.get("enviar_para") or os.getenv("REPORT_TO"):
        boto3.client("ses").send_email(
            Source=os.environ["REPORT_FROM"],
            Destination={"ToAddresses": [dest]},
            Message={"Subject": {"Data": f"Planilha de boletos {ts[:8]}"},
                     "Body": {"Text": {"Data": f"{len(itens)} registros. Link (24h): {url}"}}},
        )
    return {"registros": len(itens), "s3_key": key, "url": url}
