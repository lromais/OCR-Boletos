"""Treina a cabeça supervisionada (LogisticRegression) sobre embeddings do EmbeddingGemma 2.

Organize e-mails rotulados assim:
    dados/boleto/*.eml
    dados/outro/*.eml
Rode:
    python scripts/train_classifier.py --dados dados/ --saida cabeca.joblib
    aws s3 cp cabeca.joblib s3://<bucket>/models/cabeca.joblib
e configure CLASSIFIER_HEAD_PATH=s3://<bucket>/models/cabeca.joblib no worker.

Com ~100-200 exemplos por classe a cabeça já supera o zero-shot. Os embeddings
ficam congelados (sem fine-tuning), então treina em segundos em CPU.
"""
import argparse
import sys
from pathlib import Path

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import classification_report
from sklearn.model_selection import cross_val_predict

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from boletos.classifier import ClassificadorBoleto  # noqa: E402
from boletos.email_parser import parse_eml  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--dados", type=Path, required=True)
ap.add_argument("--saida", default="cabeca.joblib")
a = ap.parse_args()

textos, y = [], []
for rotulo, classe in ((1, "boleto"), (0, "outro")):
    for p in sorted((a.dados / classe).glob("*.eml")):
        textos.append(parse_eml(p.read_bytes()).texto_para_embedding())
        y.append(rotulo)
y = np.array(y)
X = ClassificadorBoleto().embed(textos)

clf = LogisticRegression(max_iter=2000, class_weight="balanced", C=2.0)
pred = cross_val_predict(clf, X, y, cv=5, method="predict_proba")[:, 1]
print(classification_report(y, pred >= 0.5, target_names=["outro", "boleto"]))
for lim in (0.3, 0.4, 0.5, 0.6):
    rec = ((pred >= lim) & (y == 1)).sum() / max(1, (y == 1).sum())
    print(f"limiar {lim:.1f}: recall boleto = {rec:.3f}")

joblib.dump(clf.fit(X, y), a.saida)
print("salvo em", a.saida)
