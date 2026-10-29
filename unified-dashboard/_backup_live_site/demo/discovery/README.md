# Discovery Report: Demo

| Field | Detail |
|---|---|
| Engagement | Demo |
| Sources analysed (3) | Oct 6 · Kickoff - Meridian Freight x AWS x Aivar; 02_followup_email.eml; 03_meeting_minutes.txt |
| Generated | 2026-10-08 |
| Status | pending human review |

> Items marked **hypothesis, not confirmed** are inferences from the sources, not statements by the customers. Every item cites a verbatim quote and its source.

## Executive summary

The customer's accounts payable team of three people processes between 4,500 and 5,200 invoices per month. Today, invoices arrive by email, are keyed into NetSuite manually, and matched against the purchase order and goods receipt. Clean invoices take about two days; exceptions involving price differences or missing receipts take closer to five days and keep the team late at month end, especially after new suppliers were onboarded in September.

The customers have stated two explicit goals: automatic matching of clean invoices with a short list of real exceptions for the team to review, and a clear record of who approved what. They also raised supplier onboarding as a follow-on priority if the invoice work goes well. Today, about 60 open exceptions are tracked in a single person's spreadsheet updated by hand every morning, and the approval trail had to be rebuilt from emails when auditors asked.

Several unconfirmed hypotheses emerged: the solution may need to capture and extract invoice data from the ap-invoices mailbox before matching can be automated; a read-only NetSuite integration might deliver early value without waiting for a security review; the weekly-only supplier portal CSV feed may conflict with the proposed daily exceptions list; and email is acting as the de facto workflow for exceptions and approvals. Nobody has measured the volume or causes of exceptions, which would set a baseline for success.

The biggest blockers are that write access to NetSuite requires a security review taking a few weeks to schedule, the supplier portal has no API and only provides a weekly CSV export, and the exceptions spreadsheet owner is reluctant to share it outside finance until the customer's lead approves. The previous RPA approach failed because it relied on screen layout, which may make the customers wary of brittle automation.

## 1. Discovery summary

| # | Use case | Category | Confidence |
|---|---|---|---|
| 1 | Automated matching of clean invoices with a daily exceptions list | Explicit | high |
| 2 | Read-only first integration that avoids the NetSuite write-access review | Implied | high |
| 3 | Automated invoice capture from the ap-invoices shared mailbox | Implied | medium |
| 4 | Daily exceptions list depends on a weekly-only supplier portal feed | Latent | medium |
| 5 | Exception baseline and root-cause analytics | Latent | high |
| 6 | Approval routing and audit trail | Explicit | medium |
| 7 | Shared supplier master data linking onboarding and approval routing | Adjacent | medium |
| 8 | Replace email as the de facto exception and approval workflow | Latent | medium |
| 9 | Capacity pressure from supplier growth | Latent | medium |
| 10 | Supplier onboarding (later phase) | Explicit | low |
| 11 | Finance data access governance for the new solution | Implied | low |

## 2. Top hidden opportunities

1. **Read-only first integration that avoids the NetSuite write-access review** (`UC-2`, Implied) — _hypothesis, not confirmed_
   A read-only integration could deliver value without waiting for the NetSuite security review, which takes a few weeks to schedule. If matched invoice results can be returned to NetSuite through a user import or existing feed rather than direct tool write access, the engagement can start sooner and de-risk the delivery schedule.
2. **Automated invoice capture from the ap-invoices shared mailbox** (`UC-3`, Implied) — _hypothesis, not confirmed_
   Automatic invoice capture and data extraction from the ap-invoices mailbox is prerequisite to any automatic matching. The team processes 5,200 invoices per month and someone opens each one to key it into NetSuite by hand, so automating intake removes the bottleneck and scales with new suppliers without adding headcount.
3. **Daily exceptions list depends on a weekly-only supplier portal feed** (`UC-4`, Latent) — _hypothesis, not confirmed_
   The solution is proposed to deliver a daily exceptions list, but the only supplier portal data feed is a weekly CSV. Resolving this data freshness conflict early—either by running exports more often, using alternative data sources for goods receipts, or resetting expectations on the exceptions cadence—prevents a repeat of the earlier RPA failure and sets realistic timelines.
