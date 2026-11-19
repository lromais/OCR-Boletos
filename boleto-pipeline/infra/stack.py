"""Infra AWS (CDK v2, Python).

S3 raw/ --(event)--> SQS emails --> ECS Fargate classifier (EmbeddingGemma 2)
                                        |
                       S3 attachments/ + SQS ocr --> ECS Fargate OCR (Indra + zxing)
                                                         |
                                                    DynamoDB boletos
                                                         |
                       EventBridge (cron) --> Lambda report --> S3 reports/*.xlsx (+ SES)
"""
from pathlib import Path

from aws_cdk import (Duration, RemovalPolicy, Stack, aws_applicationautoscaling as appscaling,
                     aws_cloudwatch as cw, aws_dynamodb as ddb, aws_ec2 as ec2, aws_ecs as ecs,
                     aws_ecs_patterns as ecsp, aws_events as events, aws_events_targets as targets,
                     aws_iam as iam, aws_kms as kms, aws_lambda as lambda_, aws_s3 as s3,
                     aws_s3_notifications as s3n, aws_secretsmanager as sm, aws_sqs as sqs)
from constructs import Construct

RAIZ = str(Path(__file__).resolve().parents[1])


class BoletoPipelineStack(Stack):
    def __init__(self, scope: Construct, cid: str, **kw):
        super().__init__(scope, cid, **kw)

        chave = kms.Key(self, "Chave", enable_key_rotation=True)
        # chave separada p/ filas: a notificação S3->SQS edita a key policy e,
        # com a mesma chave do bucket, gera dependência circular no CloudFormation
        chave_filas = kms.Key(self, "ChaveFilas", enable_key_rotation=True)

        bucket = s3.Bucket(
            self, "Dados", encryption=s3.BucketEncryption.KMS, encryption_key=chave,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL, enforce_ssl=True,
            versioned=True, removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(prefix="raw/", expiration=Duration.days(90)),
                s3.LifecycleRule(prefix="reports/", expiration=Duration.days(30)),
            ])

        def fila(nome: str, visibilidade: int) -> sqs.Queue:
            dlq = sqs.Queue(self, f"{nome}Dlq", retention_period=Duration.days(14),
                            encryption=sqs.QueueEncryption.KMS, encryption_master_key=chave_filas)
            return sqs.Queue(self, nome, visibility_timeout=Duration.seconds(visibilidade),
                             encryption=sqs.QueueEncryption.KMS, encryption_master_key=chave_filas,
                             dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=3, queue=dlq))

        fila_emails = fila("FilaEmails", 900)
        fila_ocr = fila("FilaOcr", 900)
        bucket.add_event_notification(s3.EventType.OBJECT_CREATED, s3n.SqsDestination(fila_emails),
                                      s3.NotificationKeyFilter(prefix="raw/"))

        tabela = ddb.Table(
            self, "Boletos", partition_key=ddb.Attribute(name="pk", type=ddb.AttributeType.STRING),
            billing_mode=ddb.BillingMode.PAY_PER_REQUEST, point_in_time_recovery_specification=ddb.PointInTimeRecoverySpecification(point_in_time_recovery_enabled=True),
            encryption=ddb.TableEncryption.CUSTOMER_MANAGED, encryption_key=chave,
            removal_policy=RemovalPolicy.RETAIN)

        env_comum = {"BUCKET_NAME": bucket.bucket_name, "TABLE_NAME": tabela.table_name,
                     "OCR_QUEUE_URL": fila_ocr.queue_url}

        # --------------------------- ECS (workers) --------------------------- #
        vpc = ec2.Vpc(self, "Vpc", max_azs=2, nat_gateways=1)
        vpc.add_gateway_endpoint("S3", service=ec2.GatewayVpcEndpointAwsService.S3)
        vpc.add_gateway_endpoint("Ddb", service=ec2.GatewayVpcEndpointAwsService.DYNAMODB)
        cluster = ecs.Cluster(self, "Cluster", vpc=vpc, enable_fargate_capacity_providers=True,
                              container_insights_v2=ecs.ContainerInsights.ENABLED)

        spot_primeiro = [
            ecs.CapacityProviderStrategy(capacity_provider="FARGATE_SPOT", weight=4),
            ecs.CapacityProviderStrategy(capacity_provider="FARGATE", weight=1, base=0),
        ]
        # backlog -> nº de tasks. scale-to-zero quando a fila esvazia.
        degraus = [appscaling.ScalingInterval(upper=0, change=-1),
                   appscaling.ScalingInterval(lower=1, change=+1),
                   appscaling.ScalingInterval(lower=50, change=+3),
                   appscaling.ScalingInterval(lower=300, change=+6)]

        classificador = ecsp.QueueProcessingFargateService(
            self, "Classificador", cluster=cluster, queue=fila_emails,
            image=ecs.ContainerImage.from_asset(RAIZ, file="services/classifier_worker/Dockerfile"),
            cpu=2048, memory_limit_mib=6144,          # 270M params fp32 + torch
            min_scaling_capacity=0, max_scaling_capacity=10, scaling_steps=degraus,
            capacity_provider_strategies=spot_primeiro,
            environment={**env_comum, "EMAIL_QUEUE_URL": fila_emails.queue_url,
                         "EMBEDDING_DIM": "256", "LIMIAR_BOLETO": "0.55"},
            runtime_platform=ecs.RuntimePlatform(cpu_architecture=ecs.CpuArchitecture.X86_64))

        ocr = ecsp.QueueProcessingFargateService(
            self, "Ocr", cluster=cluster, queue=fila_ocr,
            image=ecs.ContainerImage.from_asset(RAIZ, file="services/ocr_worker/Dockerfile"),
            cpu=4096, memory_limit_mib=8192,          # Indra é CPU-bound: mais vCPU = mais páginas/s
            min_scaling_capacity=0, max_scaling_capacity=40, scaling_steps=degraus,
            capacity_provider_strategies=spot_primeiro,
            environment={**env_comum, "OCR_THREADS": "4", "OCR_MAX_PAGES": "5"},
            runtime_platform=ecs.RuntimePlatform(cpu_architecture=ecs.CpuArchitecture.ARM64))

        for svc in (classificador, ocr):
            bucket.grant_read_write(svc.task_definition.task_role)
            tabela.grant_read_write_data(svc.task_definition.task_role)
        fila_ocr.grant_send_messages(classificador.task_definition.task_role)

        # --------------------------- Lambdas -------------------------------- #
        segredo_imap = sm.Secret(self, "ImapCreds", description='{"host","user","password","folder"}')
        ingest = lambda_.DockerImageFunction(
            self, "Ingest", code=lambda_.DockerImageCode.from_image_asset(RAIZ, file="services/ingest/Dockerfile"),
            timeout=Duration.minutes(5), memory_size=512, reserved_concurrent_executions=1,
            environment={"BUCKET_NAME": bucket.bucket_name, "IMAP_SECRET_ID": segredo_imap.secret_arn})
        bucket.grant_put(ingest)
        chave.grant_encrypt_decrypt(ingest)
        segredo_imap.grant_read(ingest)
        events.Rule(self, "IngestCron", schedule=events.Schedule.rate(Duration.minutes(5)),
                    targets=[targets.LambdaFunction(ingest)])

        relatorio = lambda_.DockerImageFunction(
            self, "Relatorio", code=lambda_.DockerImageCode.from_image_asset(RAIZ, file="services/report/Dockerfile"),
            timeout=Duration.minutes(10), memory_size=2048, environment=env_comum)
        bucket.grant_read_write(relatorio)
        tabela.grant_read_data(relatorio)
        relatorio.add_to_role_policy(iam.PolicyStatement(actions=["ses:SendEmail"], resources=["*"]))
        events.Rule(self, "RelatorioDiario",
                    schedule=events.Schedule.cron(minute="0", hour="10"),  # 07:00 BRT
                    targets=[targets.LambdaFunction(relatorio)])

        # --------------------------- Observabilidade ------------------------ #
        for f in (fila_emails, fila_ocr):
            cw.Alarm(self, f"{f.node.id}DlqAlarme",
                     metric=f.dead_letter_queue.queue.metric_approximate_number_of_messages_visible(),
                     threshold=1, evaluation_periods=1)
            cw.Alarm(self, f"{f.node.id}IdadeAlarme",
                     metric=f.metric_approximate_age_of_oldest_message(), threshold=1800,
                     evaluation_periods=3)
