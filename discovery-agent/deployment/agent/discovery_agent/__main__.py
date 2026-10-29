import argparse
import json
import logging
from pathlib import Path

from .analysis.pipeline import run, write_narrative
from .data.documents import load_documents
from .rendering.readme import render_markdown
from .rendering.word import markdown_to_docx


def main() -> None:
    ap = argparse.ArgumentParser(description="Run the discovery agent on the documents of one customer engagement.")
    ap.add_argument("documents", type=Path, nargs="+",
                    help="transcripts, emails (.eml), MOMs, chats, docs (.txt .md .eml .docx .pdf), in chronological order")
    ap.add_argument("--engagement", required=True, help="customer engagement name, used in the report and file names")
    ap.add_argument("--out", type=Path, default=Path("samples/_reports"), help="reports dir (inside git-ignored samples/)")
    ap.add_argument("--no-narrative", action="store_true", help="skip the Haiku prose summaries")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    report = run(load_documents(args.documents), args.engagement)
    narrative = None if args.no_narrative else write_narrative(report)

    out = args.out / args.engagement.replace(" ", "_")
    out.mkdir(parents=True, exist_ok=True)
    readme = render_markdown(report, narrative)
    (out / "discovery.json").write_text(json.dumps(report, indent=2, ensure_ascii=False))  # for other agents
    (out / "README.md").write_text(readme)  # the standard discovery document
    markdown_to_docx(readme, out / "discovery_report.docx")  # converted from the README
    if narrative:  # kept so the README can be re-rendered without calling the model again
        (out / "narrative.json").write_text(json.dumps(narrative, indent=2, ensure_ascii=False))
    print(f"wrote {out}/discovery.json, README.md, discovery_report.docx" + ("" if narrative or args.no_narrative else " (narrative skipped, see log)"))


if __name__ == "__main__":
    main()
