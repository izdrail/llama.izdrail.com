FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY main.py .
COPY ui.html .

ENV TRACKER_PORT=8088
EXPOSE 8088
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${TRACKER_PORT}"]

