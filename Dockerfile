# Uma imagem base + 3 alvos. Build:
#   docker build --target classificador -t boletos-classificador .
#   docker build --target ocr           -t boletos-ocr .
#   docker build --target app           -t boletos-app .      (ingest, api, relatório)
FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml ./
COPY src/ src/
RUN pip install ".[postgres,aws,gcp,azure]"

FROM base AS app
RUN pip install ".[gmail,api]"
USER 1000
CMD ["boletos", "api"]

FROM base AS classificador
ENV HF_HOME=/opt/hf TOKENIZERS_PARALLELISM=false
# torch CPU-only: imagem ~4x menor que a CUDA
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu && pip install ".[gemma]"
# modelo embutido na imagem: worker sobe sem internet (passe --build-arg HF_TOKEN se exigido)
ARG HF_TOKEN=""
RUN HF_TOKEN=$HF_TOKEN python -c "from huggingface_hub import snapshot_download as s; s('google/embeddinggemma-2')"
ENV HF_HUB_OFFLINE=1
USER 1000
CMD ["boletos", "worker", "classificar"]

FROM base AS ocr
ENV INDRA_OCR_MODEL_DIR=/opt/indra
RUN apt-get update && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/* && pip install ".[ocr]" huggingface_hub
RUN python -c "from huggingface_hub import snapshot_download as s; s('datalab-to/indra-ocr', local_dir='/opt/indra')"
USER 1000
CMD ["boletos", "worker", "ocr"]