4. **Exception baseline and root-cause analytics** (`UC-5`, Latent) — _hypothesis, not confirmed_
   No one has measured exception volume, trends, or causes despite them taking five days and keeping the team late at month end. Establishing a baseline count and analytics on price differences, missing receipts, and other root causes allows the team to target the most common problems and gives measurable proof that the solution works.
5. **Shared supplier master data linking onboarding and approval routing** (`UC-7`, Adjacent) — _hypothesis, not confirmed_
   Approval thresholds are out of date in a SharePoint document and differ by supplier category. Linking supplier master data from onboarding to invoice routing keeps thresholds current and avoids rework during the follow-on onboarding engagement, making the two projects cohesive.

## 3. Use cases

### 1. Automated matching of clean invoices with a daily exceptions list (`UC-1`)

Automatic matching of clean invoices with a daily exceptions list that replaces the hand-updated spreadsheet. Matches invoices against purchase orders and goods receipts; unmatched items go to a short list for manual review.

- **Category:** Explicit
- **Confidence:** high
- **Problem:** Invoices are keyed into NetSuite manually and matched against PO and goods receipt; exceptions take about five days and are tracked in a single person's spreadsheet.
- **Systems involved:** NetSuite, supplier portal, SharePoint
- **Current process:**
  - Step 1: Invoices arrive by email in the ap-invoices shared mailbox
  - Step 2: Somebody opens each invoice and keys it into NetSuite
  - Step 3: Invoice is matched against the purchase order and goods receipt
  - Step 4: Exceptions are tracked in Raj's spreadsheet, updated by hand every morning
- **Data sources and APIs:**
  - NetSuite invoices and purchase orders
  - Supplier portal weekly CSV export (no API)
  - Exceptions spreadsheet
- **Owners:** Raj Patel; Dana Whitfield
- **Dependencies and blockers:**
  - Security review needed for direct write access to NetSuite, takes a few weeks to schedule
  - Supplier portal has no API; weekly CSV only
  - Marcus must decide whether the exceptions spreadsheet can be shared
- **Customer-stated metrics:**
  - about two days for clean invoices
  - closer to five days for exceptions
  - three people in accounts payable
- **Status:** Planned by the customers
- **Evidence:**
  - "Somebody opens each one, keys it into NetSuite, and then matches it against the purchase order and the goods receipt." (Dana Whitfield, Oct 6)
  - "Aivar proposed starting with automated matching of clean invoices, and a daily exceptions list that replaces the spreadsheet." (Tom Becker, 03_meeting_minutes.txt)
- **Value for the customer:** Clean invoices matched automatically and a short list of real exceptions for the team to look at.
- **Value for us:** Anchor engagement for AP automation; reusable invoice matching pattern on AWS that can expand to onboarding.
- **Ask on the next call:** What read/write access to NetSuite can be granted and how soon can the security review be requested?

### 2. Read-only first integration that avoids the NetSuite write-access review (`UC-2`)

A read-only integration to NetSuite that avoids the security review delay, allowing matching analysis to begin while write-access approvals are scheduled. Results would be returned to NetSuite by user import or an existing feed rather than direct tool write.

- **Category:** Implied — _hypothesis, not confirmed_
- **Confidence:** high
- **Problem:** Write access to NetSuite needs a security review that takes a few weeks to schedule.
- **Systems involved:** NetSuite
- **Data sources and APIs:**
  - NetSuite invoices and purchase orders
- **Owners:** Lena Kowalski (IT)
- **Dependencies and blockers:**
  - Security review for write access takes a few weeks to schedule
- **Evidence:**
  - "Lena: confirm what read access to NetSuite can be granted without a full security review." (Tom Becker, 03_meeting_minutes.txt)
  - "please tell us what access you will need as soon as you know." (Marcus Obi, 02_followup_email.eml)
- **Why it is hidden:** Nobody has said whether the solution can deliver value with read-only access, or how results would get back into NetSuite without a direct write.
- **Value for the customer:** Earlier value without waiting on the security review.
- **Value for us:** De-risks the delivery start; a phased integration architecture.
- **Ask on the next call:** Could matched results be posted to NetSuite by an AP user or through an existing import, rather than by direct tool write access?

