import { INTAKE_ACCEPT, LIMITS } from "../config.js";

export function toBase64(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1] ?? "");
    reader.onerror = () => reject(new Error(`Could not read ${file.name}.`));
    reader.readAsDataURL(file);
  });
}

export const readText = (file) => file.text();

const extOf = (name) => name.slice(name.lastIndexOf(".")).toLowerCase();

/** Client-side guard for the Intake upload rules; the server stays the authority. */
export function validateIntakeFiles({ crmFiles = [], notesFiles = [] }) {
  const problems = [];
  for (const f of crmFiles) if (!INTAKE_ACCEPT.crm.includes(extOf(f.name))) problems.push(`${f.name}: CRM records must be .csv or .xlsx.`);
  for (const f of notesFiles) if (!INTAKE_ACCEPT.notes.includes(extOf(f.name))) problems.push(`${f.name}: seller notes must be .pdf or .docx.`);
  const total = [...crmFiles, ...notesFiles].reduce((n, f) => n + f.size, 0);
  if (total > LIMITS.maxUploadBytes) problems.push(`Files total ${(total / 1048576).toFixed(1)} MB; the limit is ${LIMITS.maxUploadBytes / 1048576} MB.`);
  return problems;
}

export function downloadBlob(filename, content, type = "text/plain") {
  const url = URL.createObjectURL(new Blob([content], { type }));
  const a = Object.assign(document.createElement("a"), { href: url, download: filename });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}
