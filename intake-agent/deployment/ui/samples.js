// Sample input files (fictional customers) shown in the CRM and Seller notes boxes.
// The files are copied from ../test-data into ui/samples/ by scripts/deploy.sh.
window.INTAKE_SAMPLES = [
  { kind: "crm", name: "crm-records.csv", default: true,
    about: "5 CRM opportunities: Northwind Health, Fabrikam Logistics, Contoso Retail, Tailspin Toys, Margie's Travel" },
  { kind: "crm", name: "crm-records.xlsx",
    about: "The same 5 records as an Excel export (use one of the two)" },
  { kind: "notes", name: "seller-notes-contoso-retail.docx", default: true,
    about: "SAP ECC move to AWS Mumbai: newer numbers than the CRM record (45 servers, 14 TB, Q2 2027)" },
  { kind: "notes", name: "seller-notes-tailspin-toys.docx", default: true,
    about: "Colocation exit to AWS Frankfurt: 60 VMs, SQL Server 2014, GDPR, budget not confirmed" },
  { kind: "notes", name: "seller-notes-margies-travel.pdf", default: true,
    about: "Generative AI travel assistant in 3 languages for an online travel agency in Milan" },
];