### 3. Automated invoice capture from the ap-invoices shared mailbox (`UC-3`)

Automated capture and extraction of invoice data from the ap-invoices shared mailbox, removing manual keying for a volume that has grown to 5,200 invoices in busy months on a three-person team.

- **Category:** Implied — _hypothesis, not confirmed_
- **Confidence:** medium
- **Problem:** Automatic matching needs structured invoice data. Today every emailed invoice is opened and keyed into NetSuite by hand, so matching cannot be automatic unless intake and data extraction are automated first.
- **Systems involved:** NetSuite
- **Current process:**
  - Step 1: Invoices arrive by email into the ap-invoices shared mailbox
  - Step 2: Somebody opens each one and keys it into NetSuite
- **Data sources and APIs:**
  - ap-invoices shared mailbox
- **Dependencies and blockers:**
  - Write access to NetSuite requires a security review
- **Customer-stated metrics:**
  - about 4,500 invoices a month
  - closer to 5,200 invoices in a busy month
- **Status:** Not started
- **Evidence:**
  - "most of those arrive by email into a shared mailbox, ap-invoices. Somebody opens each one, keys it into NetSuite" (Dana Whitfield, Oct 6)
  - "We are now closer to 5,200 invoices in a busy month, not 4,500" (Marcus Obi, 02_followup_email.eml)
- **Why it is hidden:** The customers asked for automatic matching. Nobody asked to automate the mailbox intake or the extraction of invoice fields, which comes before matching.
- **Value for the customer:** Removes manual keying for a growing invoice volume handled by a three-person team.
- **Value for us:** Adds a document ingestion and extraction workstream; a reusable intelligent document processing pattern on AWS.
- **Ask on the next call:** In what formats do invoices arrive in ap-invoices (PDF, scanned image, email body), and do any invoices arrive outside the mailbox?

### 4. Daily exceptions list depends on a weekly-only supplier portal feed (`UC-4`)

A robust pipeline to ingest the weekly supplier portal CSV export that does not break when the portal layout changes, and a clarification of whether daily exceptions can be supported when portal data arrives only once per week.

- **Category:** Latent — _hypothesis, not confirmed_
- **Confidence:** medium
- **Problem:** Aivar proposed a daily exceptions list. The supplier portal only provides a weekly CSV export, so any matching that depends on portal data may be up to a week stale. A robust CSV ingestion pipeline is needed because the earlier RPA approach broke.
- **Systems involved:** supplier portal, NetSuite
- **Current process:**
  - Step 1: Weekly CSV export from the supplier portal
- **Data sources and APIs:**
  - Supplier portal weekly CSV export
- **Owners:** Lena Kowalski (IT)
- **Dependencies and blockers:**
  - API request to portal vendor open for a year with no answer
- **Evidence:**
  - "The supplier portal is a separate vendor product and it has no API, so we only get data out of it as a weekly CSV export." (Lena Kowalski, Oct 6)
  - "Aivar proposed starting with automated matching of clean invoices, and a daily exceptions list that replaces the spreadsheet." (Tom Becker, 03_meeting_minutes.txt)
- **Why it is hidden:** Nobody pointed out that the daily cadence and the weekly feed conflict. It was also not said which matching data (for example goods receipts) comes from the portal.
- **Value for the customer:** Exceptions list that reflects the actual data freshness; no repeat of the RPA failure.
- **Value for us:** Data ingestion pipeline workstream; sets realistic expectations early.
- **Ask on the next call:** Which fields needed for matching come only from the portal CSV, and can the export be run more often than weekly?

### 5. Exception baseline and root-cause analytics (`UC-5`)

Baseline measurement and analytics of exceptions by count, age, cause, supplier, and month. Nobody has measured how many exceptions occur or why, so success cannot be shown and root causes cannot be addressed.

- **Category:** Latent — _hypothesis, not confirmed_
- **Confidence:** high
- **Problem:** Nobody has measured how many exceptions there are. The only view is a hand-updated spreadsheet of about 60 open items. Without a baseline, the success of the project cannot be shown and the causes of exceptions cannot be addressed.
- **Systems involved:** exceptions spreadsheet
- **Current process:**
  - Step 1: Raj updates the spreadsheet by hand every morning
