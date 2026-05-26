FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements.txt -r requirements-dev.txt
COPY . .
RUN black --check . && pylint cache_warmer tests && pytest --cov=cache_warmer --cov-fail-under=85 -v tests/

CMD ["python", "-m", "cache_warmer.main"]
