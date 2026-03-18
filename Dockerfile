FROM python:3.14-slim AS builder

WORKDIR /build

COPY browse.py prusti_analysis.py ./
COPY issues/ issues/
COPY *.db ./

RUN pip install --no-cache-dir polars markdown
RUN python browse.py --output /build/static


FROM nginx:alpine

COPY --from=builder /build/static /usr/share/nginx/html

EXPOSE 80
