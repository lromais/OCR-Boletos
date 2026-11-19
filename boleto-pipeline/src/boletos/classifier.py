"""Classificador "é boleto?" com EmbeddingGemma 2.

Três sinais, do mais forte ao mais fraco:
  1. Regra: linha digitável válida no corpo do e-mail  -> boleto (certeza).
  2. Cabeça supervisionada (LogisticRegression sobre embeddings), se treinada
     com scripts/train_classifier.py e publicada no S3.
  3. Zero-shot: similaridade de cosseno com protótipos de cada classe.

O classificador é um *portão barato*: ele decide o que vai para o OCR. A
verdade final é o validador de DV, então calibramos para recall alto (na
dúvida, manda para OCR).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import cached_property
from typing import Optional

import numpy as np

from .email_parser import Email
from .extractor import extrair_boletos

log = logging.getLogger(__name__)

MODEL_ID = os.getenv("EMBEDDING_MODEL", "google/embeddinggemma-2")
DIM = int(os.getenv("EMBEDDING_DIM", "256"))          # Matryoshka: 768/512/256/128
LIMIAR_BOLETO = float(os.getenv("LIMIAR_BOLETO", "0.55"))
LIMIAR_DUVIDA = float(os.getenv("LIMIAR_DUVIDA", "0.40"))

PROTOTIPOS = {
    "boleto": [
        "Segue em anexo o boleto para pagamento com vencimento e linha digitável.",
        "Sua fatura está disponível. Pague com o código de barras do boleto bancário.",
        "Boleto de cobrança referente à mensalidade. Vencimento, valor do documento, beneficiário.",
        "2ª via do boleto da conta de luz, água, gás ou condomínio.",
        "Aviso de cobrança: título em aberto, boleto registrado anexo em PDF.",
        "Guia de pagamento DAS / DARF / IPTU com código de barras.",
    ],
    "outro": [
        "Newsletter com novidades e promoções da semana.",
        "Confirmação de pedido e código de rastreio da entrega.",
        "Comprovante de pagamento realizado com sucesso via Pix.",
        "Nota fiscal eletrônica emitida, segue XML e DANFE.",
        "Convite de reunião e pauta do projeto.",
        "Extrato mensal da conta e informe de rendimentos.",
    ],
}


@dataclass
class Classificacao:
    eh_boleto: bool
    score: float           # 0..1, prob. de ser boleto
    motivo: str            # "regra_linha_digitavel" | "modelo_supervisionado" | "zero_shot"
    duvida: bool = False


class ClassificadorBoleto:
    def __init__(self, cabeca_path: Optional[str] = None, device: Optional[str] = None):
        self.cabeca_path = cabeca_path or os.getenv("CLASSIFIER_HEAD_PATH")
        self.device = device

    # Carregamento preguiçoso: o worker carrega uma vez e reaproveita entre mensagens.
    @cached_property
    def modelo(self):
        import torch
        from sentence_transformers import SentenceTransformer

        dtype = torch.bfloat16 if torch.cuda.is_available() and torch.cuda.is_bf16_supported() else torch.float32
        # NUNCA float16: o EmbeddingGemma 2 estoura o range e devolve NaN silenciosamente.
        log.info("Carregando %s (dtype=%s, dim=%s)", MODEL_ID, dtype, DIM)
        return SentenceTransformer(
            MODEL_ID,
            device=self.device,
            truncate_dim=DIM,
            model_kwargs={"torch_dtype": dtype},
            # só o encoder de texto (270M) -- desliga visão e áudio
            config_kwargs={"vision_config": None, "audio_config": None},
        )

    @cached_property
    def prototipos(self) -> dict[str, np.ndarray]:
        out = {}
        for classe, textos in PROTOTIPOS.items():
            emb = self.modelo.encode(textos, prompt_name="Classification", normalize_embeddings=True)
            c = emb.mean(axis=0)
            out[classe] = c / np.linalg.norm(c)
        return out

    @cached_property
    def cabeca(self):
        if not self.cabeca_path:
            return None
        import joblib
        if self.cabeca_path.startswith("s3://"):
            import tempfile
            import boto3
            bucket, key = self.cabeca_path[5:].split("/", 1)
            tmp = tempfile.NamedTemporaryFile(suffix=".joblib", delete=False).name
            boto3.client("s3").download_file(bucket, key, tmp)
            return joblib.load(tmp)
        return joblib.load(self.cabeca_path)

    def embed(self, textos: list[str]) -> np.ndarray:
        return self.modelo.encode(textos, prompt_name="Classification",
                                  normalize_embeddings=True, batch_size=16)

    def classificar(self, emails: list[Email]) -> list[Classificacao]:
        resultados: list[Optional[Classificacao]] = [None] * len(emails)
        pendentes = []
        for i, e in enumerate(emails):
            if extrair_boletos(e.corpo, e.data):
                resultados[i] = Classificacao(True, 1.0, "regra_linha_digitavel")
            else:
                pendentes.append(i)

        if pendentes:
            emb = self.embed([emails[i].texto_para_embedding() for i in pendentes])
            if self.cabeca is not None:
                probs = self.cabeca.predict_proba(emb)[:, 1]
                motivo = "modelo_supervisionado"
            else:
                sim_b = emb @ self.prototipos["boleto"]
                sim_o = emb @ self.prototipos["outro"]
                probs = 1 / (1 + np.exp(-(sim_b - sim_o) * 20))  # logit com temperatura
                motivo = "zero_shot"
            for i, p in zip(pendentes, probs):
                p = float(p)
                tem_anexo = bool(emails[i].anexos)
                duvida = LIMIAR_DUVIDA <= p < LIMIAR_BOLETO
                # recall > precisão: na dúvida e com anexo, manda pro OCR
                resultados[i] = Classificacao(p >= LIMIAR_BOLETO or (duvida and tem_anexo), p, motivo, duvida)
        return resultados  # type: ignore[return-value]
