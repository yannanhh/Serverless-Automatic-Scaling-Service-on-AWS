import aws_cdk as cdk

from code.code_stack import CodeStack
from code.config import DEFAULT_ACCOUNT, DEFAULT_REGION

app = cdk.App()

CodeStack(
    app,
    "CodeStack",
    env=cdk.Environment(account=DEFAULT_ACCOUNT, region=DEFAULT_REGION),
)

app.synth()
