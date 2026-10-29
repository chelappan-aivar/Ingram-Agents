# Sample Outlook emails for testing "Fetch from Outlook"

Send these to the mailbox you sign in with (copy subject and body into a new email, or forward them to yourself).
Emails 1 to 4 describe real opportunities with different amounts of detail. Email 5 is not an opportunity and should be skipped.

None of the customers below exist in Dynamics (the seed data has Adventure Works, Alpine Ski House, A. Datum, Bellows College,
Coho Winery, Contoso Retail, Fabrikam Logistics, Lamna Healthcare, Litware, Northwind Health, Proseware, Relecloud,
Tailwind Robotics, Wingtip Toys and Woodgrove Bank), so each one should show up as new.

---

## 1. Migration with clear numbers

**Subject:** Harbourline Freight: AWS migration of 120 servers, budget approved

Hi team,

Harbourline Freight (Rotterdam, about 2,500 employees) has approved moving its on-premises data centre to AWS.
The scope is roughly 120 VMware servers, mostly SQL Server and Windows workloads. Their current hosting contract ends 31 March, so they want to start in January.
Expected AWS spend is about 45,000 EUR per month once migrated. They have asked for migration funding and a partner to run the move.
Contact: Anna de Vries, IT Director, anna.devries@harbourline-freight.example

Can we check which AWS funding programme applies?

Thanks,
Marco

---

## 2. Generative AI proof of concept

**Subject:** FW: Silverbirch Insurance wants a GenAI claims assistant

Hello,

Silverbirch Insurance (Dublin) wants a proof of concept for a claims-handling assistant built on Amazon Bedrock.
They have about 300 claims handlers and want to start with a 3-month pilot. No firm budget yet, the sponsor is the Head of Claims, Liam Murphy.
Data is in S3 and a SQL database on-premises. They would like to decide by the end of next month.

Regards,
Sofia

---

## 3. New workload on AWS, little detail

**Subject:** Redwood Outdoor: interested in moving their e-commerce platform

Hi,

Had a call with Redwood Outdoor today. They are unhappy with their current hosting provider and are considering AWS for their e-commerce platform.
No numbers or dates yet. They will send traffic and cost figures next week. The contact is Priya Shah (CTO).

Can you flag this in the pipeline?

Cheers,
Daniel

---

## 4. Expansion (existing AWS customer)

**Subject:** Kestrel Toys: expanding their AWS footprint for the holiday season

Hi all,

Kestrel Toys already runs on AWS (about 8,000 USD per month). For the holiday peak they plan to add a data analytics platform (Redshift and QuickSight) and expect spend to grow to about 25,000 USD per month from November.
They want help with architecture and are open to a Well-Architected review. Decision maker: Karen Lee, VP Engineering. Timeline: kick-off in 3 weeks.

Best,
Tom

---

## 5. Not an opportunity (should be skipped)

**Subject:** Team lunch on Friday

Hi everyone,

We are having a team lunch on Friday at 12:30 in the canteen. Please reply if you have dietary requirements.

Thanks,
HR

---

# Set 2: complete emails that match Dynamics opportunities (for "Funding determined")

These four name customers that already exist in Dynamics (added by the seed script). The Fetch button combines each email with its
Dynamics record into one row with both badges, and Check funding then gives a programme. A mailbox-only row cannot be
funding-checked, because the funding check reads the Dynamics record.

## 6. Initech Energy: expected MAP

**Subject:** Initech Energy: billing platform move from Azure to AWS, 65k USD per month

Hi team,

Initech Energy (6,000 employees, an established utility, not a startup) will move its customer billing platform from Microsoft Azure to AWS.
About 150 servers (VMs) and two Oracle databases are in scope. Expected AWS spend is 65,000 USD per month once live.
Their Azure agreement ends in April 2027, so they want to start in Q1. Contact: Paul Reeves, Head of Infrastructure.
This is a migration, and we would like to request AWS migration funding.

Thanks,
Marco

## 7. Nimbus Payments: expected IW Migrate

**Subject:** Nimbus Payments: startup migration to AWS, 12k USD per month, no VMs

Hello,

Nimbus Payments is a seed-funded startup in Berlin. They are migrating their container platform from a colocation provider to AWS.
There are no virtual machines to rehost, because the application is already containerised. Expected AWS spend is 12,000 USD per month.
They want to start within 3 months. Contact: Clara Meyer, CTO.

Regards,
Sofia

## 8. Orbit Mobility: expected MAP Lite

**Subject:** Orbit Mobility: Series B startup moving fleet platform from colocation to AWS Mumbai

Hi,

Orbit Mobility is a Series B startup. They are migrating their fleet-tracking platform, about 60 VMs, from a colocation facility to AWS Mumbai.
Expected AWS spend is 30,000 USD per month. They plan to start in 3 to 6 months and need migration funding.
Contact: Kavya Nair, VP Engineering.

Cheers,
Daniel

## 9. Pioneer Mutual: expected Gen AI production funding

**Subject:** Pioneer Mutual: claims copilot going to production on Amazon Bedrock, 40k USD per month

Hi all,

Pioneer Mutual (an established insurer with 4,000 employees, not a startup) is taking its claims-handling copilot on Amazon Bedrock
into production for 800 claims handlers. The pilot was successful. This is a new Gen AI workload, not a modernisation.
Expected AWS spend is 40,000 USD per month. Go-live is planned within 3 months. Contact: Helen Ward, Chief Data Officer.

Best,
Tom
