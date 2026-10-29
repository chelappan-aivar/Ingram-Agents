# Deploying with the AWS CLI only (ZIP route: no Docker, no ECR)
> **Superseded (deployment).** Deployment is now one script: see `../deployment/README.md` (`deployment/scripts/deploy.sh` + `deployment/template.yaml`). The manual CLI/console steps, IAM JSON files, `settings.py` constants and file paths below describe the earlier layout and are kept for reference only. The agent now reads its configuration from environment variables set by the template.


> **Configuration changed: no environment variables.** The agent no longer reads any environment variable. All settings
> (profile, region, model ids, limits, storage) are fixed in `discovery_agent/settings.py`. Where this document tells you to
> set `DISCOVERY_BUCKET`, `DISCOVERY_KMS_KEY_ID`, `DISCOVERY_LOCAL_ROOT`, `AWS_PROFILE_NAME`, `AWS_REGION`, any `*_MODEL_ID`,
> `BEDROCK_READ_TIMEOUT`, `MAX_OUTPUT_TOKENS` or `CORPUS_TOKEN_BUDGET`, **ignore it** and edit `settings.py` instead.
> `S3_BUCKET` and `S3_KMS_KEY_ID` are already set there (copied from `build/runtime_env.json`; existence not verified). The
> runtime uses S3 automatically (no local AWS profile); a development machine uses `samples/_store`. Rebuild the package/image
> after any change to `settings.py`: older artifacts under `build/` still carry the previous environment-variable setup.

