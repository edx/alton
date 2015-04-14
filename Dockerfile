FROM python:2.7.7
ADD . /opt/alton
WORKDIR /opt/alton
RUN pip install -r requirements.txt
CMD [ "/opt/alton/run_alton.py" ]
