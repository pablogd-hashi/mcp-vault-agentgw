FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
COPY lab ./lab
COPY agent ./agent
RUN pip install --no-cache-dir ".[agent]"
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "agent.run"]
