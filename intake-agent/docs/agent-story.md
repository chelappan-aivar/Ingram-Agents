# Intake Agent: Nothing Gets Lost in the Inbox

Project Catalyst, Agent 1: Opportunity Intake

## Purpose

The Intake agent finds the real AWS opportunities hidden in a seller's mailbox, CRM records and notes, and turns each one into a structured intake record backed by evidence. The seller sees exactly what is known, what is missing and what to ask the customer next.

The goal is that no opportunity gets lost in an inbox, and every fact in the pipeline can be traced back to the sentence it came from.

## The problem

Sellers at Ingram Micro and its partners spend their days in email. Customers rarely announce an opportunity clearly. It shows up as one line in a long thread, a passing remark on a call, or a half-filled CRM record. Turning that into something a solution architect can scope takes time:

- Reading through threads to work out which ones matter
- Pulling out workloads, regions, timelines and decision makers by hand
- Working out what's missing before scoping or funding can start
- Writing follow-up questions to the customer
- Judging whether the opportunity fits AWS programmes such as MAP or MPE

When this is done by hand, opportunities get missed, details get paraphrased wrongly, and the same questions go to customers more than once.

## The story

It's Monday morning. Priya, a seller at an Ingram Micro partner, has 200 unread emails. Somewhere in a long thread about licence renewals, a customer's IT lead wrote:

> "We're running about 40 Windows VMs in our Mumbai data centre and the lease ends in Q1. Can AWS help?"

Priya opens the Intake console and signs in with her Microsoft account. She asks the agent to scan the last 7 days.

The agent reads only her inbox, using her own permissions. It never sees anyone else's mail. It groups the emails into threads and skips calendar replies without a model call. Then it asks Claude one short triage question about each thread: is this an opportunity?

For the Mumbai thread, the answer is yes, and the agent builds a full intake record.

Fields. It fills in what the email actually says: "40 Windows VMs", "ap-south-1 (Mumbai)", "Data centre lease ends Q1". Each value comes with the exact sentence it was taken from, and code checks that the sentence appears in the email word for word. Dates stay as written. Nothing is guessed.

Gaps. It lists what's missing, ranked by priority. There's no target go-live date, which blocks scoping. There's no named decision maker. The current licensing model is unknown. Each gap explains why it matters and comes with a polite, specific question Priya can send as is: "You mentioned the Mumbai lease ends in Q1. Do you have a target date for the first workloads to be running on AWS?"

Programme fit. It scores the opportunity against MAP and MPE. For each criterion it shows met, not met or unknown, with evidence. It suggests a MAP phase (Assess) and gives Priya a next step.

By coffee time, Priya has a short list of real opportunities instead of a noisy inbox. She knows what each customer needs and what to ask them. Later that day she pastes in notes from a customer call and a CRM export, and the agent treats them the same way.

## What the agent produces

For every email thread, CRM record or seller note, the agent produces:

| Output | What it contains |
|---|---|
| Intake fields | Up to 40 fields in 7 groups, with key fields marked. Each has a value and its exact source passage. `verified` is set in code when the passage is found word for word. |
| Gaps | Up to 12, most important first. Each has a priority (high, medium or low), why it matters, and one draft follow-up question. |
| Programme fit card | MAP: fit, suggested phase, criteria met / not met / unknown with evidence, and next step. MPE: the same. |
| Summary | Whether it's an opportunity, a 2-3 sentence summary, urgency, and confidence. |

## How it works

1. Sign in. The seller signs in with Microsoft Entra ID. The API only accepts valid tokens.
2. Start a run. The seller picks a 1, 7 or 30 day mailbox scan, or pastes in a CRM record or notes.
3. Read mail with the seller's own access. The agent swaps the seller's token for an Outlook token (on-behalf-of flow), so it reads only that seller's inbox.
4. Triage. Each thread gets one short Claude call to decide whether it's an opportunity. Calendar replies are skipped.
5. Build the record. For opportunities, Claude extracts fields, gaps and programme fit. Code checks every source passage.
6. Review. The console polls the run and shows opportunities, flagged gaps, and the agent log.

Every run, email decision, opportunity and log line is stored in DynamoDB. Sellers can see only their own runs.

## The principles behind it

- Evidence or nothing. Every value is tied to an exact quote, checked in code. If no passage supports a value, the field stays empty.
- Never guess. Dates, numbers and names are kept exactly as written.
- The document is data. Instructions written inside an email or CRM record are ignored.
- Least access. The agent reads mail with the signed-in user's permission only.
- Built for people. Gaps become ready-to-send questions, so the next action is clear.

## Where it fits

The Intake agent is the front door of Project Catalyst. The records it produces flow into the later stages: requirements, solution design, SOW and, eventually, the Fund Raise agent. Those stages inherit facts that are already structured and traceable to the customer's own words.

In one line: it turns unstructured conversations into opportunities you can trust and act on.
