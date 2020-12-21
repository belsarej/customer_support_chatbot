FROM python:3.6
WORKDIR /customer_service_chatbot
COPY . ./
RUN pip install -r requirements.txt 
RUN python3 -m spacy download en
# Expose port 5000
EXPOSE 5000
ENV PORT 5000
# Use gunicorn as the entrypoint
CMD exec gunicorn --bind :$PORT app:app --workers 1 --threads 1 --timeout 60