- **Data sources and APIs:**
  - Exceptions spreadsheet
- **Owners:** Raj Patel
- **Dependencies and blockers:**
  - Marcus must agree before the spreadsheet is shared
- **Customer-stated metrics:**
  - about 60 open exceptions at any time
  - closer to five days for exceptions
- **Status:** Not started
- **Evidence:**
  - "nobody has measured exactly how many there are." (Marcus Obi, 02_followup_email.eml)
  - "He tracks about 60 open exceptions at any time and updates it by hand every morning." (Tom Becker, 03_meeting_minutes.txt)
- **Why it is hidden:** The customers asked for a short exceptions list. They did not ask for measurement, trends, or analysis of exception causes such as price differences or missing receipts.
- **Value for the customer:** Gives a baseline to prove improvement and targets the most common causes of exceptions.
- **Value for us:** Adds an analytics and reporting layer; measurable success criteria for the engagement.
- **Ask on the next call:** Would you want exception counts, ageing and causes reported per supplier and per month?

### 6. Approval routing and audit trail (`UC-6`)

Automated approval routing and audit trail. Approvals are currently a record in emails and had to be rebuilt for auditors. Approval thresholds differ by supplier category and are kept in a SharePoint document that is not always current.

- **Category:** Explicit
- **Confidence:** medium
- **Problem:** Approval history had to be rebuilt from emails for auditors, and approval thresholds differ by supplier category and are kept in a SharePoint document that is not always current.
- **Systems involved:** SharePoint, NetSuite
- **Current process:**
  - Step 1: Approvals happen and the trail is reconstructed from emails when auditors ask
- **Data sources and APIs:**
  - SharePoint approval thresholds document
- **Owners:** Dana Whitfield
- **Dependencies and blockers:**
  - Threshold list is not always up to date
- **Status:** Planned by the customers
- **Evidence:**
  - "Our auditors asked about approvals last quarter, and honestly we had to rebuild the trail from emails." (Dana Whitfield, Oct 6)
  - "the current list lives in a SharePoint document that is not always up to date." (Tom Becker, 03_meeting_minutes.txt)
- **Value for the customer:** A clear record of who approved what.
- **Value for us:** Extends scope to approval workflow and audit reporting.
- **Ask on the next call:** Who are the approvers and what are the thresholds per supplier category?

### 7. Shared supplier master data linking onboarding and approval routing (`UC-7`)

Shared supplier master data linking invoice approval routing to onboarding, so that supplier categories recorded during onboarding automatically set approval thresholds and stay current.

- **Category:** Adjacent — _hypothesis, not confirmed_
- **Confidence:** medium
- **Problem:** Approval thresholds depend on supplier category, and the threshold list is out of date. Onboarding is where supplier categories would originate, and the same people are involved in both, so shared supplier master data would serve both processes.
- **Systems involved:** SharePoint, NetSuite
- **Data sources and APIs:**
  - SharePoint approval thresholds document
- **Owners:** Dana Whitfield
- **Dependencies and blockers:**
  - Threshold list is not always up to date
- **Status:** Not started
- **Evidence:**
  - "Dana confirmed that approval thresholds differ by supplier category, and that the current list lives in a SharePoint document that is not always up to date." (Tom Becker, 03_meeting_minutes.txt)
  - "Some of the same people are involved." (Marcus Obi, 02_followup_email.eml)
- **Why it is hidden:** Onboarding was deferred as a separate topic. Nobody linked supplier category data to invoice routing.
- **Value for the customer:** Routing rules that stay current; less rework later in the onboarding phase.
- **Value for us:** Natural bridge into the onboarding follow-on engagement.
- **Ask on the next call:** Where is a supplier's category recorded today, and who sets it during onboarding?

### 8. Replace email as the de facto exception and approval workflow (`UC-8`)

Automated exception and approval workflow to replace email chasing. When Raj is unavailable, people are contacted by email; this defines email as the workflow and record system for both exceptions and approvals.

