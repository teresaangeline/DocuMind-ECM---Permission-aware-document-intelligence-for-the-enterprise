FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY opspilot ./opspilot
EXPOSE 8000
CMD ["uvicorn", "opspilot.main:app", "--host", "0.0.0.0", "--port", "8000"]
