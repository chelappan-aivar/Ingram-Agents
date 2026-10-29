"""Generate docs/aws-architecture/funding-agent-aws.drawio in the style of intake-agent-aws.drawio (same shapes,
colours, numbered steps and legend), then export a PNG with the draw.io CLI.

    python3 deployment/scripts/gen_funding_diagram.py
"""

import html
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "docs" / "aws-architecture"
SRC = ET.parse(OUT / "intake-agent-aws.drawio").getroot()
STYLE = {c.get("id"): c.get("style") for c in SRC.iter("mxCell")}   # reuse the original diagram's styles

ICON = STYLE["amp-i0"].split("resIcon=")[0]   # resource icon base style
def icon_style(res: str, fill: str) -> str:
    base = STYLE["amp-i0"]
    for part in base.split(";"):
        if part.startswith("resIcon="):
            base = base.replace(part, f"resIcon=mxgraph.aws4.{res}")
        if part.startswith("fillColor="):
            base = base.replace(part, f"fillColor={fill}")
    return base


def res_of(cell_id: str) -> str:
    return next(p.split("=", 1)[1] for p in STYLE[cell_id].split(";") if p.startswith("resIcon=")).rsplit(".", 1)[1]


cells: list[str] = []


def esc(text: str) -> str:
    return html.escape(text, quote=True)


