// Endpoints of the seven deployed agent backends. Contains no secrets.
// The Live Assist access key is typed by the user per browser session and is never stored here.
window.UNIFIED_CONFIG = {
  intake: "https://xknyierfsd.execute-api.us-east-1.amazonaws.com",
  liveAssist: "https://bg0681jeib.execute-api.us-east-1.amazonaws.com",
  discovery: "https://eaxi52af2c.execute-api.us-east-1.amazonaws.com",
  sow: "https://jt1cq55g59.execute-api.us-east-1.amazonaws.com/demo/api/v1/sow",
  architecture: "https://6z8b7f8w27.execute-api.us-east-1.amazonaws.com",
  cost: "https://w2ydw8uim4.execute-api.us-east-1.amazonaws.com/prod/invoke-agent",
  orchestrator: "https://8ed3d0lms6.execute-api.us-east-1.amazonaws.com/prod",
};
