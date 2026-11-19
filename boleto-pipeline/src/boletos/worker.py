"""Loop genérico de consumo SQS para os containers ECS.

- long polling (20s), processa em lote, apaga só o que deu certo;
- falha -> mensagem volta após o visibility timeout -> após N tentativas vai pra DLQ;
- SIGTERM (scale-in / Fargate Spot) termina o lote atual e sai limpo.
"""
from __future__ import annotations

import logging
import signal
from typing import Callable

from . import storage

log = logging.getLogger(__name__)
_parar = False


def _sigterm(*_):
    global _parar
    log.warning("SIGTERM recebido: terminando lote atual e saindo")
    _parar = True


def rodar(fila_url: str, processar_lote: Callable[[list[dict]], set[str]], max_msgs: int = 10):
    """`processar_lote` recebe as mensagens e devolve o conjunto de MessageId com sucesso."""
    signal.signal(signal.SIGTERM, _sigterm)
    while not _parar:
        msgs = storage.consumir(fila_url, max_msgs=max_msgs)
        if not msgs:
            continue
        try:
            ok = processar_lote(msgs)
        except Exception:
            log.exception("Falha no lote inteiro; mensagens voltarão para a fila")
            continue
        for m in msgs:
            if m["MessageId"] in ok:
                storage.ack(fila_url, m["ReceiptHandle"])
