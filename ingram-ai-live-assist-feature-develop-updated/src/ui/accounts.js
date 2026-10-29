// Accounts, opportunities and long-term memory. Uses $, el, api and log from app.js.

function selectedScope() {
  const f = $("start").elements;
  return { account_id: f.account_id.value || null, opportunity_id: f.opportunity_id.value || null };
}

function scopeQuery(scope) {
  return scope.opportunity_id ? `?opportunity_id=${encodeURIComponent(scope.opportunity_id)}` : "";
}

function fillSelect(select, items, valueKey, label, selected) {
  const none = el("option", "None");
  none.value = "";
  select.replaceChildren(none, ...items.map((item) => {
    const o = el("option", label(item));
    o.value = item[valueKey];
    o.selected = item[valueKey] === selected;
    return o;
  }));
}

async function loadAccounts(selected) {
  const { data } = await api("GET", "/accounts?limit=100");
  fillSelect($("account"), data.accounts, "account_id", (a) => a.name, selected);
  await accountChanged();
}

async function accountChanged(selectedOpportunity) {
  const { account_id } = selectedScope();
  $("opportunity").disabled = $("add-opportunity").disabled = !account_id;
  if (!account_id) {
    fillSelect($("opportunity"), [], "opportunity_id", () => "", null);
    $("account-panel").hidden = true;
    return;
  }
  const { data } = await api("GET", `/accounts/${account_id}`);
  fillSelect($("opportunity"), data.opportunities, "opportunity_id",
    (o) => o.name + (o.opportunity_type ? ` (${o.opportunity_type})` : ""), selectedOpportunity);
  $("account-heading").textContent = `Account: ${data.name}`;
  await loadAccountPanel();
}

async function loadAccountPanel() {
  const scope = selectedScope();
  if (!scope.account_id) return;
  $("account-panel").hidden = false;
  $("forget-opportunity").disabled = !scope.opportunity_id;

  const calls = (await api("GET", `/accounts/${scope.account_id}/calls${scopeQuery(scope)}`)).data.calls;
  $("account-calls").replaceChildren(...(calls.length ? calls.map((c) => {
    const li = el("li");
    const open = el("button", `${c.created_at}  ${c.title}  [${c.status}, ${c.turn_count} turns]`, "link");
    open.type = "button";
    open.onclick = () => openCall(c.session_id);
    li.append(open);
    return li;
  }) : [el("li", "No calls yet", "muted")]));

  const memory = (await api("GET", `/accounts/${scope.account_id}/memory${scopeQuery(scope)}`)).data;
  $("account-memory-status").textContent = memory.enabled
    ? "Facts are extracted a minute or two after each call ends, and kept for 180 days."
    : "Long-term memory is not configured in this environment; only earlier call minutes are used.";
  const facts = (items) => items.length ? items.map((f) => el("li", f.text)) : [el("li", "Nothing yet", "muted")];
  $("account-facts").replaceChildren(...facts(memory.account));
  $("opportunity-facts").replaceChildren(...(scope.opportunity_id ? facts(memory.opportunity)
    : [el("li", "Choose an opportunity", "muted")]));
  $("account-previous").replaceChildren(...(memory.previous_calls.length
    ? memory.previous_calls.map((c) => el("li", `${(c.ended_at || "").slice(0, 10)} ${c.title}: ${c.summary || ""}`))
    : [el("li", "No earlier calls with minutes", "muted")]));
  if (memory.forgotten_at) {
    $("account-memory-status").textContent += ` Forgotten at ${memory.forgotten_at}; earlier calls are no longer used.`;
  }
}

async function forget(wholeAccount) {
  const scope = selectedScope();
  const what = wholeAccount ? "everything remembered about this account and all its opportunities"
    : "everything remembered about this opportunity";
  if (!confirm(`Erase ${what}? New calls will no longer use it or earlier calls' minutes. ` +
    "Past calls keep their own transcript and minutes. This can't be undone.")) return;
  const path = `/accounts/${scope.account_id}/memory` + (wholeAccount ? "" : scopeQuery(scope));
  const { data } = await api("DELETE", path);
  log(`erased ${data.deleted} remembered fact(s)`);
  await loadAccountPanel();
}

function renderPrior(prior) {
  const box = $("prior");
  const body = $("prior-body");
  body.replaceChildren();
  const sections = [["account", "About the customer"], ["opportunity", "About this opportunity"]];
  const any = prior && (prior.account?.length || prior.opportunity?.length || prior.previous_calls?.length);
  box.hidden = !any;
  if (!any) return;
  for (const [key, title] of sections) {
    if (!prior[key]?.length) continue;
    body.append(el("h4", title));
    const list = el("ul");
    list.append(...prior[key].map((t) => el("li", t)));
    body.append(list);
  }
  for (const c of prior.previous_calls || []) {
    body.append(el("h4", `Earlier call: ${c.title || "Untitled"} (${(c.ended_at || "").slice(0, 10)})`));
    if (c.summary) body.append(el("p", c.summary));
  }
}

function initAccounts(opts) {
  const fill = (id, values, selected) => $(id).replaceChildren(...values.map((v) => {
    const o = el("option", v); o.value = v; o.selected = v === selected; return o;
  }));
  fill("use-prior-context", opts.assist_config.use_prior_context, opts.assist_config.defaults.use_prior_context);
  const types = $("new-opportunity-type");  // Filled per config by configs.js.
  $("memory-status").textContent = opts.memory.enabled
    ? "Long-term memory is on." : "Long-term memory is off in this environment.";

  const guard = (fn) => () => fn().catch((e) => log(e.message));
  $("account").onchange = guard(() => accountChanged());
  $("opportunity").onchange = guard(loadAccountPanel);
  $("add-account").onclick = guard(async () => {
    const name = $("new-account").value.trim();
    if (!name) return log("type a company name first");
    const { data } = await api("POST", "/accounts", { name });
    $("new-account").value = "";
    log(`account ${data.account_id} created`);
    await loadAccounts(data.account_id);
  });
  $("add-opportunity").onclick = guard(async () => {
    const name = $("new-opportunity").value.trim();
    if (!name) return log("type an opportunity name first");
    const { account_id } = selectedScope();
    const { data } = await api("POST", `/accounts/${account_id}/opportunities`,
      { name, opportunity_type: types.value || null });
    $("new-opportunity").value = "";
    log(`opportunity ${data.opportunity_id} created`);
    await accountChanged(data.opportunity_id);
  });
  $("forget-account").onclick = guard(() => forget(true));
  $("forget-opportunity").onclick = guard(() => forget(false));
  return loadAccounts();
}