def vertex(cid, value, style, x, y, w, h, parent="1"):
    cells.append(f'<mxCell id="{cid}" value="{esc(value)}" style="{esc(style)}" vertex="1" parent="{parent}">'
                 f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')


def box(cid, title, style_id, x, y, w, h, icons, panel=None, panel_h=0):
    """A titled container (style copied from the original) with icons [(res, fill, label)] spread across it."""
    vertex(cid, title, STYLE[style_id], x, y, w, h)
    n = len(icons)
    for i, (res, fill, label) in enumerate(icons):
        cx = w * (i + 0.5) / n - 24
        vertex(f"{cid}-i{i}", label, icon_style(res, fill), cx, 30 if style_id not in ("aux", "data", "deploy") else 40,
               48, 48, parent=cid)
    if panel:
        vertex(f"{cid}-panel", panel, STYLE["rt-panel"], 12, h - panel_h - 12, w - 24, panel_h, parent=cid)


def edge(cid, label, src, dst, exit_, entry, points=(), dashed=False, both=False, style_id="e1", at=None):
    style = STYLE["e10" if dashed else style_id]
    style = ";".join(p for p in style.split(";") if not p.split("=")[0] in
                     ("exitX", "exitY", "exitDx", "exitDy", "entryX", "entryY", "entryDx", "entryDy", "startArrow", "startFill"))
    style += f";exitX={exit_[0]};exitY={exit_[1]};exitDx=0;exitDy=0;entryX={entry[0]};entryY={entry[1]};entryDx=0;entryDy=0;"
    if both:
        style += "startArrow=classic;startFill=1;"
    pts = "".join(f'<mxPoint x="{x}" y="{y}"/>' for x, y in points)
    pos = f' x="{at}"' if at is not None else ""   # label position along the edge (-1 .. 1)
    geo = f'<mxGeometry{pos} relative="1" as="geometry">{f"<Array as=\"points\">{pts}</Array>" if pts else ""}</mxGeometry>'
    cells.append(f'<mxCell id="{cid}" value="{esc(label)}" style="{esc(style)}" edge="1" parent="1" source="{src}" '
                 f'target="{dst}">{geo}</mxCell>')


def step(n, x, y):
    vertex(f"step-{n}", str(n), STYLE["step-1-125-300"], x, y, 34, 34)


def lbl(name, *lines):
    return name + "".join(f"<div><i>{line}</i></div>" for line in lines)


# ---------------------------------------------------------------- header
vertex("title", "Project Catalyst: Intake Agent funding check on AWS", STYLE["title"], 50, 30, 1800, 40)
vertex("subtitle", "Opportunities from Microsoft Dynamics 365 in, AWS Funding Programs Decision Framework decisions out. "
                   "Intake form columns (ingr_) read directly; Claude Sonnet 4.5 only for free text; deterministic rules engine.",
       STYLE["subtitle"], 50, 72, 1800, 30)
vertex("rule", "", STYLE["rule"], 50, 108, 1800, 10)
vertex("aws-cloud", "AWS Cloud", STYLE["aws-cloud"], 230, 140, 1460, 900)
vertex("region", "Region: us-east-1 (stack ingram-intake, AWS SAM)", STYLE["region"], 250, 175, 1420, 845)

# ---------------------------------------------------------------- outside AWS
box("users", "Users", "users", 40, 430, 150, 130, [(res_of("users-i0"), "#232F3D", "Ingram sellers<div><i>Microsoft sign-in</i></div>")])
box("ms", "Microsoft 365", "ms", 1720, 200, 150, 160,
    [(res_of("ms-i0"), "#232F3D", lbl("Entra ID", "device-code sign-in", "client-credentials", "token (real Dynamics)"))])
vertex("dyn", "Microsoft Dynamics 365", STYLE["ms"].replace("shadow=1", "shadow=0") + "dashed=1;", 1720, 470, 150, 180)
vertex("dyn-i0", lbl("Dataverse Web API", "real environment", "(later: set", "DYNAMICS_URL)"), icon_style("client", "#666666"),
       51, 30, 48, 48, parent="dyn")
box("dev", "Developer", "dev", 1720, 860, 150, 130,
    [(res_of("dev-i0"), "#232F3D", "deploy.sh<div><i>seed_dynamics.py</i></div>")])

# ---------------------------------------------------------------- inside the region
box("amp", "Web UIs", "amp", 300, 200, 300, 140,
    [(res_of("amp-i0"), "#DD344C", lbl("Amplify Hosting", "ingram-funding-desk", "Intake Agent desk")),
     (res_of("amp-i0"), "#DD344C", lbl("Amplify Hosting", "ingram-intake-ui", "funding.html (form)"))])
box("apigw", "API", "apigw", 330, 430, 160, 150,
    [(res_of("apigw-i0"), "#8C4FFF", lbl("Amazon API Gateway", "HTTP API, throttled", "/funding/* routes"))])
box("lambda", "UI API", "lambda", 570, 430, 180, 150,
    [(res_of("lambda-i0"), "#ED7100", lbl("AWS Lambda", "ingram-intake-api", "session, sign-in gate,", "form validation, quotas"))])
box("rt", "Intake Agent (funding check)", "rt", 830, 380, 330, 290,
    [(res_of("rt-i0"), "#01A88D", lbl("Bedrock AgentCore Runtime", "ingram_intake_agent, action funding_batch"))],
    panel="<b>Background task per check (4 workers, up to 25)</b><br>"
          "• fetch: opportunity + notes from Dynamics (OData)<br>"
          "• facts: ingr_ form columns first (servers → VMs)<br>"
          "• Claude only when the columns leave a gap;<br>&nbsp;&nbsp;every quote checked word for word<br>"
          "• decide: rules engine (segment → workload → ARR)<br>"
          "• record: DynamoDB + note on the opportunity", panel_h=135)
box("br", "Foundation Models", "br", 1260, 205, 170, 140,
    [(res_of("br-i0"), "#01A88D", lbl("Amazon Bedrock", "Claude Sonnet 4.5", "free-text facts only"))])
vertex("mock", "Mock Dynamics 365 (Dataverse Web API v9.2)", STYLE["apigw"].replace("#EDE7F6", "#F3F9FF").replace("#8C4FFF", "#007CBD"),
       1265, 420, 385, 260)
for i, (res, fill, label) in enumerate([
        (res_of("apigw-i0"), "#8C4FFF", lbl("API Gateway", "OAuth token +", "/api/data/v9.2")),
        (res_of("lambda-i0"), "#ED7100", lbl("AWS Lambda", "ingram-mock-dynamics", "OData subset")),
        (res_of("data-i0"), "#C925D1", lbl("DynamoDB dummy tables", "accounts, annotations,", "opportunities"))]):
    vertex(f"mock-i{i}", label, icon_style(res, fill), 385 * (i + 0.5) / 3 - 24, 34, 48, 48, parent="mock")
vertex("mock-panel", "<b>Opportunity row</b>: name, account, estimatedvalue, close date, status<br>"
                     "+ <b>118 ingr_ columns</b>, one per intake form question<br>"
                     "(choice questions = choice columns, e.g. ingr_requesttype)<br>"
                     "15 dummy opportunities, every framework branch",
       STYLE["rt-panel"].replace("#01A88D", "#007CBD"), 12, 160, 361, 88, parent="mock")

box("aux", "Security and observability", "aux", 290, 820, 560, 170,
    [("identity_and_access_management", "#DD344C", lbl("AWS IAM", "least-privilege", "roles")),
     ("secrets_manager", "#DD344C", lbl("Secrets Manager", "Dynamics client", "secret")),
     ("cloudwatch_2", "#E7157B", lbl("CloudWatch", "logs, metrics")),
     ("xray", "#C925D1", lbl("AWS X-Ray", "traces"))])
box("data", "Check state", "data", 890, 820, 300, 170,
    [(res_of("data-i0"), "#C925D1", lbl("Amazon DynamoDB", "ingram-intake-runs", "SESSION#, FUNDOPP#, FUNDCHK#,", "FUNDLATEST#, QUOTA#"))])
box("deploy", "Deployment", "deploy", 1230, 820, 420, 170,
    [(res_of("deploy-i0"), "#3F8624", lbl("Amazon S3", "agent code zip")),
     (res_of("lambda-i0"), "#ED7100", lbl("Lambda layer", "opportunity form +", "Dynamics client")),
     (res_of("deploy-i1"), "#E7157B", lbl("AWS CloudFormation", "SAM template.yaml"))])

# ---------------------------------------------------------------- flows
edge("e1", "HTTPS: load UI", "users", "amp", (0.5, 0), (0, 0.5), [(115, 270)])
edge("e2", "REST + polling", "users", "apigw", (1, 0.5), (0, 0.5))
edge("e3", "proxy", "apigw", "lambda", (1, 0.5), (0, 0.5))
edge("e4", "InvokeAgentRuntime", "lambda", "rt", (1, 0.5), (0, 0.5))
edge("e5", "InvokeModel", "rt", "br", (0.8, 0), (0, 0.5), [(1110, 275)])
edge("e6", "OData", "rt", "mock", (1, 0.5), (0, 0.5), both=True)
edge("e7", "create (ingr_ columns), list", "lambda", "mock", (0.5, 1), (0.5, 1), [(660, 720), (1457, 720)], both=True,
     at=0.55)
edge("e8", "OAuth device code", "lambda", "ms", (0.5, 0), (0.5, 1), [(660, 375), (1795, 375)])
edge("e9", "sessions, checks, quotas", "lambda", "data", (0.25, 1), (0.25, 0), [(615, 780), (965, 780)], both=True)
edge("e10", "FUNDCHK#, FUNDLATEST#", "rt", "data", (0.4, 1), (0.6, 0), [(970, 750), (1070, 750)])
edge("e11", "later", "mock", "dyn", (1, 0.75), (0, 0.5), dashed=True)
edge("e12", "code zip + layer", "deploy", "rt", (0.25, 0), (0.85, 1), [(1335, 790), (1128, 790)], dashed=True)
edge("e13", "sam deploy, seed", "dev", "deploy", (0, 0.5), (1, 0.5), dashed=True, at=-0.3)

for n, (x, y) in enumerate([(125, 300), (240, 455), (515, 395), (770, 455), (1196, 470), (1195, 226), (1135, 745),
                            (1790, 815)], start=1):
    step(n, x, y)

# ---------------------------------------------------------------- legend
vertex("legend-bg", "", STYLE["legend-bg"], 40, 1080, 1820, 560)
LEGEND = [
    ("Load the UI and sign in", "Intake Agent desk (Amplify ingram-funding-desk) or funding.html. Sign-in with Microsoft",
     "(device code) is required: the flow reads and writes Dynamics. Sessions are SHA-256 hashed in DynamoDB."),
    ("Create or list opportunities", "POST /funding/opportunities validates the intake form answers (113 questions) and",
     "creates the Dynamics opportunity with ingr_ columns. GET /funding/dynamics lists them with the latest decision."),
    ("Microsoft sign-in", "Lambda runs the Entra device-code flow for users. Real Dynamics uses client credentials",
     "for the agent's app user; the client secret lives in Secrets Manager."),
    ("Start the checks", "POST /funding/checks/batch (up to 25) queues FUNDCHK# items and calls InvokeAgentRuntime once;",
     "the agent answers at once and runs the checks in a background task, 4 at a time."),
    ("Fetch from Dynamics", "The agent signs in (OAuth token), then GETs the opportunity with its 118 ingr_ columns and",
     "notes through the Dataverse Web API. Mock now; real Dynamics later with the same client code."),
    ("Facts and decision", "Form columns give the facts first. Claude reads free text only when a fact is missing, and a",
     "fact counts only if its quote is in the text. The rules engine then applies segment → workload → ARR."),
    ("Record and show", "FUNDCHK# (full result), FUNDLATEST# (shared latest decision) and an 'AWS funding check' note",
     "on the Dynamics opportunity. The desk polls and shows program, amount, path, facts and questions."),
    ("Deploy and seed", "deploy.sh builds the agent zip and shared layer, runs sam deploy (DYNAMICS_URL=mock adds the",
     "mock), publishes both UIs. seed_dynamics.py loads the 15 dummy opportunities through the Web API."),
]
for i, (head, a, b) in enumerate(LEGEND):
    col, row = divmod(i, 4)
    x, y = 70 + col * 890, 1110 + row * 104
    vertex(f"legend-badge-{i + 1}", str(i + 1), STYLE["legend-badge-1"], x, y, 48, 48)
    vertex(f"legend-text-{i + 1}", f"<div><b>{head}</b></div><div>- {a}</div><div>- {b}</div>", STYLE["legend-text-1"],
           x + 65, y - 2, 800, 98)
vertex("legend-lines", "<div><b>Line styles</b></div><b>Solid</b>: request and data flow at run time<br>"
       "<b>Solid, two arrows</b>: read and write<br><b>Dashed gray</b>: build, deploy or later swap", STYLE["legend-lines"],
       70, 1530, 360, 96)
vertex("legend-notes", "<div><i>Decisions are deterministic code (agent/funding/rules.py); Claude never decides funding.</i></div>"
       "<div><i>Choice columns: option value 100,000,000 + position; reading accepts a JSON answers note too.</i></div>"
       "<div><i>Mock Dynamics: 3 DynamoDB tables, generated client secret, removed when DYNAMICS_URL is real.</i></div>"
       "<div><i>Agent reaches Dynamics over HTTPS (NetworkMode PUBLIC, no VPC). OData: token, then GET opportunity + notes, POST note.</i></div>", STYLE["legend-notes"],
       470, 1526, 1360, 100)

xml = ('<mxfile host="Electron"><diagram name="Intake agent funding check" id="f1"><mxGraphModel dx="1400" dy="900" '
       'grid="0" gridSize="10" guides="1" tooltips="1" connect="1" arrows="1" fold="1" page="0" pageScale="1" '
       'pageWidth="1900" pageHeight="1660" math="0" shadow="0"><root><mxCell id="0"/><mxCell id="1" parent="0"/>'
       + "".join(cells) + "</root></mxGraphModel></diagram></mxfile>")
drawio = OUT / "funding-agent-aws.drawio"
drawio.write_text(xml)
ET.fromstring(xml)   # well-formed
png = OUT / "funding-agent-aws.png"
subprocess.run(["drawio", "-x", "-f", "png", "-s", "1.5", "-b", "20", "-o", str(png), str(drawio)], check=True,
               capture_output=True)
print(drawio, png, sep="\n")
