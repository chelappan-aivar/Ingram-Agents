"""The sequencer: walks the six stages, carrying each one's output into the next.

One stage per call to `advance`, so a worker Lambda can run a stage, persist, and come back.
`run` drives it to completion for callers that can block.

Everything the stages need from each other goes through `adapters`, and every agent call goes
through an `AgentCaller`, so this module has no AWS dependency and the whole sequence can be
tested against fake replies.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from orchestrator import adapters, sow_edit
from orchestrator.agents import (AGENT_ARCHITECTURE, AGENT_COST, AGENT_SOW, AgentCaller,
                                 AgentError, JobPoller, session_id)
from orchestrator.contracts import (REVISION_LABELS, REVISION_TARGETS, STAGES, PipelineState, Stage,
                                    StageRun, Status)
from orchestrator.scope import extract_scope, scope_is_usable

Logger = Callable[[str, str], None]          # (stage, message)

# How much of each agent's reply to keep. The diagram PNG is the big one, and the combined
# review needs it, so it is kept; everything merely diagnostic is dropped.
KEEP_ARCHITECTURE = ("run_id", "adr", "design", "usage", "diagram", "scope", "scope_warnings",
                     "report_status", "job_id")
KEEP_COST = ("estimate", "estimate_id", "calculator_url", "estimate_name", "services_count",
             "checklist", "coverage")


def _noop(stage: str, message: str) -> None:
    return None


class Pipeline:
    """Runs one engagement: SOW -> SA review -> WA review -> architecture -> cost -> final SOW review."""

    def __init__(self, call: AgentCaller, *, log: Logger = _noop,
                 poller: Optional[JobPoller] = None,
                 wa_verify: bool = False,
                 combined_verify: bool = True) -> None:
        self._call = call
        self._log = log
        self._poller = poller
        # Opt-in model calls. The Well-Architected rules are deterministic and free, so the
        # advisory model pass is off by default; the final SOW review is the one place a
        # single extra model call is clearly worth it.
        self._wa_verify = wa_verify
        self._combined_verify = combined_verify

    # ---------------------------------------------------------------- driving
    def advance(self, state: PipelineState) -> Optional[StageRun]:
        """Run the next pending stage. Returns its StageRun, or None when there is nothing to do."""
        stage = state.next_stage()
        if stage is None:
            return None
        run = state.run(stage)
        run.status = Status.RUNNING
        started = time.monotonic()
        self._log(stage.value, f"{run.label} started")
        try:
            self._handlers()[stage](state, run)
        except AgentError as exc:
            run.status, run.error = Status.FAILED, str(exc)
            self._log(stage.value, f"{run.label} failed: {exc}")
        except ValueError as exc:
            # A bad handoff between stages: our problem, not the agent's.
            run.status, run.error = Status.FAILED, str(exc)
            self._log(stage.value, f"{run.label} failed: {exc}")
        finally:
            run.seconds = round(time.monotonic() - started, 1)
        if run.status == Status.RUNNING:          # a handler that forgot to set a status
            run.status = Status.COMPLETED
        if run.status in (Status.COMPLETED, Status.SKIPPED):
            self._log(stage.value, f"{run.label} {run.status.value} in {run.seconds}s")
        return run

    def run(self, state: PipelineState) -> PipelineState:
        """Drive to completion, or until a stage fails or blocks."""
        while self.advance(state) is not None:
            pass
        return state

    def _handlers(self) -> dict[Stage, Callable[[PipelineState, StageRun], None]]:
        return {
            Stage.SOW_DRAFT: self._sow_draft,
            Stage.SA_REVIEW: self._sa_review,
            Stage.WA_REVIEW: self._wa_review,
            Stage.ARCHITECTURE: self._architecture,
            Stage.COST: self._cost,
            Stage.COMBINED_REVIEW: self._final_review,
        }

    def _invoke(self, agent: str, state: PipelineState, stage: Stage,
                payload: dict[str, Any], *, label: str = "") -> dict[str, Any]:
        result = self._call(agent, payload,
                            session=session_id(agent, state.run_id, label or stage.value))
        status = str(result.get("status") or "")
        if status in ("failed", "error"):
            raise AgentError(agent, str(result.get("error") or f"the {agent} agent failed"))
        return result

    # ---------------------------------------------------------------- stage 1: SOW
    def _sow_draft(self, state: PipelineState, run: StageRun) -> None:
        """Generate the SOW from intake and discovery only.

        The architecture and cost blocks are deliberately absent here: they do not exist yet.
        The SOW's own cross-checks for them degrade to `not_applicable`, which is why the
        combined review at the end is a separate stage rather than a nicety.
        """
        if state.sow_markdown:
            run.status, run.detail = Status.SKIPPED, "A SOW draft was supplied with the request."
            return
        upstream = {k: v for k, v in (state.upstream or {}).items()
                    if k not in ("architecture", "cost")}
        intake = upstream.get("intake") or {}
        request: dict[str, Any] = {
            "customer_name": str(intake.get("customer_name") or "").strip() or "Customer",   # required by the agent
            "template_id": state.template_id,
            "upstream": upstream or None,
        }
        opportunity = str((upstream.get("meta") or {}).get("opportunity_id") or "").strip()
        if opportunity:
            request["opportunity_id"] = opportunity
        if state.project_details:
            request["project_details"] = state.project_details

        job_id = _new_job_id()
        accepted = self._invoke(AGENT_SOW, state, Stage.SOW_DRAFT,
                                {"action": "generate", "job_id": job_id, "request": request})
        job = self._await_job(accepted, job_id, Stage.SOW_DRAFT)
        markdown = str(job.get("markdown") or "")
        if not markdown.strip():
            raise AgentError(AGENT_SOW, "the SOW generator returned an empty draft")
        state.sow_markdown = markdown
        run.output = {"job_id": job_id, "chars": len(markdown)}
        run.status = Status.COMPLETED

    def _await_job(self, accepted: dict[str, Any], job_id: str, stage: Stage) -> dict[str, Any]:
        """Wait out one of the SOW agent's background jobs."""
        if accepted.get("status") == "completed":      # a synchronous reply; nothing to poll
            return accepted
        if self._poller is None:
            raise AgentError(AGENT_SOW, "this deployment cannot wait for background SOW jobs "
                                        "(no job poller is configured)")
        return self._poller.wait(job_id, on_progress=lambda job: self._log(
            stage.value, f"SOW agent: {job.get('stage')} {job.get('progress', 0)}%"))

    # ---------------------------------------------------------------- stage 2: Solutions Architect review
    def _sa_review(self, state: PipelineState, run: StageRun) -> None:
        """The human checkpoint on the draft, before anything is built on it.

        This mirrors how the SAs work today: they read the draft, edit it (the "change with AI"
        path edits the SOW) and/or leave comments, then sign off. There is no agent here, so the
        stage either waits for that sign-off or, on a fully automated run, passes straight through.

        The pause is governed by the same `stop_on_blocked` switch as the review gates: with it
        off, the run is fully automated and the checkpoint is skipped. Sign-off is a per-run latch
        (`sa_signed_off`), so once the SA has approved, re-running this stage (after an AI edit,
        say) does not stop again.
        """
        comments = len(state.sa_comments)
        note = f" {comments} architect comment(s) recorded." if comments else ""
        if state.sa_signed_off or not state.stop_on_blocked:
            run.status = Status.COMPLETED
            run.detail = (("Approved by the solutions architect." if state.sa_signed_off
                           else "Automated run: no architect checkpoint.") + note).strip()
            return
        run.status = Status.BLOCKED
        run.detail = ("Waiting for the solutions architect to review the draft SOW. Edit it with "
                      "AI or add comments, then approve to continue to the Well-Architected "
                      "review." + note)

    # ---------------------------------------------------------------- stage 3: WA review
    def _wa_review(self, state: PipelineState, run: StageRun) -> None:
        """Well-Architected review of the SOW, before any diagram exists.

        This is the stage that saves the loop. A high finding blocks by default, because the
        point of reviewing here is to fix the SOW before a diagram and a price are built on it.
        """
        result = self._invoke(AGENT_SOW, state, Stage.WA_REVIEW, {
            "action": "wa_review",
            "request": {"sow_markdown": state.sow_markdown,
                        "upstream": state.upstream or None,
                        "verify": self._wa_verify,
                        "template_id": state.template_id or None},
        })
        review = result.get("review") or {}
        summary = review.get("summary") or {}
        run.output = {"review": review}

        # Record the scope here: the SOW is final as of this stage, and the next stage needs it.
        state.scope = extract_scope(state.sow_markdown,
                                    source=_scope_source(state))
        if not scope_is_usable(state.scope):
            self._log(Stage.WA_REVIEW.value,
                      "No in-scope / out-of-scope list could be read from the SOW; the design "
                      "will run unconstrained.")

        high = int(summary.get("high") or 0)
        self._log(Stage.WA_REVIEW.value,
                  f"{summary.get('total', 0)} finding(s): {high} high, "
                  f"{summary.get('medium', 0)} medium, {summary.get('low', 0)} low")
        if high and state.stop_on_blocked:
            titles = ", ".join(f.get("title", "") for f in (review.get("findings") or [])
                               if f.get("severity") == "high")[:400]
            run.status = Status.BLOCKED
            run.detail = (f"{high} high Well-Architected finding(s) against the SOW: {titles}. "
                          f"Fix the SOW and re-run, or re-run with stop_on_blocked disabled to "
                          f"continue anyway.")
            return
        run.status = Status.COMPLETED

    # ---------------------------------------------------------------- stage 4: architecture
    def _architecture(self, state: PipelineState, run: StageRun) -> None:
        """Design, usage and diagram, bound by the SOW's agreed scope."""
        payload = adapters.architecture_request(state.sow_markdown, state.scope)
        if "scope" in payload:
            self._log(Stage.ARCHITECTURE.value,
                      f"Applying the SOW scope: {len(payload['scope']['in_scope'])} in scope, "
                      f"{len(payload['scope']['out_of_scope'])} out of scope")
        result = self._invoke(AGENT_ARCHITECTURE, state, Stage.ARCHITECTURE, payload)
        run.output = {k: result[k] for k in KEEP_ARCHITECTURE if k in result}

        warnings = result.get("scope_warnings") or []
        for warning in warnings:
            self._log(Stage.ARCHITECTURE.value, warning)
        # Out-of-scope components are a scope breach, not a crash: carry them into the combined
        # review rather than stopping, because a human has to decide whether scope changed.
        if warnings:
            run.detail = f"{len(warnings)} component(s) may fall outside the agreed scope."
        run.status = Status.COMPLETED

    # ---------------------------------------------------------------- stage 5: cost
    def _cost(self, state: PipelineState, run: StageRun) -> None:
        """Price what the architecture stage produced."""
        architecture = state.run(Stage.ARCHITECTURE).output
        if not architecture:
            run.status, run.detail = Status.SKIPPED, "No architecture output to price."
            return
        requirements = adapters.cost_requirements(architecture)     # ValueError -> stage failure
        run.output, state.sow_markdown = self._price(state, requirements)
        run.status = Status.COMPLETED

    def _price(self, state: PipelineState, requirements: str,
               label: str = "") -> tuple[dict[str, Any], str]:
        """Ask the cost agent for an estimate. Returns (stage output, SOW with the new link in it).

        Mutates nothing, so a revision that fails here leaves the run exactly as it was.
        """
        result = self._invoke(AGENT_COST, state, Stage.COST,
                              {"action": "estimate", "requirements": requirements}, label=label)
        output = {k: result[k] for k in KEEP_COST if k in result}
        url = result.get("calculator_url")
        if not url:
            # The SOW refers to this link instead of restating figures, so a missing link is a
            # real problem: the Pricing section would have nothing to point at.
            raise AgentError(AGENT_COST, "the cost agent returned no calculator_url, so the SOW "
                                         "would have no cost estimate to refer to")
        self._log(Stage.COST.value, f"Estimate ready: {result.get('services_count', 0)} services, {url}")
        # The SOW was drafted before this link existed. Write it in now, so the Pricing section refers
        # to the real estimate and the final review has something true to check.
        sow, how = adapters.apply_cost_link(state.sow_markdown, str(url))
        if how != "unchanged":
            self._log(Stage.COST.value, f"Cost estimate link written into the SOW Pricing section ({how})")
        output["sow_link"] = how
        return output, sow

    # ---------------------------------------------------------------- stage 6: final SOW review
    def _final_review(self, state: PipelineState, run: StageRun) -> None:
        """Re-check the final SOW now that the architecture and cost blocks exist.

        This is where the SOW's service-coverage rule and the cost-estimate-link rule finally
        have something to check: at generation time neither block existed.

        On top of the SOW agent's rule check, it runs a deterministic pass of the sa-doc-reviewer
        checklist over the final SOW (adapters.sa_review_findings): placeholders and AI slop left
        in the draft, a placeholder calculator link, the two-way architecture <-> cost audit, a
        missing Out of Scope section, missing SOW sections, a region mismatch, and services named
        in the SOW that the diagram and estimate miss (KMS, a security agent, QuickSight, ...).
        A `critical` finding stops the run the same way a failed required rule does; the rest are
        advisory notes for a person to confirm before the SOW goes out.
        """
        architecture = state.run(Stage.ARCHITECTURE).output or None
        cost = state.run(Stage.COST).output or None
        upstream = adapters.upstream_with_results(state.upstream, architecture=architecture, cost=cost)
        job_id = _new_job_id()
        accepted = self._invoke(AGENT_SOW, state, Stage.COMBINED_REVIEW, {
            "action": "check", "job_id": job_id,
            "request": {"sow_markdown": state.sow_markdown, "upstream": upstream,
                        "verify": self._combined_verify},
        })
        job = self._await_job(accepted, job_id, Stage.COMBINED_REVIEW)
        check = job.get("check") or accepted.get("check") or {}
        summary = (check or {}).get("summary") or {}
        sa_findings = adapters.sa_review_findings(state.sow_markdown, state.scope, architecture, cost)
        run.output = {"job_id": job_id, "check": check, "upstream": upstream}
        if sa_findings:
            run.output["sa_findings"] = sa_findings
        self._log(Stage.COMBINED_REVIEW.value,
                  f"{summary.get('passed', 0)}/{summary.get('total', 0)} rules passed, "
                  f"{len(check.get('issues') or [])} issue(s)")
        for finding in sa_findings:
            self._log(Stage.COMBINED_REVIEW.value,
                      f"SA review [{finding['severity']}] {finding['label']}: {finding['detail']}")

        failed_required = [r for r in (check.get("rules") or [])
                           if r.get("status") == "fail" and r.get("severity") == "required"]
        criticals = [f for f in sa_findings if f["severity"] == "critical"]
        blockers: list[str] = []
        if failed_required:
            names = ", ".join(str(r.get("name") or r.get("id")) for r in failed_required)
            blockers.append(f"{len(failed_required)} required rule(s) failed: {names}")
        if criticals:
            labels = ", ".join(f["label"] for f in criticals)
            blockers.append(f"{len(criticals)} critical SA finding(s): {labels}")
        if blockers and state.stop_on_blocked:
            run.status = Status.BLOCKED
            run.detail = ("; ".join(blockers))[:500] + "."
            return
        run.status = Status.COMPLETED
        if sa_findings:
            counts = ", ".join(f"{n} {sev}" for sev, n in (
                (s, sum(1 for f in sa_findings if f["severity"] == s))
                for s in ("critical", "high", "medium", "low")) if n)
            run.detail = f"{len(sa_findings)} SA review finding(s) ({counts}); review the final SOW before it goes out."

    # ---------------------------------------------------------------- revisions
    def revise(self, state: PipelineState) -> Optional[dict[str, Any]]:
        """Carry out the change a person asked for (`state.revision`), then clear the request.

        The change goes to the agent that owns the thing being changed: the SOW agent's editor,
        the architecture agent's reviser, or the cost agent with the adjustment added to what it
        prices. Each one works on the current output and leaves the run untouched if it fails, so
        a bad instruction costs a retry and never a half-edited SOW or a diagram that no longer
        matches its design.

        When the change takes, every later stage is stale and goes back to pending, so the normal
        sequence rebuilds them. Returns the history entry, or None when nothing was pending.
        """
        request = state.revision
        if not request:
            return None
        target = str(request.get("target") or "")
        instruction = str(request.get("instruction") or "").strip()
        label = REVISION_LABELS.get(target, target)
        entry: dict[str, Any] = {
            "target": target, "instruction": instruction,
            "requested_at": request.get("requested_at"),
            "requested_by": request.get("requested_by"),
            "status": "failed", "summary": "", "details": [], "error": "", "seconds": 0.0,
        }
        started = time.monotonic()
        self._log("revision", f"Changing the {label}: {instruction[:200]}")
        try:
            handler = {"sow": self._revise_sow, "architecture": self._revise_architecture,
                       "cost": self._revise_cost}.get(target)
            if handler is None:
                raise ValueError(f"Unknown change target {target!r}.")
            summary, details, changed = handler(state, instruction)
            entry.update(status="applied" if changed else "no_change", summary=summary, details=details)
            if changed:
                state.invalidate_after(REVISION_TARGETS[target])
        except Exception as exc:       # noqa: BLE001 - the state is untouched, so record and move on
            entry["error"] = str(exc) if isinstance(exc, (AgentError, ValueError)) \
                else f"{type(exc).__name__}: {exc}"
            self._log("revision", f"The {label} change was not applied: {entry['error']}")
        finally:
            entry["seconds"] = round(time.monotonic() - started, 1)
            entry["finished_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            state.revision = {}
            state.revisions.append(entry)
        if entry["status"] != "failed":
            self._log("revision", f"The {label} change: {entry['summary']}")
        return entry

    def _revise_sow(self, state: PipelineState, instruction: str) -> tuple[str, list[str], bool]:
        """The SOW agent's editor proposes changes; the ones that still fit are applied."""
        original = state.sow_markdown
        if not original.strip():
            raise ValueError("This run has no SOW to change yet.")
        sections_map, outline = sow_edit.sections_map_of(sow_edit.parse(original))
        intake = (state.upstream or {}).get("intake") or {}
        job_id = _new_job_id()
        accepted = self._invoke(AGENT_SOW, state, Stage.SOW_DRAFT, {
            "action": "edit", "job_id": job_id,
            "request": {
                "customer_name": str(intake.get("customer_name") or "").strip(),
                "template_id": state.template_id,
                "instruction": instruction,
                "document_outline": outline,
                "sections_map": sections_map,
            }}, label="sow-revise")
        job = self._await_job(accepted, job_id, Stage.SOW_DRAFT)
        if job.get("proposals_dropped"):
            raise AgentError(AGENT_SOW, "the editor's proposals were too large to return; "
                                        "ask for a smaller change")
        proposals = job.get("proposals") or []
        reply = str(job.get("assistant_text") or "").strip()[:1500]
        updated, applied, skipped = sow_edit.apply_proposals(original, proposals)
        details = [*applied, *(f"Not applied. {s}" for s in skipped)][:25]
        if not applied or updated == original:
            reason = reply or ("The editor did not propose a change." if not proposals else
                               "None of the editor's changes could be applied to the current SOW.")
            return reason, details, False
        state.sow_markdown = updated
        summary = f"{len(applied)} change(s) applied to the SOW" \
                  + (f", {len(skipped)} could not be" if skipped else "") + "."
        return (f"{summary} {reply}".strip(), details, True)

    def _revise_architecture(self, state: PipelineState, instruction: str) -> tuple[str, list[str], bool]:
        """Revise the design, re-estimate its usage and redraw the diagram, as one unit.

        Nothing is written to the state until all three are in hand, so a failed redraw cannot
        leave a new design next to the old diagram.
        """
        previous = state.run(Stage.ARCHITECTURE).output
        design = adapters.design_of(previous)
        if not design:
            raise ValueError("There is no architecture design to change yet.")
        base = adapters.architecture_request(state.sow_markdown, state.scope)
        stage, label = Stage.ARCHITECTURE, "architecture-revise"
        revise_payload: dict[str, Any] = {
            "action": "revise_adr", "srs_text": base["srs_text"], "srs_filename": base["srs_filename"],
            "design": design, "instruction": instruction, "run_id": previous.get("run_id") or "",
        }
        if "scope" in base:
            revise_payload["scope"] = base["scope"]
        revised = self._invoke(AGENT_ARCHITECTURE, state, stage, revise_payload, label=label)
        new_design = revised.get("design")
        if not isinstance(new_design, dict) or not new_design:
            raise AgentError(AGENT_ARCHITECTURE, "the architecture agent returned no revised design")
        self._log("revision", "Design revised; re-estimating usage")

        usage: dict[str, Any] = {}
        try:
            estimated = self._invoke(AGENT_ARCHITECTURE, state, stage, {
                "action": "estimate_usage", "srs_text": base["srs_text"],
                "srs_filename": base["srs_filename"], "design": new_design,
                "run_id": previous.get("run_id") or ""}, label=label)
            usage = {k: estimated[k] for k in ("status", "usage_json", "usage_assumptions_md") if k in estimated}
        except AgentError as exc:
            # Dropping the old usage is deliberate: it described the old design. The cost stage
            # falls back to pricing the new design's components.
            self._log("revision", f"Usage could not be re-estimated ({exc}); the cost will be "
                                  f"built from the design's components instead")

        self._log("revision", "Redrawing the diagram")
        drawn = self._invoke(AGENT_ARCHITECTURE, state, stage,
                             {"action": "generate_diagram", "design": new_design}, label=label)
        diagram = drawn.get("diagram")
        if not isinstance(diagram, dict) or not diagram.get("png_base64"):
            raise AgentError(AGENT_ARCHITECTURE, "the architecture agent returned no diagram")

        warnings = revised.get("scope_warnings") or []
        run = state.run(Stage.ARCHITECTURE)
        run.output = {
            "run_id": revised.get("run_id") or previous.get("run_id"),
            "adr": {"adr_md": revised.get("adr_md"), "spec_json": revised.get("spec_json"),
                    "design": new_design},
            "usage": usage,
            "diagram": diagram,
            "scope": revised.get("scope") or previous.get("scope"),
            "scope_warnings": warnings,
            "report_status": drawn.get("report_status"),
            "job_id": drawn.get("job_id"),
        }
        run.status, run.error = Status.COMPLETED, ""
        run.detail = f"{len(warnings)} component(s) may fall outside the agreed scope." if warnings else ""

        added, removed = adapters.component_changes(design, new_design)
        details = [*(f"Added: {name}" for name in added), *(f"Removed: {name}" for name in removed)][:25]
        summary = ("Design revised and the diagram redrawn"
                   + (f": {len(added)} component(s) added, {len(removed)} removed." if added or removed
                      else "; the set of components is unchanged."))
        return summary, details, True

    def _revise_cost(self, state: PipelineState, instruction: str) -> tuple[str, list[str], bool]:
        """Price the current architecture again with the person's adjustment added."""
        architecture = state.run(Stage.ARCHITECTURE).output
        if not architecture:
            raise ValueError("There is no architecture to price yet.")
        requirements = adapters.with_cost_adjustment(adapters.cost_requirements(architecture), instruction)
        output, sow = self._price(state, requirements, label="cost-revise")
        run = state.run(Stage.COST)
        run.output, run.status, run.error = output, Status.COMPLETED, ""
        run.detail = f"Re-estimated with: {instruction[:200]}"
        state.sow_markdown = sow
        return (f"Re-estimated with your adjustment: {output.get('services_count', 0)} services priced.",
                [f"Estimate: {output.get('calculator_url')}"], True)


def _scope_source(state: PipelineState) -> str:
    meta = (state.upstream or {}).get("meta") or {}
    opportunity = str(meta.get("opportunity_id") or "").strip()
    return f"sow:{opportunity}" if opportunity else "sow"


def _new_job_id() -> str:
    import uuid

    return uuid.uuid4().hex


def stage_order() -> list[str]:
    """The sequence, for the UI. Exposed so the UI cannot drift from the code."""
    return [s.value for s in STAGES]
