# Quiet Öppen Data — produktionsimage.
#
# Två saker som är avsiktliga här (se PLAN.md steg 19-diskussionen):
#   1. CPU-only torch installeras EXPLICIT innan paketet i övrigt — annars
#      drar sentence-transformers in CUDA-byggen av torch (flera extra GB,
#      och poänglöst på en VPS utan GPU).
#   2. Embeddings-modellen laddas ner i BYGGSTEGET, inte vid första frågan.
#      Görs det inte finns modellen inte i imagen, och första riktiga
#      frågan mot appen tar flera minuter medan den hämtas.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# build-essential krävs för att bygga vissa transitiva beroenden (tokenizers m.fl.)
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN pip install torch --index-url https://download.pytorch.org/whl/cpu

COPY pyproject.toml ./
COPY src ./src
RUN pip install -e .

# Förladdar KBLab/sentence-bert-swedish-cased i imagen (se motivering ovan).
RUN python -c "from sentence_transformers import SentenceTransformer; SentenceTransformer('KBLab/sentence-bert-swedish-cased')"

COPY config.toml ./
COPY kallor ./kallor
COPY lagar ./lagar

# data/ (index.sqlite, cache.sqlite, kvoter.sqlite) monteras som volym i
# Coolify så att index och cache överlever omdeploy — se README.md.
VOLUME ["/app/data"]

EXPOSE 8000

CMD ["uvicorn", "quiet_oppen_data.api:app", "--host", "0.0.0.0", "--port", "8000"]
