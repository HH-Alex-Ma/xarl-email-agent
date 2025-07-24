FROM python:3.11-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

FROM python:3.11-slim
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY ./app ./app
COPY ./fonts ./fonts
COPY ./downloaded_emails ./downloaded_emails
COPY ./processed_emails ./processed_emails
COPY ./workflow_responses ./workflow_responses
COPY ./xarl_email_workflow.py ./
COPY ./download_email_as_eml.py ./
USER 1001
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"] 