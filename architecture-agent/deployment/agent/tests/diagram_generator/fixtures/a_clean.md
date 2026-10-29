# Solution Architecture: Voice AI Assistant Platform

**Customer:** Northwind Support Services (fictional)
**Project:** Aria Voice Assistant
**Status:** Final, approved for build
**Author:** Solution Architecture team

## 1. Overview

Aria is a voice AI assistant platform that lets the customer's end users hold real-time spoken conversations with an AI agent from a web browser or a mobile app. The platform is deployed on AWS in a single Region (**us-east-1**) inside one VPC that spans **three Availability Zones**. Real-time media is handled by LiveKit (external, SaaS) and speech-to-text by Deepgram (external, SaaS). Reasoning and response generation use Amazon Bedrock.

## 2. Network Layout

- **Region:** us-east-1.
- **VPC:** one VPC (10.20.0.0/16) spanning three Availability Zones (us-east-1a, us-east-1b, us-east-1c).
- **Public subnets** (one per AZ) contain the Internet Gateway attachment, a NAT Gateway, and the Application Load Balancer. The Application Load Balancer terminates TLS using a certificate issued by AWS Certificate Manager (ACM).
- **Private subnets** (one per AZ) contain the Amazon ECS cluster running on EC2 capacity, the self-managed Qdrant vector database (also on ECS), and the Amazon DocumentDB cluster.
- Outbound internet access from the private subnets (to LiveKit, Deepgram and image pulls) goes through the NAT Gateway.

## 3. Compute and Application Services

The Amazon ECS cluster (EC2 launch type) runs three services:

1. **FrontEnd** - serves the web application and mobile API gateway layer.
2. **Backend** - session management, business logic, conversation history, and orchestration of retrieval and LLM calls.
3. **VoiceAgent** - joins LiveKit rooms as a participant, streams audio to Deepgram, and streams responses back to the user.

Container images for all three services are stored in Amazon ECR and pulled by the ECS container instances.

## 4. Data and AI Services

- **Amazon Bedrock** provides the large language model used by the Backend and VoiceAgent services.
- **Qdrant** is the vector database, deployed as a self-managed ECS service in the private subnets. It stores embeddings used for retrieval-augmented generation.
- **Amazon DocumentDB** cluster stores sessions, transcripts and user configuration.
- **Amazon S3** stores call recordings and uploaded knowledge documents.

## 5. Security and Operations

| Capability | Service |
|---|---|
| Encryption keys | AWS KMS (encrypts S3, DocumentDB and EBS volumes) |
| Identity and access | AWS IAM (task roles and instance roles) |
| Monitoring and logs | Amazon CloudWatch |
| API audit trail | AWS CloudTrail |
| Configuration compliance | AWS Config |
| Threat detection | Amazon GuardDuty |
| Findings aggregation | AWS Security Hub |

## 6. Request Flow

1. The end user opens the Aria web app in a browser or the mobile app.
2. The request reaches the Application Load Balancer in the public subnets over HTTPS (443).
3. The Application Load Balancer forwards the request to the FrontEnd service on Amazon ECS.
4. FrontEnd calls the Backend service, which creates a session and requests a LiveKit access token.
5. The end user's browser or mobile app connects to LiveKit (external) for real-time audio over WebRTC.
6. The VoiceAgent service joins the same LiveKit room and receives the user's audio stream.
7. VoiceAgent streams the audio to Deepgram (external) and receives the transcript.
8. The Backend queries Qdrant for relevant knowledge chunks using the transcript.
9. The Backend invokes Amazon Bedrock with the transcript and retrieved context to generate the answer.
10. The answer is passed to VoiceAgent, which returns synthesized audio to the user through LiveKit.
11. The Backend stores the session and transcript in Amazon DocumentDB, and recordings and documents in Amazon S3.

## 7. Components Table

| Component | AWS Service / Technology | Placement | Purpose |
|---|---|---|---|
| End users | Browser / mobile app | Internet | Voice users of the platform |
| Internet Gateway | Amazon VPC Internet Gateway | VPC edge | Internet access for public subnets |
| NAT Gateway | Amazon VPC NAT Gateway | Public subnets | Outbound access for private subnets |
| Application Load Balancer | Elastic Load Balancing | Public subnets | HTTPS entry point, ACM certificate |
| ACM certificate | AWS Certificate Manager | Region | TLS certificate for the load balancer |
| FrontEnd | Amazon ECS on EC2 | Private subnets | Web and mobile API layer |
| Backend | Amazon ECS on EC2 | Private subnets | Business logic and orchestration |
| VoiceAgent | Amazon ECS on EC2 | Private subnets | Real-time voice agent worker |
| Amazon ECR | Amazon Elastic Container Registry | Region | Container image registry |
| LiveKit | External SaaS | Outside AWS | Real-time media (WebRTC) |
| Deepgram | External SaaS | Outside AWS | Speech-to-text |
| Amazon Bedrock | Amazon Bedrock | Region | LLM inference |
| Qdrant | Self-managed on Amazon ECS | Private subnets | Vector database |
| Amazon DocumentDB | Amazon DocumentDB | Private subnets | Sessions and transcripts |
| Amazon S3 | Amazon S3 | Region | Recordings and documents |
| AWS KMS | AWS Key Management Service | Region | Encryption keys |
| AWS IAM | AWS Identity and Access Management | Account | Roles and policies |
| Amazon CloudWatch | Amazon CloudWatch | Region | Metrics and logs |
| AWS CloudTrail | AWS CloudTrail | Account | Audit logging |
| AWS Config | AWS Config | Account | Compliance tracking |
| Amazon GuardDuty | Amazon GuardDuty | Account | Threat detection |
| AWS Security Hub | AWS Security Hub | Account | Security findings aggregation |
