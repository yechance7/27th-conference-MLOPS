terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.45"
    }
  }
}

provider "aws" {
  region = var.aws_region
}

variable "aws_region" {
  description = "AWS region for the database stack."
  type        = string
  default     = "ap-northeast-2"
}

variable "project" {
  description = "Name prefix for tags and resources."
  type        = string
  default     = "mlops-db"
}

variable "vpc_cidr" {
  description = "CIDR block for the database VPC."
  type        = string
  default     = "10.52.0.0/16"
}

variable "private_subnet_cidr" {
  description = "CIDR block for the private subnet hosting the DB/ETL EC2."
  type        = string
  default     = "10.52.1.0/24"
}

variable "instance_type_db" {
  description = "Instance type for the TimescaleDB EC2."
  type        = string
  default     = "t3.medium"
}

variable "ssh_key_name" {
  description = "EC2 key pair for emergency access (optional when using SSM Session Manager)."
  type        = string
  default     = ""
}

variable "db_ingress_cidrs" {
  description = "CIDR ranges that may reach the DB service port (e.g., collector SG or bastion subnet)."
  type        = list(string)
  default     = []
}

variable "db_port" {
  description = "Port exposed by the database service (PostgreSQL/Timescale)."
  type        = number
  default     = 5432
}

variable "landing_bucket_arn" {
  description = "ARN of the landing bucket the DB/ETL host must read."
  type        = string
}

variable "tags" {
  description = "Common tags applied to all resources."
  type        = map(string)
  default     = {}
}

locals {
  tags = merge(var.tags, { Project = var.project })
}

data "aws_caller_identity" "current" {}

resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = merge(local.tags, { Name = "${var.project}-vpc" })
}

resource "aws_subnet" "private" {
  vpc_id            = aws_vpc.main.id
  cidr_block        = var.private_subnet_cidr
  availability_zone = "${var.aws_region}a"

  tags = merge(local.tags, { Name = "${var.project}-private" })
}

resource "aws_route_table" "private" {
  vpc_id = aws_vpc.main.id
  tags   = merge(local.tags, { Name = "${var.project}-rt-private" })
}

resource "aws_route_table_association" "private" {
  subnet_id      = aws_subnet.private.id
  route_table_id = aws_route_table.private.id
}

# S3 Gateway endpoint allows the private subnet to reach S3 without public egress.
resource "aws_vpc_endpoint" "s3" {
  vpc_id             = aws_vpc.main.id
  service_name       = "com.amazonaws.${var.aws_region}.s3"
  vpc_endpoint_type  = "Gateway"
  route_table_ids    = [aws_route_table.private.id]

  tags = merge(local.tags, { Name = "${var.project}-vpce-s3" })
}

# Interface endpoints for SSM/CloudWatch so the EC2 can be managed without Internet.
locals {
  interface_endpoint_services = [
    "ssm",
    "ec2messages",
    "ssmmessages",
    "logs"
  ]
}

resource "aws_security_group" "db" {
  name        = "${var.project}-db-sg"
  description = "Private DB/ETL host security group"
  vpc_id      = aws_vpc.main.id

  dynamic "ingress" {
    for_each = length(var.db_ingress_cidrs) == 0 ? [] : var.db_ingress_cidrs
    content {
      description = "DB client access"
      from_port   = var.db_port
      to_port     = var.db_port
      protocol    = "tcp"
      cidr_blocks = [ingress.value]
    }
  }

  ingress {
    description     = "Allow HTTPS from self for SSM VPCE"
    from_port       = 443
    to_port         = 443
    protocol        = "tcp"
    self            = true
  }

  # Allow HTTPS egress so interface endpoints + S3 can be reached.
  egress {
    description = "Allow HTTPS egress"
    from_port   = 443
    to_port     = 443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.tags, { Name = "${var.project}-db-sg" })
}

resource "aws_vpc_endpoint" "interface" {
  for_each            = toset(local.interface_endpoint_services)
  vpc_id              = aws_vpc.main.id
  subnet_ids          = [aws_subnet.private.id]
  service_name        = "com.amazonaws.${var.aws_region}.${each.key}"
  vpc_endpoint_type   = "Interface"
  private_dns_enabled = true
  security_group_ids  = [aws_security_group.db.id]

  tags = merge(local.tags, { Name = "${var.project}-vpce-${each.key}" })
}

resource "aws_iam_role" "db" {
  name               = "${var.project}-host-role"
  assume_role_policy = data.aws_iam_policy_document.assume_role.json
  tags               = local.tags
}

data "aws_iam_policy_document" "assume_role" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role_policy" "db" {
  name = "${var.project}-host-policy"
  role = aws_iam_role.db.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect   = "Allow"
        Action   = ["s3:GetObject", "s3:ListBucket"]
        Resource = [
          var.landing_bucket_arn,
          "${var.landing_bucket_arn}/*"
        ]
      },
      {
        Effect   = "Allow"
        Action   = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = [
          "ssm:DescribeParameters",
          "ssm:GetParameter",
          "ssm:GetParameters"
        ]
        Resource = "*"
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.db.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

resource "aws_iam_instance_profile" "db" {
  name = "${var.project}-instance-profile"
  role = aws_iam_role.db.name
}

data "aws_ssm_parameter" "al2023" {
  name = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
}

locals {
  user_data = <<-EOT
              #!/bin/bash
              set -xeuo pipefail
              dnf update -y
              dnf install -y postgresql15 jq amazon-ssm-agent
              systemctl enable --now amazon-ssm-agent
              useradd -m dbadmin || true
              mkdir -p /data/timescale
              chown dbadmin:dbadmin /data/timescale
              echo "DB host bootstrap complete" | systemd-cat -t db-bootstrap
          EOT
}

resource "aws_instance" "db_host" {
  ami                    = data.aws_ssm_parameter.al2023.value
  instance_type          = var.instance_type_db
  subnet_id              = aws_subnet.private.id
  iam_instance_profile   = aws_iam_instance_profile.db.name
  vpc_security_group_ids = [aws_security_group.db.id]
  associate_public_ip_address = false
  key_name                    = var.ssh_key_name != "" ? var.ssh_key_name : null
  user_data                   = local.user_data

  tags = merge(local.tags, { Name = "${var.project}-host" })
}

output "db_instance_id" {
  value = aws_instance.db_host.id
}

output "db_security_group_id" {
  value = aws_security_group.db.id
}

output "private_subnet_id" {
  value = aws_subnet.private.id
}
