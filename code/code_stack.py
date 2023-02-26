import json
from aws_cdk import (
    aws_ec2 as ec2,
    aws_ecs as ecs,
    aws_ecs_patterns as ecs_patterns,
    aws_stepfunctions as sfn,
    aws_ssm as ssm,
    aws_iam as iam,
    aws_events as events,
    aws_events_targets as events_targets,
    aws_logs as logs,
    aws_lambda,
    aws_apigateway as apigateway,
    aws_dynamodb as dynamodb,
    Stack,
)
from constructs import Construct

from code.config import DEFAULT_ACCOUNT, DEFAULT_REGION


class CodeStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Sample service
        vpc = ec2.Vpc(self, "Ab2SampleServiceVpc", max_azs=2)

        cluster = ecs.Cluster(self, "Ab2SampleServiceCluster", vpc=vpc)

        fargate_service = ecs_patterns.ApplicationLoadBalancedFargateService(
            self,
            "Ab2SampleService",
            cluster=cluster,
            task_image_options=ecs_patterns.ApplicationLoadBalancedTaskImageOptions(
                image=ecs.ContainerImage.from_registry("amazon/amazon-ecs-sample")
            ),
            desired_count=1,
            public_load_balancer=True,
        )

        autoScalingTarget = fargate_service.service.auto_scale_task_count(
            min_capacity=1, max_capacity=20
        )

        autoScalingTarget.scale_on_cpu_utilization(
            "Ab2SampleServiceCpuScaling",
            target_utilization_percent=50,
            disable_scale_in=True,
        )

        cluster_arn = fargate_service.cluster.cluster_arn
        service_arn = fargate_service.service.service_arn

        # Event bus
        event_bus = events.EventBus(self, "Ab2EventBus", event_bus_name="Ab2EventBus")

        log_all_rule = events.Rule(
            self,
            "Ab2EventBusLogAllRule",
            event_bus=event_bus,
            rule_name="Ab2EventBusLogAllRule",
            event_pattern={"source": ["ab2.scalingworkflow"]},
        )

        log_all_rule.add_target(
            events_targets.CloudWatchLogGroup(
                log_group=logs.LogGroup(
                    self,
                    "Ab2EventsLogAllLogGroup",
                    log_group_name="/aws/events/ab2-events-log-all",
                )
            )
        )

        # Scaling service dynamoDB database
        table_name = "Ab2ScalingServiceDatabase"
        db = dynamodb.Table(
            self,
            table_name,
            table_name=table_name,
            partition_key=dynamodb.Attribute(
                name="scalingRequestId", type=dynamodb.AttributeType.STRING
            ),
        )

        # Scaling workflow
        check_function_name = "Ab2ScalingServiceCheckFunction"
        check_function_arn = f"arn:aws:lambda:{DEFAULT_REGION}:{DEFAULT_ACCOUNT}:function:{check_function_name}:$LATEST"
        check_function = aws_lambda.Function(
            self,
            check_function_name,
            function_name=check_function_name,
            runtime=aws_lambda.Runtime.PYTHON_3_8,
            code=aws_lambda.Code.from_asset("./functions"),
            handler="check.handler",
            vpc=vpc,
        )

        check_function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                resources=["*"],
                actions=["ecs:DescribeServices"],
            )
        )

        check_function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                resources=["*"],
                actions=["ssm:Get*"],
            )
        )

        db.grant_read_write_data(check_function)


        scaling_workflow_role = iam.Role(
            self,
            "Ab2ScalingWorkflowRole",
            assumed_by=iam.ServicePrincipal("states.amazonaws.com"),
        )


        scaling_workflow_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                resources=["*"],
                actions=["lambda:InvokeFunction"],
            )
        )

        scaling_workflow_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                resources=["*"],
                actions=["ecs:UpdateService"],
            )
        )

        event_bus.grant_put_events_to(scaling_workflow_role)

        db.grant_read_write_data(scaling_workflow_role)

        with open("./statemachine/scaling-workflow-full.asl.json", "r") as f:
            definition = json.load(f)

        definition["States"]["Check Scale Out"]["Parameters"][
            "FunctionName"
        ] = check_function_arn
        definition["States"]["Check Scale In"]["Parameters"][
            "FunctionName"
        ] = check_function_arn

        scaling_workflow = sfn.CfnStateMachine(
            self,
            "Ab2ScalingWorkflow",
            state_machine_name="ScalingWorkflow",
            state_machine_type="STANDARD",
            role_arn=scaling_workflow_role.role_arn,
            definition_string=json.dumps(definition),
        )

        # Scaling service
        # Role for eventBridge, pass to Lambda env , then pass role to eventBridge
        scheduler_role = iam.Role(
            self,
            "Ab2SchedulerRole",
            assumed_by=iam.ServicePrincipal("events.amazonaws.com"),
        )

        # EventBridge scheduled rule role
        scheduler_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                resources=["*"],
                actions=["states:StartExecution"],
            )
        )

        schedule_function = aws_lambda.Function(
            self,
            "Ab2ScalingServiceScheduleFunction",
            function_name="Ab2ScalingServiceScheduleFunction",
            runtime=aws_lambda.Runtime.PYTHON_3_8,
            code=aws_lambda.Code.from_asset("./functions"),
            handler="schedule.handler",
            environment={
                "SCHEDULER_ROLE_ARN": scheduler_role.role_arn,
                "SCALING_WORKFLOW_ARN": scaling_workflow.attr_arn,
            },
            vpc=vpc,
        )

        schedule_function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                resources=[scheduler_role.role_arn],
                actions=["iam:PassRole"],
            )
        )

        schedule_function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                resources=["*"],
                actions=["events:PutRule", "events:PutTargets"],
            )
        )

        schedule_function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                resources=["*"],
                actions=["ssm:Get*"],
            )
        )

        schedule_function.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                resources=["*"],
                actions=["ecs:DescribeServices"],
            )
        )

        db.grant_read_write_data(schedule_function)

        # scaling_workflow_role = iam.Role(
        #     self,
        #     "Ab2ScalingWorkflowRole",
        #     assumed_by=iam.ServicePrincipal("states.amazonaws.com"),
        # )


        # scaling_workflow_role.add_to_policy(
        #     iam.PolicyStatement(
        #         effect=iam.Effect.ALLOW,
        #         resources=["*"],
        #         actions=["lambda:InvokeFunction"],
        #     )
        # )

        # scaling_workflow_role.add_to_policy(
        #     iam.PolicyStatement(
        #         effect=iam.Effect.ALLOW,
        #         resources=["*"],
        #         actions=["ecs:UpdateService"],
        #     )
        # )

        # api_gateway_role = iam.Role(
        #     self,
        #     "Ab2ScalingWorkflowRole",
        #     assumed_by=iam.ServicePrincipal("cloudshell.amazonaws.com"),
    
        # )

        # api_gateway_role.add_to_policy(
        #     iam.PolicyStatement(
        #         effect=iam.Effect.ALLOW,
        #         resources=["*"],
        #         actions=["execute-api:Invoke"],
        #         principals=
        #     )
        # )

        # api_gateway_policy = iam.PolicyDocument(
        #     statements=[iam.PolicyStatement(
        #         actions=["execute-api:Invoke"],
        #         principals=[iam.AccountPrincipal("116266104059")],
        #         resources=["*"] 
        #     )]
        # )

        apigateway.LambdaRestApi(
            self,
            "Ab2ScalingServiceAPI",
            handler=schedule_function,  
            endpoint_configuration=apigateway.EndpointConfiguration(
                types=[apigateway.EndpointType.REGIONAL]
            )
            # policy=api_gateway_policy
        )

        # Config: save parameters such as ARN
        ssm.StringParameter(
            self,
            "Ab2ConfigClusterArn",
            parameter_name="cluster-arn",
            string_value=cluster_arn,
            description="AB2 sample service cluster arn",
        )

        ssm.StringParameter(
            self,
            "Ab2ConfigServiceArn",
            parameter_name="service-arn",
            string_value=service_arn,
            description="AB2 sample service service arn",
        )

        ssm.StringParameter(
            self,
            "Ab2TableName",
            parameter_name="table-name",
            string_value=table_name,
            description="AB2 DynamoDB table name",
        )