- **Category:** Latent — _hypothesis, not confirmed_
- **Confidence:** medium
- **Problem:** Email is used to chase people when Raj is away and is the only source for rebuilding the approval trail. Email is acting as the workflow and record system.
- **Systems involved:** email
- **Current process:**
  - Step 1: People are chased by email when Raj is out
- **Owners:** Raj Patel
- **Status:** Not started
- **Evidence:**
  - "Our auditors asked about approvals last quarter, and honestly we had to rebuild the trail from emails." (Dana Whitfield, Oct 6)
  - "We chase people by email until he is back. It has happened a couple of times." (Dana Whitfield, Oct 6)
- **Why it is hidden:** Nobody asked for automated notifications, assignment or escalation of exceptions.
- **Value for the customer:** Removes the dependency on one person; exceptions are routed and escalated automatically.
- **Value for us:** Workflow and notification scope; a reusable case-management pattern.
- **Ask on the next call:** Who needs to act on an exception (suppliers, requesters, approvers), and how are they contacted today?

### 9. Capacity pressure from supplier growth (`UC-9`)

Capacity planning for supplier growth. Invoice volume grew after new suppliers were onboarded in September while team size remained at three people. The solution must scale and onboarding speed affects AP volume.

- **Category:** Latent — _hypothesis, not confirmed_
- **Confidence:** medium
- **Problem:** Invoice volume rose because new suppliers were onboarded, while the team stays at three people. Each new supplier adds load, so the solution must scale and onboarding speed affects AP volume.
- **Customer-stated metrics:**
  - three people
  - about 4,500 invoices a month
- **Evidence:**
  - "two new suppliers came on in September." (Marcus Obi, 02_followup_email.eml)
  - "Our accounts payable team is three people, and we process about 4,500 invoices a month." (Marcus Obi, Oct 6)
- **Why it is hidden:** No growth forecast or capacity target was stated.
- **Value for the customer:** Absorbs growth without adding headcount.
- **Value for us:** Justifies a scalable serverless design and an onboarding follow-on.
- **Ask on the next call:** How many new suppliers do you expect over the next year?

### 10. Supplier onboarding (later phase) (`UC-10`)

Faster supplier onboarding. The customer stated that onboarding takes far too long and would like to tackle it next if invoice automation goes well. The current onboarding process and timeline were not described.

- **Category:** Explicit
- **Confidence:** low
- **Problem:** Supplier onboarding takes far too long.
- **Dependencies and blockers:**
  - Customers asked to not mix it in yet; invoice matching first
- **Status:** Not started
- **Evidence:**
  - "supplier onboarding is taking us far too long, and if the invoice work goes well I would like to look at it next." (Marcus Obi, 02_followup_email.eml)
- **Value for the customer:** Faster supplier onboarding.
- **Value for us:** Potential follow-on engagement after invoice matching.
- **Ask on the next call:** What does the current onboarding process look like and who is involved?

### 11. Finance data access governance for the new solution (`UC-11`)

Finance data access governance. The exceptions spreadsheet owner is reluctant to share exception data outside finance, implying the new solution will need role-based access controls and data handling rules for sensitive financial data.

- **Category:** Implied — _hypothesis, not confirmed_
- **Confidence:** low
- **Problem:** Raj is reluctant to share exception data outside finance. This implies the new solution will need role-based access and data handling rules for finance data.
- **Data sources and APIs:**
  - Exceptions spreadsheet
- **Owners:** Marcus Obi
- **Dependencies and blockers:**
  - Marcus decision pending
- **Evidence:**
  - "Raj said he is not comfortable sharing the spreadsheet outside the finance team until Marcus agrees." (Tom Becker, 03_meeting_minutes.txt)
- **Why it is hidden:** Nobody discussed access controls or data classification for the future system.
- **Value for the customer:** Finance data is visible only to authorised staff.
- **Value for us:** Security and governance design scope.
- **Ask on the next call:** Who outside finance may see exception and invoice data in the new tool?

## 4. What we heard

### Goals

- **[stated]** Invoices that are clean are matched automatically, leaving a short list of real exceptions for the team to review.
  - "Invoices matched automatically when they are clean, and a short list of real exceptions for the team to look at." (Marcus Obi, Oct 6)
