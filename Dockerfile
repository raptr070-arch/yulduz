FROM python:3.10-slim

WORKDIR /app

# Kutubxonalarni o'rnatish
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bot fayllarini nusxalash
COPY . .

# Botni ishga tushirish
CMD ["python", "bot.py"]
