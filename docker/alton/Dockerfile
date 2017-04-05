FROM python:2.7.13
COPY requirements.txt /opt/alton/requirements.txt
WORKDIR /opt/alton
RUN pip install -r requirements.txt
CMD [ "/opt/alton/run_alton.py" ]
