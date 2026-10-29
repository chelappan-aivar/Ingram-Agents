/**
 * Endpoints and limits for the Pipeline Runs app.
 *
 * Every execute-api host listed here must also be in the CloudFront response-headers policy
 * `connect-src` (see ../scripts/update_csp.py) and must allow this site's origin in its CORS config.
 * Live Assist is reached through the dashboard's own authenticated proxy, so no key is held here.
 */
export const ENDPOINTS = Object.freeze({
  intake: "https://xknyierfsd.execute-api.us-east-1.amazonaws.com",
  discovery: "https://eaxi52af2c.execute-api.us-east-1.amazonaws.com",
  sow: "https://m8yzw40uva.execute-api.us-east-1.amazonaws.com",
  architecture: "https://6z8b7f8w27.execute-api.us-east-1.amazonaws.com",
  cost: "https://g2fvp3xlbk.execute-api.us-east-1.amazonaws.com",
  liveAssist: "/api/v1/live-assist",
  dashboardApi: "/api/v1",
});

const MINUTE = 60_000;

/** Poll cadence and the longest each agent may take before the pipeline reports a timeout. */
export const POLLING = Object.freeze({
  intake: { intervalMs: 4_000, timeoutMs: 25 * MINUTE },
  liveAssist: { intervalMs: 2_500, timeoutMs: 12 * MINUTE },
  discovery: { intervalMs: 6_000, timeoutMs: 60 * MINUTE },
  sow: { intervalMs: 5_000, timeoutMs: 25 * MINUTE },
  architecture: { intervalMs: 6_000, timeoutMs: 25 * MINUTE },
  cost: { intervalMs: 6_000, timeoutMs: 25 * MINUTE },
});

export const LIMITS = Object.freeze({
  maxUploadBytes: 3 * 1024 * 1024, // Intake: raw bytes, all files together
  maxNotesChars: 20_000, // Intake: pasted notes
  maxLiveAssistChars: 55_000, // Live Assist accepts 60,000; leave headroom
  maxLiveAssistTurnChars: 700,
  maxDiagramBytes: 3 * 1024 * 1024, // keep the SOW request body under its 4.5 MB cap
  costMinChars: 30,
  costMaxChars: 20_000,
});

export const LIVE_ASSIST = Object.freeze({
  seller: "Ingram Seller",
  customer: "Customer",
  assist: { scope: "guided", frequency: "proactive", max_suggestions: 3 },
  replaySpeed: 100,
});

export const INTAKE_ACCEPT = Object.freeze({ crm: [".csv", ".xlsx"], notes: [".pdf", ".docx"] });
