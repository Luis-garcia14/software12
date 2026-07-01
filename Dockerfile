# 1. Usamos una imagen oficial de Python ligera basada en Debian
FROM python:3.10-slim

# 2. Instalar dependencias del sistema operativo (Tesseract y librerías que OpenCV necesita)
RUN apt-get update && apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-spa \
    libgl1-mesa-glx \
    libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

# 3. Crear el directorio de trabajo dentro del servidor
WORKDIR /app

# 4. Copiar los archivos de dependencias e instalarlos
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 5. Copiar todo el código de tu proyecto al servidor
COPY . .

# 6. Exponer el puerto en el que corre tu app de Flask
EXPOSE 10000

# 7. Comando para arrancar tu aplicación (reemplaza 'app:app' si tu archivo principal se llama diferente)
CMD ["gunicorn", "--bind", "0.0.0.0:10000", "app:app"]
