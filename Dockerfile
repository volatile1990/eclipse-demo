FROM python:3.13-slim@sha256:e48f59cdba69a3cbd9ed2cf0f348d90d45a8d350d98134cf1070bc106ee9ed90

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    EDEMO_DATA=/data \
    EDEMO_MEDIA=/media/preview \
    EDEMO_PORT=8080

WORKDIR /app
COPY app/ /app/

USER 10050:10050
EXPOSE 8080
CMD ["python", "/app/server.py"]
