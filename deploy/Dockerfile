# Build from the project root:  docker build -f deploy/Dockerfile -t claimguard-web:1 .
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
RUN pip install --no-cache-dir "boto3>=1.34" \
 && useradd --system --uid 10001 --no-create-home app
WORKDIR /app
COPY lambdas/review_api/handler.py lambdas/review_api/portal.py lambdas/review_api/admin.py ./
COPY deploy/server.py ./server.py
COPY dashboard/index.html ./index.html
COPY dashboard/static ./static
USER 10001
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"
CMD ["python", "server.py"]
