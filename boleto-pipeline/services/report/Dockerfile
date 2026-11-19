FROM public.ecr.aws/lambda/python:3.12
RUN pip install openpyxl boto3
COPY src/ ${LAMBDA_TASK_ROOT}/
COPY services/report/handler.py ${LAMBDA_TASK_ROOT}/
CMD ["handler.handler"]
