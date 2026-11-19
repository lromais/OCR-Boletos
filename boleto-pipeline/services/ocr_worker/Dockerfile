# Contexto de build: raiz do repositório
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 INDRA_OCR_MODEL_DIR=/opt/indra
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*
COPY services/ocr_worker/requirements.txt .
RUN pip install -r requirements.txt
# Baixa os modelos ONNX (~76 MB) no build. Ajuste o repo HF conforme o README do Indra.
RUN python -c "from huggingface_hub import snapshot_download; snapshot_download('datalab-to/indra-ocr', local_dir='/opt/indra')"
COPY src/ /app/src/
COPY services/ocr_worker/main.py .
ENV PYTHONPATH=/app/src
USER 1000
CMD ["python", "main.py"]