Every resource is created with plain AWS CLI commands, in order. Run each block in a terminal opened in the repository folder. This replaces the container steps of `docs/MANUAL_DEPLOYMENT_RUNBOOK.md` (that file's console steps for KMS, S3, the Lambda and the notification still describe the same resources).

> **Status:** nothing here has been run against AWS. Verified without AWS: the ZIP builds (35 MB, all native libraries ARM64), the `create-agent-runtime` parameters validate against boto3's service definition, and the installed AWS CLI (2.34.37) has these commands and flags. Not verified: any of the commands against your account, and a real run from a ZIP.
>
> **Which CLI:** this uses the **AWS CLI** (`aws ...`). It does not use the separate Node-based AgentCore CLI (`agentcore create/deploy`) from the pasted text: that tool generates its own project and a CDK/CloudFormation stack, which does not fit an existing codebase and your Terraform plan. Both end in the same AgentCore API call.

## 0. Setup (repeat in every new terminal)

```bash
export AWS_PROFILE=<your profile>          # then: aws sso login --profile $AWS_PROFILE
export REGION=us-east-1
export AWS_REGION=$REGION AWS_DEFAULT_REGION=$REGION
export ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
export BUCKET=discovery-agent-$ACCOUNT_ID-$REGION
export ROLE_ARN=arn:aws:iam::$ACCOUNT_ID:role/DiscoveryAgentRuntimeRole
mkdir -p build
```
Steps 1 and 5 print more values to export (`KMS_KEY_ARN`, `RUNTIME_ID`, `RUNTIME_ARN`); add them to each new terminal too.

Your own identity needs permission to create KMS keys, S3 buckets, IAM roles, Lambda functions and AgentCore runtimes, plus `iam:PassRole` on the runtime role. An administrator role is simplest for a first deployment.

---

## 1. KMS key
```bash
KEY_ID=$(aws kms create-key --description "Discovery agent data" --query KeyMetadata.KeyId --output text)
aws kms create-alias --alias-name alias/discovery-agent --target-key-id $KEY_ID
aws kms enable-key-rotation --key-id $KEY_ID
export KMS_KEY_ARN=$(aws kms describe-key --key-id $KEY_ID --query KeyMetadata.Arn --output text)
echo $KMS_KEY_ARN
```
**Check:** `aws kms describe-key --key-id alias/discovery-agent --query KeyMetadata.KeyState` prints `"Enabled"`.
(The default key policy lets IAM policies grant access, which is what steps 3 and 6 rely on. You can read results because your own identity is allowed by IAM.)

## 2. S3 bucket
```bash
aws s3api create-bucket --bucket $BUCKET        # us-east-1 needs no location constraint
aws s3api put-public-access-block --bucket $BUCKET --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true
aws s3api put-bucket-versioning --bucket $BUCKET --versioning-configuration Status=Enabled
aws s3api put-bucket-encryption --bucket $BUCKET --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"aws:kms","KMSMasterKeyID":"'$KMS_KEY_ARN'"},"BucketKeyEnabled":true}]}'
aws s3api put-bucket-lifecycle-configuration --bucket $BUCKET --lifecycle-configuration '{"Rules":[
  {"ID":"expire-work","Status":"Enabled","Filter":{"Prefix":"work/"},"Expiration":{"Days":14},"NoncurrentVersionExpiration":{"NoncurrentDays":14}},
  {"ID":"abort-multipart","Status":"Enabled","Filter":{"Prefix":""},"AbortIncompleteMultipartUpload":{"DaysAfterInitiation":7}}]}'
sed "s/<BUCKET>/$BUCKET/g" scripts/iam/bucket_policy.json > build/bucket_policy.json
aws s3api put-bucket-policy --bucket $BUCKET --policy file://build/bucket_policy.json
```
**Check:** `aws s3api get-bucket-encryption --bucket $BUCKET` shows your key; `aws s3api get-bucket-versioning --bucket $BUCKET` shows `Enabled`.

## 3. Runtime execution role
```bash
sed "s/<ACCOUNT_ID>/$ACCOUNT_ID/g" scripts/iam/runtime_trust.json > build/runtime_trust.json
aws iam create-role --role-name DiscoveryAgentRuntimeRole --assume-role-policy-document file://build/runtime_trust.json
sed -e "s/<ACCOUNT_ID>/$ACCOUNT_ID/g" -e "s/<BUCKET>/$BUCKET/g" -e "s#<KMS_KEY_ARN>#$KMS_KEY_ARN#g" \
  scripts/iam/runtime_permissions.json > build/runtime_permissions.json
aws iam put-role-policy --role-name DiscoveryAgentRuntimeRole --policy-name DiscoveryAgentRuntime \
  --policy-document file://build/runtime_permissions.json
```
**Check:** `aws iam get-role-policy --role-name DiscoveryAgentRuntimeRole --policy-name DiscoveryAgentRuntime --query PolicyName`.
Do not add an `s3:prefix` condition to `s3:ListBucket`: the agent tests for checkpoints with a HEAD request, and with that condition a missing file returns 403 instead of 404. Wait about 10 seconds before step 5 (IAM changes take a moment to spread).

## 4. Build the package and upload it
```bash
bash scripts/build_package.sh                   # prints: built build/discovery_agent.zip: 35 MB zipped ...
aws s3 cp build/discovery_agent.zip s3://$BUCKET/code/discovery_agent.zip
```
The script installs ARM64 wheels for Python 3.12 into the package, adds `agentcore_app.py`, `discovery_agent/` and `schema/`, fixes file permissions (644 / 755), and refuses to finish if the package is oversized or any native library is not ARM64.
**Check:** `aws s3 ls s3://$BUCKET/code/` lists the file.

## 5. Create the AgentCore runtime
```bash
cat > build/artifact.json <<EOF
{"codeConfiguration":{"code":{"s3":{"bucket":"$BUCKET","prefix":"code/discovery_agent.zip"}},
 "runtime":"PYTHON_3_12","entryPoint":["agentcore_app.py"]}}
EOF
cat > build/runtime_env.json <<EOF
{"DISCOVERY_BUCKET":"$BUCKET","DISCOVERY_KMS_KEY_ID":"$KMS_KEY_ARN","AWS_REGION":"$REGION"}
EOF
aws bedrock-agentcore-control create-agent-runtime \
  --agent-runtime-name discovery_agent \
  --agent-runtime-artifact file://build/artifact.json \
  --role-arn $ROLE_ARN \
  --network-configuration '{"networkMode":"PUBLIC"}' \
  --lifecycle-configuration '{"idleRuntimeSessionTimeout":900,"maxLifetime":7200}' \
  --environment-variables file://build/runtime_env.json \
  --query '{id:agentRuntimeId,arn:agentRuntimeArn,status:status}' --output json
```
Copy the printed values:
```bash
export RUNTIME_ID=<id from the output>
export RUNTIME_ARN=<arn from the output>
```
Wait until it is ready (a few minutes):
```bash
for i in $(seq 1 90); do
  S=$(aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id $RUNTIME_ID --query status --output text)
  echo "$S"; [ "$S" != "CREATING" ] && break; sleep 10
done
aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id $RUNTIME_ID --query '{status:status,reason:failureReason}'
```
**Check:** status `READY`. If `CREATE_FAILED`, `reason` says why (typical: a non-ARM64 binary, a missing entry point, or the role cannot read `code/*`).
**Decision for you:** `PUBLIC` network mode is the simplest. The documents are customer-confidential, so consider `VPC` mode (with VPC endpoints for S3, Bedrock and CloudWatch Logs) before real data goes in. Tell me and I will add it.

## 6. Smoke test (fictional document, no trigger yet)
```bash
aws s3 cp docs/smoke/smoke_call.txt s3://$BUCKET/input/Smoke/01_smoke_call.txt
SESSION=$(uuidgen | tr 'A-Z' 'a-z')            # a UUID is 36 characters; the minimum is 33
aws bedrock-agentcore invoke-agent-runtime \
  --agent-runtime-arn $RUNTIME_ARN --runtime-session-id $SESSION --qualifier DEFAULT \
  --content-type application/json --cli-binary-format raw-in-base64-out \
  --payload '{"engagement":"Smoke","input_prefix":"input/Smoke/","run_id":"smoke1"}' build/invoke_out.json
cat build/invoke_out.json                      # {"status": "accepted", "run_id": "smoke1", ...}
```
Follow the run (it checks every 20 seconds, up to 30 minutes):
```bash
for i in $(seq 1 90); do
  J=$(aws s3 cp s3://$BUCKET/output/Smoke/smoke1/status.json - 2>/dev/null || true)
  echo "$J" | grep -qE '"state": "(succeeded|failed)"' && break; sleep 20
done
echo "$J" | python3 -m json.tool | head -40
aws s3 cp s3://$BUCKET/output/Smoke/smoke1/ build/smoke_out/ --recursive      # download the results
```
**Check:** `"state": "succeeded"`, and `build/smoke_out/README.md` mentions Zendesk, Excel, SharePoint and the discount rules. Stop a run early with `aws bedrock-agentcore stop-runtime-session --agent-runtime-arn $RUNTIME_ARN --runtime-session-id $SESSION --qualifier DEFAULT`.

## 7. Trigger: Lambda role, function, S3 notification
```bash
# role
aws iam create-role --role-name DiscoveryAgentTriggerRole --assume-role-policy-document file://scripts/iam/lambda_trust.json
aws iam attach-role-policy --role-name DiscoveryAgentTriggerRole \
  --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
sed -e "s/<ACCOUNT_ID>/$ACCOUNT_ID/g" -e "s/<RUNTIME_ID>/$RUNTIME_ID/g" scripts/iam/trigger_invoke_policy.json > build/trigger_policy.json
aws iam put-role-policy --role-name DiscoveryAgentTriggerRole --policy-name InvokeDiscoveryRuntime \
  --policy-document file://build/trigger_policy.json

# package: our code plus a current boto3 (Lambda's built-in boto3 can be too old for the AgentCore client)
rm -rf build/lambda && mkdir -p build/lambda
uv pip install -q --target build/lambda boto3
cp scripts/trigger_lambda.py build/lambda/lambda_function.py
(cd build/lambda && zip -q -r ../trigger.zip .)

# function (wait ~10 s after creating the role, then retry if it says the role cannot be assumed)
echo "{\"Variables\":{\"RUNTIME_ARN\":\"$RUNTIME_ARN\"}}" > build/lambda_env.json
aws lambda create-function --function-name discovery-agent-trigger --runtime python3.12 \
  --handler lambda_function.handler --zip-file fileb://build/trigger.zip --timeout 60 --memory-size 128 \
  --role arn:aws:iam::$ACCOUNT_ID:role/DiscoveryAgentTriggerRole --environment file://build/lambda_env.json

# let S3 call it, then connect the marker file to it
aws lambda add-permission --function-name discovery-agent-trigger --statement-id s3invoke \
  --action lambda:InvokeFunction --principal s3.amazonaws.com \
  --source-arn arn:aws:s3:::$BUCKET --source-account $ACCOUNT_ID
cat > build/notification.json <<EOF
{"LambdaFunctionConfigurations":[{"LambdaFunctionArn":"arn:aws:lambda:$REGION:$ACCOUNT_ID:function:discovery-agent-trigger",
 "Events":["s3:ObjectCreated:*"],
 "Filter":{"Key":{"FilterRules":[{"Name":"prefix","Value":"input/"},{"Name":"suffix","Value":"_run"}]}}}]}
EOF
aws s3api put-bucket-notification-configuration --bucket $BUCKET --notification-configuration file://build/notification.json
```
**Check:** `aws s3api get-bucket-notification-configuration --bucket $BUCKET` shows the function.

## 8. Test the trigger
```bash
touch build/_run
aws s3 cp build/_run s3://$BUCKET/input/Smoke/_run
aws logs tail /aws/lambda/discovery-agent-trigger --since 5m          # expect one JSON line with "answer": ... "accepted" and a run_id
```
Then follow that run with the loop from step 6, using the `run_id` from the log line. Each new `_run` upload is a new run; the same S3 event delivered twice maps to the same run and the agent refuses to start it twice.

## 9. Logs, audit, budget
```bash
aws logs put-retention-policy --log-group-name /aws/lambda/discovery-agent-trigger --retention-in-days 30
aws logs put-retention-policy --log-group-name /aws/bedrock-agentcore/runtimes/$RUNTIME_ID-DEFAULT --retention-in-days 30
```
(The two log groups exist only after their first invocation, which step 6 and 8 created.)

**CloudTrail.** Creating a brand-new trail from the CLI also needs a log bucket with a policy, so use the console wizard for a new trail (CloudTrail → Create trail; it creates the bucket and policy; add S3 data events for your bucket). If your organisation already has a trail, add data events for this bucket. **Warning:** this command *replaces* that trail's event selectors, so include what it already records (`aws cloudtrail get-event-selectors --trail-name <trail>` first):
```bash
cat > build/selectors.json <<EOF
[{"Name":"Management","FieldSelectors":[{"Field":"eventCategory","Equals":["Management"]}]},
 {"Name":"Discovery bucket objects","FieldSelectors":[{"Field":"eventCategory","Equals":["Data"]},
  {"Field":"resources.type","Equals":["AWS::S3::Object"]},{"Field":"resources.ARN","StartsWith":["arn:aws:s3:::$BUCKET/"]}]}]
EOF
aws cloudtrail put-event-selectors --trail-name <trail> --advanced-event-selectors file://build/selectors.json
```
**Budget alert** (the Opus pass is the main cost): replace `<AMOUNT>` and `<EMAIL>`:
```bash
cat > build/budget.json <<EOF
{"BudgetName":"discovery-agent-monthly","BudgetLimit":{"Amount":"<AMOUNT>","Unit":"USD"},"TimeUnit":"MONTHLY","BudgetType":"COST"}
EOF
cat > build/budget_notifications.json <<EOF
[{"Notification":{"NotificationType":"ACTUAL","ComparisonOperator":"GREATER_THAN","Threshold":80,"ThresholdType":"PERCENTAGE"},
  "Subscribers":[{"SubscriptionType":"EMAIL","Address":"<EMAIL>"}]}]
EOF
aws budgets create-budget --account-id $ACCOUNT_ID --budget file://build/budget.json \
  --notifications-with-subscribers file://build/budget_notifications.json
```

## 10. First real run
```bash
aws s3 cp <file1> s3://$BUCKET/input/<Engagement>/01_<name>      # number files oldest to newest: they are read in file-name order
aws s3 cp <file2> s3://$BUCKET/input/<Engagement>/02_<name>
aws s3 cp build/_run s3://$BUCKET/input/<Engagement>/_run        # starts the run
```
Engagement name: letters, digits, space, `.`, `_`, `-`. Output appears in `output/<Engagement>/<run_id>/` (spaces become `_`). Follow it with the loop in step 6. Expect about 10 to 30 minutes.
**Resume a failed run** (finished steps are reused, so no repeated model cost): invoke as in step 6 with the payload `{"engagement":"<Engagement>","input_prefix":"input/<Engagement>/","run_id":"<same id>","options":{"resume":true}}`.

## 11. Updating the code later
```bash
bash scripts/build_package.sh
aws s3 cp build/discovery_agent.zip s3://$BUCKET/code/discovery_agent_v2.zip
sed -i '' 's#discovery_agent.zip#discovery_agent_v2.zip#' build/artifact.json
aws bedrock-agentcore-control update-agent-runtime --agent-runtime-id $RUNTIME_ID \
  --agent-runtime-artifact file://build/artifact.json --role-arn $ROLE_ARN \
  --network-configuration '{"networkMode":"PUBLIC"}' \
  --lifecycle-configuration '{"idleRuntimeSessionTimeout":900,"maxLifetime":7200}' \
  --environment-variables file://build/runtime_env.json
```
(Use a new file name each time so it is clear which version is running. If `sed -i ''` does not work in your shell, edit `build/artifact.json` by hand. Passing the lifecycle and environment settings again keeps them.)

## 12. Teardown (reverse order)
```bash
aws s3api put-bucket-notification-configuration --bucket $BUCKET --notification-configuration '{}'
aws lambda delete-function --function-name discovery-agent-trigger
aws iam delete-role-policy --role-name DiscoveryAgentTriggerRole --policy-name InvokeDiscoveryRuntime
aws iam detach-role-policy --role-name DiscoveryAgentTriggerRole --policy-arn arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole
aws iam delete-role --role-name DiscoveryAgentTriggerRole
aws bedrock-agentcore-control delete-agent-runtime --agent-runtime-id $RUNTIME_ID
aws iam delete-role-policy --role-name DiscoveryAgentRuntimeRole --policy-name DiscoveryAgentRuntime
aws iam delete-role --role-name DiscoveryAgentRuntimeRole
# the bucket is versioned: every version and delete marker must be removed before it can be deleted
# (use the S3 console "Empty" button, or a version-by-version delete), then:
aws s3api delete-bucket --bucket $BUCKET
aws kms schedule-key-deletion --key-id alias/discovery-agent --pending-window-in-days 7     # makes remaining encrypted data unreadable
```

## Troubleshooting
| Symptom | Cause and fix |
|---|---|
| `create-agent-runtime` returns AccessDenied | Your identity lacks `iam:PassRole` on the runtime role, or permission to read `s3://$BUCKET/code/*` and decrypt the key (AWS says the caller needs `s3:GetObject` and `kms:Decrypt` for the package) |
| Status `CREATE_FAILED`: "incompatible with Linux ARM64" | A non-ARM64 library got into the zip: rebuild with `scripts/build_package.sh` |
| Status `CREATE_FAILED`: entry point not found | `agentcore_app.py` must be at the zip root (the build script checks this) |
| Run ends with `ReadTimeoutError` in `status.json` | Set the runtime environment variable `BEDROCK_READ_TIMEOUT` higher (default 900 s) via `update-agent-runtime` |
| `AccessDenied` for S3 or KMS in `status.json` | Role policy placeholders wrong, or `DISCOVERY_KMS_KEY_ID` is not the key ARN |
| 403 on a checkpoint check | `s3:ListBucket` has a prefix condition: remove it |
| `no input documents found` | Wrong folder, files start with `_` or `.`, or an unsupported file type |
| `corpus too large` | More than about 150,000 tokens even after trimming: split the engagement |
| Nothing happens after uploading `_run` | The file must be named exactly `_run` directly under `input/<Engagement>/`; check `get-bucket-notification-configuration` and the Lambda log |
| Run vanishes after about 15 minutes | Session idle timeout: check the runtime logs; the entrypoint registers the run as busy |
