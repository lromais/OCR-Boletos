# Contexto de build: raiz do repositório
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 HF_HOME=/opt/hf TOKENIZERS_PARALLELISM=false
WORKDIR /app
# torch CPU-only (imagem ~1/4 do tamanho da versão CUDA)
RUN pip install torch --index-url https://download.pytorch.org/whl/cpu
COPY services/classifier_worker/requirements.txt .
RUN pip install -r requirements.txt
# Baixa o modelo no build: cold start sem rede para o Hugging Face.
# Passe --build-arg HF_TOKEN=... se o repositório exigir aceite de termos.
ARG HF_TOKEN=""
RUN HF_TOKEN=$HF_TOKEN python -c "from huggingface_hub import snapshot_download; snapshot_download('google/embeddinggemma-2')"
ENV HF_HUB_OFFLINE=1
COPY src/ /app/src/
COPY services/classifier_worker/main.py .
ENV PYTHONPATH=/app/src
USER 1000
CMD ["python", "main.py"]
