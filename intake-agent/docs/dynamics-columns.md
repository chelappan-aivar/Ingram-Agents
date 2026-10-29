# Dynamics 365 columns for the Ingram intake form

Generated from `deployment/shared/python/opportunity_form.py`; regenerate after changing the form.
Every intake form question is a custom column on the **Opportunity** table, prefix `ingr_`. Choice questions are
local choice columns with option values 100,000,000 + position. Create them all with
`python3 scripts/create_dynamics_columns.py --apply` (System Customizer role, publisher prefix `ingr`).

Standard columns the form also fills: `name` (opportunity title), `customerid` (End-Customer Customer Name, account
found or created), `estimatedvalue` (MRR x 12, else ARR), `description` (free-text answers).

118 columns: 113 questions + 5 Microsoft Forms response fields.


## Form response

| Column | Type | Question | Options |
|---|---|---|---|
| `ingr_formresponseid` | Text | Id |  |
| `ingr_formstarttime` | DateTime | Start time |  |
| `ingr_formcompletiontime` | DateTime | Completion time |  |
| `ingr_submitteremail` | Email | Email |  |
| `ingr_submittername` | Text | Name |  |

## Request

| Column | Type | Question | Options |
|---|---|---|---|
| `ingr_requesttype` | Choice | What type of request would you like to submit? | 100000000 Assessment; 100000001 Cloud Services Opportunity; 100000002 Cyber Security Opportunity; 100000003 Marketing Campaign; 100000004 Generic Service Request; 100000005 Data & AI Opportunity; 100000006 Microsoft Copilot Opportunity; 100000007 General Inquiry |

## Ingram Micro

| Column | Type | Question | Options |
|---|---|---|---|
| `ingr_ingramcountry` | Text | Ingram Micro Country |  |
| `ingr_ingramcontact` | Text | Ingram Micro Associate Contact (Primary Contact) |  |
| `ingr_ingramcontactphone` | Phone | Ingram Micro Associate Contact Phone Number |  |
| `ingr_ingrambu` | Text | Ingram Micro BU |  |
| `ingr_ingramaccountmanager` | Text | Responsible Ingram Micro Account Manager |  |
| `ingr_campaigncode` | Text | Campaign Code (if known) |  |

## Partner

| Column | Type | Question | Options |
|---|---|---|---|
| `ingr_companyname` | Text | Company Name |  |
| `ingr_partnercontactname` | Text | Partner Contact Name |  |
| `ingr_partnercontactphone` | Phone | Partner Contact Phone Number |  |
| `ingr_partnercontactemail` | Email | Partner Contact Email Address |  |
| `ingr_partnersowauthority` | Choice | Does the partner contact have signing authority for the Statement of Work (SOW)? | 100000000 Yes; 100000001 No |
| `ingr_sowsignername` | Text | Please provide the name of the person authorized to sign the SOW. |  |
| `ingr_sowsigneremail` | Email | Please provide the e-mail address of the person authorized to sign the SOW. |  |
| `ingr_partnercontacttype` | Text | Partner Contact Type |  |

## End customer

| Column | Type | Question | Options |
|---|---|---|---|
| `ingr_endcustomername` | Text | End-Customer Customer Name |  |
| `ingr_endcustomercontactname` | Text | End-Customer Contact Name |  |
| `ingr_endcustomercontactphone` | Phone | End-Customer Contact Phone Number |  |
| `ingr_endcustomercontactemail` | Email | End-Customer Contact Email Address |  |
| `ingr_endcustomerjobtitle` | Text | End-Customer Contact Job Title |  |
| `ingr_endcustomerdomain` | Text | End-Customer Business Domain |  |
| `ingr_endcustomercountry` | Text | End-Customer Country |  |
| `ingr_endcustomerpostcode` | Text | End-Customer Post Code / Zip Code |  |
| `ingr_endcustomerindustry` | Text | End-Customer Industry Vertical |  |
| `ingr_endcustomertpid` | Text | End-Customer Microsoft Top Parent ID (TPID) if known |  |

## Assessment

