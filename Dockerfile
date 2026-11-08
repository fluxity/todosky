FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml ./
COPY todosky ./todosky
RUN pip install --no-cache-dir --root-user-action=ignore .
# Run from the data volume so a relative STATE_PATH (as in .env.example) also lands on it.
WORKDIR /data
ENV STATE_PATH=/data/todosky.db
VOLUME /data
CMD ["python", "-m", "todosky", "run"]