- **[stated]** A clear record of who approved what.
  - "I also want a clear record of who approved what." (Marcus Obi, Oct 6)
- **[stated]** Look at supplier onboarding next if the invoice work goes well.
  - "supplier onboarding is taking us far too long, and if the invoice work goes well I would like to look at it next." (Marcus Obi, 02_followup_email.eml)
- **[inferred]** Reduce dependency on a single person for exception tracking. _hypothesis, not confirmed_
  - "Raj keeps a spreadsheet of the open exceptions, uh, and honestly nobody else really touches it." (Dana Whitfield, Oct 6)

### Pain points

- **[stated]** Exceptions (price differences, missing receipts) take about five days and are worse at month end; they are what keep the team late.
  - "For anything with an exception, a price difference or a missing receipt, it is closer to five days, and at month end it is worse." (Marcus Obi, Oct 6)
  - "The exceptions are what keep the team late at month end, and nobody has measured exactly how many there are." (Marcus Obi, 02_followup_email.eml)
- **[stated]** Open exceptions are tracked in one person's spreadsheet that nobody else touches; when he is out, people are chased by email.
  - "Raj keeps a spreadsheet of the open exceptions, uh, and honestly nobody else really touches it." (Dana Whitfield, Oct 6)
  - "We chase people by email until he is back. It has happened a couple of times." (Dana Whitfield, Oct 6)
- **[stated]** The approval trail had to be rebuilt from emails when auditors asked.
  - "Our auditors asked about approvals last quarter, and honestly we had to rebuild the trail from emails." (Dana Whitfield, Oct 6)
- **[stated]** A previous RPA bot broke whenever the supplier portal changed layout and was switched off.
  - "It broke every time the supplier portal changed its layout, so we switched it off." (Marcus Obi, Oct 6)
- **[stated]** The approval threshold list lives in a SharePoint document that is not always up to date.
  - "the current list lives in a SharePoint document that is not always up to date." (Tom Becker, 03_meeting_minutes.txt)
- **[inferred]** Previous automation failed because it relied on screen layout, which may make the customers wary of brittle approaches. _hypothesis, not confirmed_
  - "It broke every time the supplier portal changed its layout, so we switched it off." (Marcus Obi, Oct 6)

### Current state

- **[stated]** Invoices mostly arrive by email in a shared mailbox, are keyed into NetSuite, then matched against the purchase order and goods receipt.
  - "most of those arrive by email into a shared mailbox, ap-invoices. Somebody opens each one, keys it into NetSuite" (Dana Whitfield, Oct 6)
- **[stated]** Accounts payable team of three people processing about 4,500 invoices a month; later stated as closer to 5,200 in a busy month.
  - "Our accounts payable team is three people, and we process about 4,500 invoices a month." (Marcus Obi, Oct 6)
  - "We are now closer to 5,200 invoices in a busy month, not 4,500" (Marcus Obi, 02_followup_email.eml)
- **[stated]** Clean invoices take about two days from arrival to approval.
  - "for a clean invoice it is about two days from arrival to approval. That is fine for us." (Dana Whitfield, Oct 6)
- **[stated]** NetSuite is the system of record; the supplier portal has no API and only provides a weekly CSV export.
  - "NetSuite is our system of record for invoices and purchase orders. The supplier portal is a separate vendor product and it has no API" (Lena Kowalski, Oct 6)
- **[stated]** About 60 open exceptions are tracked at any time and the spreadsheet is updated by hand every morning.
  - "He tracks about 60 open exceptions at any time and updates it by hand every morning." (Tom Becker, 03_meeting_minutes.txt)
- **[inferred]** There is no vendor API in sight for the supplier portal. _hypothesis, not confirmed_
  - "A request to the portal vendor for an API has been open for a year with no answer." (Tom Becker, 03_meeting_minutes.txt)

### Constraints

- **[stated]** A new tool cannot get direct write access to NetSuite without a security review that takes a few weeks to schedule.
  - "we cannot give a new tool direct write access to NetSuite without a security review, which usually takes our team a few weeks to schedule." (Lena Kowalski, Oct 6)
  - "our security review for any new NetSuite integration needs to be requested early." (Marcus Obi, 02_followup_email.eml)