| Column | Type | Question | Options |
|---|---|---|---|
| `ingr_serviceselection` | Text | Service Selection |  |
| `ingr_assessmentvendor` | Choice | Which Vendor Assessment? | 100000000 AWS; 100000001 Microsoft Azure; 100000002 Google Cloud; 100000003 Other |
| `ingr_keydriver` | MultilineText | What is the customers key driver for considering the cloud? |  |
| `ingr_acevalidated` | Choice | Has this opportunity been registered and validated in ACE? | 100000000 Yes; 100000001 No |
| `ingr_aceidassessment` | Text | Please provide the ACE ID. |  |
| `ingr_anticipatedstartdate` | DateOnly | Anticipated Start Date (dd/mm/yyyy) |  |
| `ingr_totalservers` | WholeNumber | Total Number of Servers |  |
| `ingr_totalwindowsservers` | WholeNumber | Total Number of Windows Servers |  |
| `ingr_totalsqlservers` | WholeNumber | Total Number of SQL Servers |  |
| `ingr_totalsqlcores` | WholeNumber | Total Number of SQL Cores |  |
| `ingr_sourceenvironment` | Choice | What source environment does the customer currently have? | 100000000 On-premises data centre; 100000001 VMware; 100000002 Colocation; 100000003 Microsoft Azure; 100000004 Google Cloud; 100000005 Other cloud; 100000006 AWS (already); 100000007 Mixed |
| `ingr_discoverytoolpermitted` | Choice | Will the end customer permit the deployment of a data discovery tool within their environment? | 100000000 Yes; 100000001 No; 100000002 Not sure |

## Cloud Services opportunity

| Column | Type | Question | Options |
|---|---|---|---|
| `ingr_csvendor` | Choice | Which vendor is your request against? | 100000000 AWS; 100000001 Microsoft; 100000002 Google Cloud; 100000003 Other |
| `ingr_msopportunitytype` | Text | What Microsoft Opportunity Type is this? |  |
| `ingr_awsopportunitytype` | Choice | What AWS opportunity type is this? | 100000000 Migration; 100000001 Generative AI; 100000002 ISV workload migration; 100000003 SAP migration; 100000004 Oracle migration; 100000005 Modernization; 100000006 Data & Analytics; 100000007 New workload; 100000008 Other |
| `ingr_aceregistered` | Choice | Has this opportunity been registered in ACE yet? | 100000000 Yes; 100000001 No |
| `ingr_aceid` | Text | Please enter the ACE ID. |  |
| `ingr_csopportunityid` | Text | Cloud Services Opportunity ID |  |
| `ingr_csopportunitytitle` | Text | Cloud Services Opportunity Title |  |
| `ingr_csopportunitydetails` | MultilineText | Cloud Services Opportunity Details |  |
| `ingr_estimatedarr` | Text | Estimated Annual Recurring Revenue (ARR) |  |
| `ingr_cstimeline` | Choice | What's the expected timeline for commencing the delivery of this opportunity? | 100000000 Within 1 month; 100000001 1-3 months; 100000002 3-6 months; 100000003 6-12 months; 100000004 More than 12 months |
| `ingr_csnextstep` | MultilineText | What is your preferred next step from the Ingram Micro Cloud Services team? |  |

## Cyber Security opportunity

