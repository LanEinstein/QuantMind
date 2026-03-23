FROM python:3.11-slim

WORKDIR /app

ARG HTTP_PROXY
ARG HTTPS_PROXY
ENV http_proxy=${HTTP_PROXY} https_proxy=${HTTPS_PROXY}

COPY pyproject.toml .
COPY backend/ backend/
RUN pip install --no-cache-dir --timeout 120 .

ENV http_proxy="" https_proxy=""

COPY config/ config/

EXPOSE 8000

CMD ["uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
