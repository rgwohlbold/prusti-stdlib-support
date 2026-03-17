FROM python:3.14-slim

WORKDIR /app

COPY browse.py prusti_analysis.py ./
COPY issues/ issues/
# Copy all database files — at least one prusti-*.db must exist in the build context
COPY *.db ./

RUN pip install --no-cache-dir polars markdown

EXPOSE 8765

CMD ["python", "browse.py", "--port", "8765"]
