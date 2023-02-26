import boto3

ecs = boto3.client("ecs")
ssm = boto3.client("ssm")


def get_parameter(parameter):
    config = ssm.get_parameters(Names=[parameter])
    return config["Parameters"][0]["Value"]


def handler(event, context):
    try:
        service_arn = get_parameter("service-arn")
        # "arn:aws:ecs:us-west-2:093575270853:service/CodeStack-Ab2SampleServiceClusterC315275A-L2cC0gRxYdXs/CodeStack-Ab2SampleService751EF6ED-kAF055EKiWoV"
        cluster_arn = get_parameter("cluster-arn")
        # "arn:aws:ecs:us-west-2:093575270853:cluster/CodeStack-Ab2SampleServiceClusterC315275A-L2cC0gRxYdXs"

        expected_desired_count = event["desiredCount"]
        response = ecs.describe_services(
            cluster=cluster_arn,
            services=[service_arn],
        )

        desired_count = response["services"][0]["desiredCount"]
        running_count = response["services"][0]["runningCount"]

        # desired count passed by request vs desired count in ECS
        if expected_desired_count != desired_count:
            return {
                "statusCode": 200,
                "body": {
                    "status": "FAILED",
                    "message": "Desired count not equal to expected desired count",
                },
            }

        if desired_count != running_count:
            return {
                "statusCode": 200,
                "body": {
                    "status": "PENDING",
                    "message": "ECS service is scaling",
                },
            }
        else:
            return {
                "statusCode": 200,
                "body": {
                    "status": "SUCCEEDED",
                    "message": "Scaling succeeded",
                },
            }

    except Exception as e:
        print(e)
        return {
            "statusCode": 500,
            "body": {
                "status": "FAILED",
                "message": "Unknown error",
            },
        }
