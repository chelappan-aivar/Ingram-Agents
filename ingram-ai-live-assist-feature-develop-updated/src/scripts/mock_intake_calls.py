"""Mock Live Assist calls for Intake opportunities (used by seed_intake_calls.py).

Each entry matches an opportunity by customer name in the Intake feed and adds one or more calls. The facts line up
with the Intake records (VM counts, program, spend) and add what a real discovery call brings out: pain points,
constraints, stakeholders, and a few things said in passing that the Discovery agent should pick up as hidden needs.
Every line is invented sample data. The partner Business Analyst is "Ravi Menon" in all calls.
"""

BA = "Ravi Menon"

MOCK_CALLS: dict[str, list[dict[str, str]]] = {
    "Relecloud": [
        {"title": "Relecloud: rehost kickoff", "text": """\
Ravi Menon: Thanks for joining. I want to understand the 18 VMs you run in the Hyderabad colocation and why you want to move now.
Priya Shah: Our colo contract ends in four months and the provider is raising the price by 30 percent. We cannot afford both.
Ravi Menon: What runs on those machines?
Priya Shah: Our SaaS platform. Eleven app servers, four database servers on PostgreSQL, and three utility boxes for queues and cron jobs.
Ravi Menon: Who looks after them today?
Daniel Ortiz: That is me and one contractor. We have no one with AWS experience, so I am a bit worried about the cutover.
Ravi Menon: Any downtime window the business can accept?
Priya Shah: Our customers are in Europe and the US. We can take maybe two hours on a Sunday night, no more.
Ravi Menon: And your expected AWS spend after the move?
Priya Shah: We budgeted around twelve and a half thousand dollars a month on EC2 and storage.
Daniel Ortiz: Backups today are a script I wrote that copies to a second server in the same rack. If the rack burns, we lose everything.
Ravi Menon: That sounds like a risk worth fixing as part of this move.
Priya Shah: Yes, but I did not want to add scope. Our investors want the migration done before the next funding round in June.
Ravi Menon: Is there any compliance requirement, for example SOC 2?
Priya Shah: Two enterprise customers asked for SOC 2 last quarter. We do not have it yet, and it is blocking a large deal.
Ravi Menon: Understood. Who signs off on the budget?
Priya Shah: I do, together with our CEO, Tom Berg.
"""},
        {"title": "Relecloud: architecture follow-up", "text": """\
Ravi Menon: Last time you mentioned SOC 2 and backups. Has anything changed?
Priya Shah: Yes, the large deal is now conditional on a SOC 2 Type 1 report by September.
Daniel Ortiz: I looked at the database servers. Two of them are on an old PostgreSQL version, 11, that is out of support.
Ravi Menon: Would you consider a managed database instead of rehosting them as they are?
Daniel Ortiz: I would like that, because I am tired of patching. But I do not know how the application behaves on a newer version.
Ravi Menon: So a test environment would help.
Daniel Ortiz: We have no staging at all. We test in production on small customers first.
Priya Shah: That is something I would rather not tell the enterprise customers.
Ravi Menon: What about monitoring?
Daniel Ortiz: Only a free uptime checker that sends me an email. Last month we were down for forty minutes before I noticed.
Priya Shah: The cost of that outage was one refund and a very angry customer.
Ravi Menon: Is the June funding round tied to a specific milestone?
Priya Shah: The investors want to see the platform running on AWS and a clear plan for security certification.
"""},
    ],
    "Tailwind Robotics": [
        {"title": "Tailwind Robotics: VMware exit discovery", "text": """\
Ravi Menon: Tell me about your VMware environment and what triggered the exit.
Sarah Whitfield: Our renewal quote came back at almost three times the previous price after the Broadcom change. We have 90 days to decide.
Ravi Menon: How big is the estate?
Marcus Lee: About 140 VMs across two clusters, mostly Linux. A dozen are Windows, and a few are very old and nobody remembers what they do.
Ravi Menon: Any workloads that need special hardware?
Marcus Lee: Yes. Our robot simulation farm uses GPU cards passed through to VMs. We do not know how that maps to AWS.
Sarah Whitfield: The simulation farm is the heart of our product development. It cannot be down for more than a day.
Ravi Menon: What is your budget and timeline?
Sarah Whitfield: The CFO, Elena Kovac, approved a migration budget but wants a cost comparison before she signs.
Marcus Lee: Our data centre lease runs for another two years, so we would double pay unless we sublet the space.
Ravi Menon: Do you have a view on which applications to retire?
Marcus Lee: Not yet. We never did an inventory. We have a spreadsheet from three years ago.
Sarah Whitfield: Honestly, the team is small and also busy shipping a product release in the autumn.
Ravi Menon: Would you want help from a partner for the assessment?
Sarah Whitfield: Yes, and if funding can cover part of it that would make the decision easier.
"""},
        {"title": "Tailwind Robotics: assessment and risks", "text": """\
Ravi Menon: Marcus, did you manage to update the inventory?
Marcus Lee: Partly. We now count 152 VMs, and 31 of them have had no network traffic for six months.
Ravi Menon: Those are candidates to retire rather than migrate.
Marcus Lee: Agreed. The simulation farm is the open item. Our vendor says GPU licensing may not transfer to the cloud.
Sarah Whitfield: That could change the whole plan. If the licence does not move, we may need to renegotiate or rewrite parts.
Ravi Menon: Who owns the vendor relationship?
Sarah Whitfield: Our head of engineering, Jonas Pfeiffer. He is away until next week.
Ravi Menon: What about data? Do you hold anything regulated?
Marcus Lee: Test footage from customer sites, some of it in the EU. Our customers ask where it is stored.
Sarah Whitfield: Data residency is in two of our contracts.
Ravi Menon: Is there a plan for disaster recovery today?
Marcus Lee: Nightly copies to a tape library. We have never tested a restore.
Elena Kovac: I joined for the last few minutes. I need the cost comparison by the end of the month, including the dual running cost.
"""},
    ],
    "Woodgrove Bank": [
        {"title": "Woodgrove Bank: data centre exit assessment", "text": """\
Ravi Menon: Let us start with the scope of the data centre exit. What is in it?
Hannah Clarke: Two data centres, about 400 VMs, and a mainframe that is not part of this project.
Ravi Menon: What is the driver?
Hannah Clarke: One lease ends in 18 months and our board wants to cut data centre spend by a third.
David Okafor: As head of security I need to say that the regulator expects us to keep customer data in region and to show an exit plan for any cloud provider.
Ravi Menon: Which regulator rules apply?
David Okafor: Local banking supervision and PCI DSS for the card systems. We also have an internal rule that any workload with customer data needs a documented recovery time of under four hours.
Hannah Clarke: Many of our applications have no owner on record. People left and the documentation is poor.
Ravi Menon: Is there a central inventory or a CMDB?
Hannah Clarke: There is a CMDB, but I would say it is about sixty percent accurate.
Ravi Menon: What network connectivity do you have?
Priti Nair: Two 1 Gbps links to the data centres. Moving the data volume we have, about 900 terabytes, will take far too long over those.
Ravi Menon: So you would consider a physical transfer device.
Priti Nair: Probably. We have not looked into it.
Hannah Clarke: Budget is not approved yet. Our CFO will want a business case with a clear funding contribution from AWS.
Ravi Menon: Who is the executive sponsor?
Hannah Clarke: Our CIO, Martin Hale. He wants a recommendation before the board meeting in March.
"""},
    ],
    "Northwind Health": [
        {"title": "Northwind Health: clinical notes summarisation", "text": """\
Ravi Menon: Tell me about the clinical notes problem you want to solve.
Dr. Amina Yusuf: Our physicians spend up to two hours a day writing and reading notes. We want a summary of a patient's history before each visit.
Ravi Menon: Where do the notes live?
Chris Dalton: In our electronic health record, plus about eight years of scanned documents that are not searchable.
Ravi Menon: Do you have a view on patient privacy requirements?
Chris Dalton: HIPAA, of course, and the hospital board wants every model call to stay inside our own cloud account. No data to public endpoints.
Dr. Amina Yusuf: Accuracy matters more than speed. A wrong allergy in a summary would be a serious problem.
Ravi Menon: So a clinician reviews every summary?
Dr. Amina Yusuf: Yes, at least in the pilot. We also need a way to see which note each statement came from.
Ravi Menon: What does success look like for the proof of concept?
Dr. Amina Yusuf: Reduce reading time by 30 percent in two departments, cardiology and oncology, within three months.
Chris Dalton: Expected spend if it goes live is around twenty-five thousand dollars a month.
Ravi Menon: Who funds the pilot?
Chris Dalton: Innovation budget, but our compliance officer, Rosa Martin, has to approve the data handling first.
Dr. Amina Yusuf: There is also interest in using the same approach for discharge letters, but that is for later.
"""},
    ],
    "Fabrikam Logistics": [
        {"title": "Fabrikam Logistics: rehost 40 Windows VMs", "text": """\
Ravi Menon: Let us talk about the 40 Windows VMs in Pune.
Kenji Mori: They run our dispatch and tracking systems. Thirty are Windows Server 2012, which is out of support.
Ravi Menon: Why move to the Mumbai region?
Kenji Mori: Our customers want data in India, and Mumbai is close to the Pune team.
Ravi Menon: What about licensing?
Kenji Mori: We have Software Assurance on most of the Windows licences, and SQL Server Standard on six of the VMs. I am not sure how it transfers.
Lena Fischer: The data centre in Pune floods in the monsoon season. We lost power for two days last year and deliveries stopped.
Ravi Menon: That sounds like a strong reason for a disaster recovery design.
Lena Fischer: Yes, but our budget is limited. We were told AWS funding could help with the migration costs.
Ravi Menon: Who decides?
Lena Fischer: I do with the operations director, Arjun Mehta. He wants it done before the festive peak in October.
Kenji Mori: Our drivers use a mobile app that talks to those servers. If latency goes up, they will complain immediately.
"""},
    ],
}
