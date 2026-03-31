FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN addgroup --system squeaky && adduser --system --ingroup squeaky squeaky
USER squeaky

CMD ["python", "main.py"]
