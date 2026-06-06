FROM python:3.12-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.12-slim AS runtime
WORKDIR /app
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser
COPY --from=builder /install /usr/local
COPY . .
RUN chown -R appuser:appgroup /app
USER appuser
EXPOSE 8088
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8088"]
