FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY todosky ./todosky
RUN pip install --no-cache-dir .
ENV STATE_PATH=/data/todosky.db
VOLUME /data
CMD ["python", "-m", "todosky", "run"]
