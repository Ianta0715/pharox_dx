# ---------------------------------------------------------------------------
# Etapa 1: builder — compila las dependencias con el toolchain completo.
# Esta capa (build-essential, g++, ~200 MB) nunca llega a la imagen final.
# ---------------------------------------------------------------------------
FROM python:3.10-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    g++ \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ---------------------------------------------------------------------------
# Etapa 2: imagen final — solo runtime, sin compiladores.
# ---------------------------------------------------------------------------
FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Tesseract (OCR de informes escaneados) sí es necesario en runtime, a
# diferencia del toolchain de compilación de la etapa builder.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    tesseract-ocr-spa \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    && rm -rf /var/lib/apt/lists/*

# Paquetes ya instalados y compilados por la etapa builder
COPY --from=builder /install /usr/local

# Código del backend y datos
COPY pharox_backend/app /app/app
COPY pharox_backend/data /app/data

# Ejecutar como usuario sin privilegios, no como root
RUN groupadd -r pharox && useradd -r -g pharox -d /app pharox \
    && chown -R pharox:pharox /app
USER pharox

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/', timeout=3)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
