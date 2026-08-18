FROM python:3.10-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Instalar herramientas de compilación necesarias para dependencias como ChromaDB (p. ej. g++, build-essential)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    g++ \
    tesseract-ocr \
    tesseract-ocr-spa \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    && rm -rf /var/lib/apt/lists/*

# Copiar requirements.txt e instalar dependencias
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código del backend y datos
COPY pharox_backend/app /app/app
COPY pharox_backend/data /app/data

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
