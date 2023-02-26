import os
import json
from datetime import datetime, timedelta
import boto3

events = boto3.client("events")
ssm = boto3.client("ssm")
db = boto3.resource("dynamodb")
ecs = boto3.client("ecs")


def get_parameter(parameter):
    config = ssm.get_parameters(Names=[parameter])
    return config["Parameters"][0]["Value"]


def get_desired_count(popularity):
    # mapping popularity to ECS tasks size
    mapping = {"hot": 3, "medium": 2, "cold": 1}
    return mapping.get(popularity) or 1


def handler(event, context):
    try:
        # Environment vars and parameter store
        SCHEDULER_ROLE_ARN = os.getenv("SCHEDULER_ROLE_ARN")
        SCALING_WORKFLOW_ARN = os.getenv("SCALING_WORKFLOW_ARN")

        service_arn = get_parameter("service-arn")
        # "arn:aws:ecs:us-west-2:093575270853:service/CodeStack-Ab2SampleServiceClusterC315275A-L2cC0gRxYdXs/CodeStack-Ab2SampleService751EF6ED-kAF055EKiWoV"
        cluster_arn = get_parameter("cluster-arn")
        # "arn:aws:ecs:us-west-2:093575270853:cluster/CodeStack-Ab2SampleServiceClusterC315275A-L2cC0gRxYdXs"
        table_name = get_parameter("table-name")

        # Process input
        params = json.loads(event["body"])
        team = params.get("team") or "Unspecified"
        launch_time = params.get("launchTime")  # %Y%m%d%H%M%S
        popularity = params.get("popularity")
        desired_count = get_desired_count(popularity)
        wait_time = params.get("waitTime") or 60  # seconds

        now = datetime.now()
        scheduled = datetime.strptime(launch_time, "%Y%m%d%H%M%S") if launch_time is not None else now + timedelta(minutes=3)
        scalingRequestId = f"ScheduledScalingAt{now.strftime('%Y%m%d%H%M%S')}"

        services = ecs.describe_services(
            cluster=cluster_arn,
            services=[service_arn],
        )
        original_desired_count = services["services"][0]["desiredCount"]

        # Schedule
        # EventBridge scheduled rule is not EventBridge Scheduler, you will need to use the latest boto3 to use scheduler,
        # but Lambda default python env does not use latest boto3
        scheduled_rule_name = scalingRequestId
        events.put_rule(
            Name=scheduled_rule_name,
            # https://www.geeksforgeeks.org/formatted-string-literals-f-strings-python/
            ScheduleExpression=f"cron({scheduled.minute} {scheduled.hour} {scheduled.day} {scheduled.month} ? {scheduled.year})",
            State="ENABLED",
        )
        events.put_targets(
            Rule=scheduled_rule_name,
            Targets=[
                {
                    "Id": f"{scheduled_rule_name}-01",
                    "Arn": SCALING_WORKFLOW_ARN,
                    "RoleArn": SCHEDULER_ROLE_ARN,
                    "Input": json.dumps(
                        {
                            "scalingRequestId": scalingRequestId,
                            "clusterArn": cluster_arn,
                            "serviceArn": service_arn,
                            "desiredCount": desired_count,
                            "originalDesiredCount": original_desired_count,
                            "waitTime": wait_time,
                            "scheduled": scheduled.strftime("%Y%m%d%H%M%S"),
                        }
                    ),
                }
            ],
        )

        # Put business info into DynamoDB
        table = db.Table(table_name) 
        table.put_item(
            Item={
                "scalingRequestId": scalingRequestId,
                "clusterArn": cluster_arn,
                "serviceArn": service_arn,
                "desiredCount": desired_count,
                "originalDesiredCount": original_desired_count,
                "waitTime": wait_time,
                "scheduled": scheduled.strftime("%Y%m%d%H%M%S"),
                "status": "scheduled",
                "team": team,
                "popularity": popularity,
            }
        )

        # Response
        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "status": "SUCCEEDED",
                    "message": "Succeeded",
                }
            ),
        }
    except Exception as e:
        print(e)
        return {
            "statusCode": 500,
            "body": json.dumps(
                {
                    "status": "FAILED",
                    "message": "Unknown error",
                }
            ),
        }
