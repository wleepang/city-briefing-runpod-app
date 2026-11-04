FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PORT=8000
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY public ./public
RUN useradd --create-home appuser
USER appuser
EXPOSE 8000
# 0.0.0.0 is required for Runpod's HTTP proxy to reach the container.
CMD ["sh", "-c", "exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port ${PORT}"]