- **[stated]** The weekly CSV export is the only supplier portal data feed; an API request to the vendor has been open for a year without an answer.
  - "A request to the portal vendor for an API has been open for a year with no answer." (Tom Becker, 03_meeting_minutes.txt)
- **[stated]** Approval thresholds differ by supplier category.
  - "approval thresholds differ by supplier category" (Tom Becker, 03_meeting_minutes.txt)
- **[stated]** The exceptions spreadsheet owner is not comfortable sharing it outside finance until Marcus agrees.
  - "Raj said he is not comfortable sharing the spreadsheet outside the finance team until Marcus agrees." (Tom Becker, 03_meeting_minutes.txt)
- **[inferred]** The security review lead time is a likely schedule risk and needs early access requirements. _hypothesis, not confirmed_
  - "please tell us what access you will need as soon as you know." (Marcus Obi, 02_followup_email.eml)

### Stakeholders

- **[stated]** Leads the customer side of the engagement; raised goals, decides on sharing the spreadsheet. (Marcus Obi)
  - "Marcus: decide whether the exceptions spreadsheet can be shared." (Tom Becker, 03_meeting_minutes.txt)
- **[stated]** Describes the AP process and owns the approvers list. (Dana Whitfield)
  - "Dana will own that list." (Marcus Obi, Oct 6)
- **[stated]** IT; NetSuite and supplier portal knowledge, security review. (Lena Kowalski, IT)
  - "From the IT side, NetSuite is our system of record" (Lena Kowalski, Oct 6)
- **[stated]** Maintains the open exceptions spreadsheet. (Raj Patel)
  - "Raj walked through his exceptions spreadsheet." (Tom Becker, 03_meeting_minutes.txt)

## 5. Gaps and open questions

- **How many invoice exceptions occur per month, and what are their causes?**
  - Why: Nobody has measured exactly how many exceptions there are.
  - "nobody has measured exactly how many there are." (Marcus Obi, 02_followup_email.eml)
- **What NetSuite access can be granted without a full security review?**
  - Why: Lena's action to confirm read access is outstanding; write access needs a review.
  - "Lena: confirm what read access to NetSuite can be granted without a full security review." (Tom Becker, 03_meeting_minutes.txt)
- **Will Marcus allow the exceptions spreadsheet to be shared?**
  - Why: Raj will not share it outside finance until Marcus agrees.
  - "Marcus: decide whether the exceptions spreadsheet can be shared." (Tom Becker, 03_meeting_minutes.txt)
- **Is the current two-day clean-invoice time acceptable?**
  - Why: Dana says it is fine, while Marcus pushes back and wants faster handling of exceptions.
  - "I would push back a little on that." (Marcus Obi, Oct 6)
  - "That is fine for us." (Dana Whitfield, Oct 6)
- **What is the actual monthly invoice volume?**
  - Why: 4,500 per month was stated on the call, but a later email says 5,200 in a busy month.
  - "We are now closer to 5,200 invoices in a busy month, not 4,500" (Marcus Obi, 02_followup_email.eml)
- **Do any invoices arrive outside the ap-invoices mailbox?**
  - Why: Only 'most' invoices were said to arrive by email.
  - "most of those arrive by email into a shared mailbox, ap-invoices." (Dana Whitfield, Oct 6)
- **Where do goods receipts come from: NetSuite or the supplier portal CSV?**
  - Why: Matching uses goods receipts, but the source of goods receipt data was not stated.
  - "matches it against the purchase order and the goods receipt." (Dana Whitfield, Oct 6)
- **Is supplier onboarding in or out of scope for this engagement?**
  - Why: Marcus deferred onboarding on the call, then raised it as the next step in his email.
  - "Supplier onboarding is another headache, but let us not mix that in yet." (Marcus Obi, Oct 6)
  - "if the invoice work goes well I would like to look at it next." (Marcus Obi, 02_followup_email.eml)
- **Is a daily exceptions list feasible with weekly portal data?**
  - Why: The only portal data feed is weekly.
  - "Lena confirmed that the weekly CSV export from the supplier portal is the only data feed available." (Tom Becker, 03_meeting_minutes.txt)
