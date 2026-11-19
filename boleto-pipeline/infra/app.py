#!/usr/bin/env python3
import aws_cdk as cdk
from stack import BoletoPipelineStack

app = cdk.App()
BoletoPipelineStack(app, "BoletoPipeline", env=cdk.Environment(region="sa-east-1"))
app.synth()