| Column | Type | Question | Options |
|---|---|---|---|
| `ingr_cyberopportunityid` | Text | Cyber Security Opportunity ID |  |
| `ingr_cyberopportunitytitle` | Text | Cyber Security Opportunity Title |  |
| `ingr_cyberservicekind` | Text | What kind of Cyber Security Service are you looking for? |  |
| `ingr_cybercustomerknows` | Choice | Does the customer know what they want , or do they need additional Information ? | 100000000 They know what they want; 100000001 They need additional information |
| `ingr_cyberinterestedservices` | MultilineText | They are interested in the following Services (please add the Details in the seperate Box below (Number of IP adresses) |  |
| `ingr_cyberdetailsips` | MultilineText | Additional Details (Scope of the Service - Number of external/internal IPs / URLs of WebApplications) |  |
| `ingr_cybersecurityservice` | Text | Which kind of security service ? |  |
| `ingr_cyberdetailsscope` | MultilineText | Additional Details (Scope of the Service) |  |

## Marketing campaign

| Column | Type | Question | Options |
|---|---|---|---|
| `ingr_campaignfor` | Text | The campaign should be made for a |  |
| `ingr_campaignvendors` | Text | Which vendor(s) is this campaign focused on? |  |
| `ingr_campaigndomains` | WholeNumber | How many domains are involved ? |  |
| `ingr_campaignhelp` | MultilineText | Where can we help ? |  |

## Generic service request

| Column | Type | Question | Options |
|---|---|---|---|
| `ingr_gsropportunityid` | Text | Generic Service Request Opportunity ID |  |
| `ingr_gsrtitle` | Text | Generic Service Request Opportunity title |  |
| `ingr_gsrdescription` | MultilineText | Generic Service Request detailed opportunity description |  |
| `ingr_gsrvendors` | Text | Service request is related to which vendor(s)? |  |
| `ingr_deliverylanguage` | Text | Required delivery language. |  |
| `ingr_deliverymethod` | Choice | Required delivery method. | 100000000 Remote; 100000001 On premise; 100000002 Hybrid |
| `ingr_fulfillmentaddress` | MultilineText | Fulfillment address (required for on premise delivery) |  |
| `ingr_deliverytimeframe` | Text | Delivery time frame |  |
| `ingr_responsecriticality` | Choice | How critical is the response time? | 100000000 Critical; 100000001 High; 100000002 Normal; 100000003 Low |
| `ingr_whyotherdelivery` | MultilineText | Why are you looking to leverage other service delivery organizations on this opportunity? |  |
| `ingr_gsrnextstep` | MultilineText | What is your preferred next step? |  |

## Data & AI opportunity

| Column | Type | Question | Options |
|---|---|---|---|
| `ingr_daivendor` | Choice | Please select a vendor. | 100000000 AWS; 100000001 Microsoft; 100000002 Google Cloud; 100000003 Other |
| `ingr_daistage` | Choice | Which Stage Is the Opportunity In | 100000000 Prospecting; 100000001 Qualification; 100000002 Proposal; 100000003 Negotiation; 100000004 Committed |
| `ingr_daiopportunityid` | Text | Data & AI Opportunity ID |  |
| `ingr_daititle` | Text | Data & AI Opportunity Title |  |
| `ingr_daidetails` | MultilineText | Data & AI Opportunity Details |  |
| `ingr_daiprojectkind` | Choice | Is this project for a specific end-customer (Project Delivery), or is the partner developing a new solution to support multiple end-customers (Solution Factory)? | 100000000 Project Delivery; 100000001 Solution Factory |
| `ingr_daibudget` | Choice | Does the customer have a budget allocated for this Data & AI project? | 100000000 Yes; 100000001 No; 100000002 Not sure |
| `ingr_partnercapabilities` | MultilineText | Partner Delivery Capabilities |  |
| `ingr_partnersupportareas` | MultilineText | Areas where Partner requires support |  |
| `ingr_daitimeline` | Choice | Expected timeline for commencing the delivery of this Data & AI opportunity? | 100000000 Within 1 month; 100000001 1-3 months; 100000002 3-6 months; 100000003 6-12 months; 100000004 More than 12 months |
| `ingr_dainextstep` | MultilineText | Preferred next step for this Data & AI opportunity? |  |

## Microsoft Copilot opportunity

| Column | Type | Question | Options |
|---|---|---|---|
| `ingr_copilotopportunityid` | Text | Microsoft Copilot Opportunity ID |  |
| `ingr_copilottitle` | Text | Microsoft Copilot Opportunity Title |  |
| `ingr_endcustomerbusinesssize` | Choice | End-customer Business Size | 100000000 Startup; 100000001 Small business; 100000002 Mid-market; 100000003 Enterprise; 100000004 Public sector |
| `ingr_copilotlicenses` | WholeNumber | How many Copilot licenses does the end-customer currently have? |  |
| `ingr_endcustomerwebsite` | Text | End-customer Website |  |
| `ingr_execsponsor` | Choice | Does the end-customer have an executive sponsor for AI adoption? | 100000000 Yes; 100000001 No |
| `ingr_execattend` | Choice | Will executive leadership attend the Exec Planning Session? | 100000000 Yes; 100000001 No |
| `ingr_aibudget` | Choice | Does the end-customer have a budget allocated for AI tools & related services for the next 3-6 months? | 100000000 Yes; 100000001 No; 100000002 Not sure |

## Qualification

| Column | Type | Question | Options |
|---|---|---|---|
| `ingr_partoflargerprogram` | Choice | Is this opportunity part of a larger end-customer program / project? | 100000000 Yes; 100000001 No; 100000002 Not sure |
| `ingr_closetodecisionmaker` | Choice | Are you close to the reseller / end-customer decision maker for this opportunity? | 100000000 Yes; 100000001 No |
| `ingr_compellingevent` | MultilineText | Are you aware of a compelling end-customer event that is driving this opportunity? |  |
| `ingr_deliverytimeline` | Choice | What's the expected timeline for commencing the delivery of this opportunity? | 100000000 Within 1 month; 100000001 1-3 months; 100000002 3-6 months; 100000003 6-12 months; 100000004 More than 12 months |
| `ingr_budgetallocated` | Choice | Does the end-customer already have a budget allocated? | 100000000 Yes; 100000001 No; 100000002 Not sure |
| `ingr_competition` | MultilineText | Are you aware of anyone competing for the same opportunity (provide details)? |  |
| `ingr_likelihoodproceed` | Choice | How likely do you believe the reseller / end-customer is going to proceed with the opportunity? | 100000000 High; 100000001 Medium; 100000002 Low |
| `ingr_likelihoodselected` | Choice | How likely do you believe the reseller / end-customer will select yourselves as the preferred delivery partner? | 100000000 High; 100000001 Medium; 100000002 Low |

## General inquiry

| Column | Type | Question | Options |
|---|---|---|---|
| `ingr_requestingcountry` | Text | Requesting Country |  |
| `ingr_practice` | Text | Practice |  |
| `ingr_vendorsinvolved` | Text | Vendor(s) Involved |  |
| `ingr_technology` | Text | Technology |  |
| `ingr_estimateddeliverytime` | Text | Estimated Delivery Time (if known) |  |
| `ingr_inquirydetails` | MultilineText | Inquiry Details |  |

## AWS funding

| Column | Type | Question | Options |
|---|---|---|---|
| `ingr_fundsegment` | Choice | Customer segment | 100000000 Startup; 100000001 Non-startup (enterprise and others); 100000002 Not sure |
| `ingr_fundworkload` | Choice | Workload | 100000000 Migration; 100000001 Generative AI; 100000002 ISV workload migration; 100000003 SAP migration; 100000004 Oracle migration; 100000005 Other; 100000006 Not sure |
| `ingr_fundmigrationsource` | Choice | Migrating from | 100000000 On-premises; 100000001 Another cloud; 100000002 Colocation; 100000003 Not sure |
| `ingr_fundvms` | Choice | Are virtual machines (VMs) in scope? | 100000000 Yes; 100000001 No; 100000002 Not sure |
| `ingr_fundgenaistage` | Choice | Gen AI stage | 100000000 Proof of concept; 100000001 Production-ready; 100000002 Not sure |
| `ingr_fundgenaitype` | Choice | Gen AI workload | 100000000 Fresh workload; 100000001 Modernization or optimization; 100000002 Not sure |
| `ingr_fundmrrusd` | Decimal | Expected AWS monthly run rate (MRR), USD |  |
| `ingr_fundarrusd` | Decimal | Expected AWS annual recurring revenue (ARR), USD |  |

## Anything else

| Column | Type | Question | Options |
|---|---|---|---|
| `ingr_otherremarks` | MultilineText | Other remarks or additional comments |  |
