FROM python:3.14-slim

ARG DB_FILE
RUN test -n "$DB_FILE" || (echo "DB_FILE build arg is required" && exit 1)

WORKDIR /app

COPY browse.py prusti_analysis.py ./
COPY issues/ issues/
COPY ${DB_FILE} ./

RUN pip install --no-cache-dir polars markdown

EXPOSE 8765

CMD ["python", "browse.py", "--port", "8765"]
