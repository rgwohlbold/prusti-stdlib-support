FROM python:3.14-slim AS builder

WORKDIR /build

RUN pip install --no-cache-dir polars markdown

COPY issues/ issues/
COPY generate_html_report.py analysis.py ./
COPY *.db ./

RUN python generate_html_report.py --output /build/static


FROM nginx:alpine

COPY --from=builder /build/static /usr/share/nginx/html

EXPOSE 80
