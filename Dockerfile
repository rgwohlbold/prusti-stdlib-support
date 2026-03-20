FROM python:3.14-slim AS builder

WORKDIR /build

RUN pip install --no-cache-dir polars markdown

COPY issues/ issues/
COPY browse.py prusti_analysis.py ./
COPY *.db ./

RUN python browse.py --output /build/static


FROM nginx:alpine

COPY --from=builder /build/static /usr/share/nginx/html

EXPOSE 80
