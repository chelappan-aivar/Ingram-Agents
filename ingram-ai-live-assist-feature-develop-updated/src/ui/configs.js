// Checklist config profiles: list, view, clone, edit (form or JSON), save versions, archive.
// Also fills the config picker on the start form. Uses $, el, api and log from app.js.

const FIELD_COLUMNS = [
  ["id", "Id", "text"], ["label", "Label", "text"], ["description", "Description", "text"],
  ["category", "Category", "text"], ["required", "Required", "checkbox"], ["memory_scope", "Remembered per", "scope"],
];
let editing = null; // { entry, draft, readOnly, isNew }

// --- start form picker ---------------------------------------------------------------

async function loadConfigPicker(selected) {
  const { data } = await api("GET", "/configs");
  const select = $("call-config");
  select.replaceChildren(...data.configs.map((c) => {
    const o = el("option", c.name + (c.builtin ? " (built-in)" : ` (v${c.current_version})`));
    o.value = c.config_id;
    o.selected = c.config_id === selected;
    return o;
  }));
  await configPicked();
}

async function configPicked() {
  const id = $("call-config").value;
  const opts = (await api("GET", `/options?config_id=${encodeURIComponent(id)}`)).data;
  $("call-config-topics").textContent = `${opts.topics.length} general topics; types: ` +
    (opts.opportunity_types.join(", ") || "none");
  // New opportunities can preset only types this config has.
  const types = $("new-opportunity-type");
  types.replaceChildren(el("option", "Decide during the call"), ...opts.opportunity_types.map((t) => {
    const o = el("option", t); o.value = t; return o;
  }));
  types.options[0].value = "";
}

// --- list ------------------------------------------------------------------------------

async function loadConfigList() {
  const archived = $("config-show-archived").checked ? "?include_archived=true" : "";
  const { data } = await api("GET", `/configs${archived}`);
  $("config-list").replaceChildren(...data.configs.map((c) => {
    const li = el("li");
    const b = el("button", c.name, "link");
    b.type = "button";
    b.onclick = () => openConfig(c.config_id).catch((e) => log(e.message));
    li.append(b, el("span", c.builtin ? "  built-in, read-only" : `  v${c.current_version}` +
      (c.archived ? ", archived" : ""), "muted"));
    return li;
  }));
}

async function openConfig(id, version) {
  const query = version !== undefined ? `?version=${version}` : "";
  const entry = (await api("GET", `/configs/${id}${query}`)).data;
  const versions = (await api("GET", `/configs/${id}/versions`)).data.versions;
  const latest = entry.version === entry.current_version;
  editing = { entry, draft: structuredClone(entry.body), readOnly: entry.builtin || entry.archived, isNew: false };
  $("config-version").replaceChildren(...versions.map((v) => {
    const o = el("option", v.version === 0 ? "built-in" : `v${v.version}${v.created_at ? " · " + v.created_at : ""}`);
    o.value = v.version; o.selected = v.version === entry.version; return o;
  }));
  $("config-meta").textContent = entry.builtin ? "Built-in profile. Clone it to make changes."
    : `Version ${entry.version} of ${entry.current_version}` + (latest ? "" : " (older version: saving makes it the newest)") +
      (entry.archived ? " · archived" : "");
  showEditor();
}

function newClone() {
  const source = editing.entry;
  editing = {
    entry: { config_id: null, current_version: 0, builtin: false },
    draft: { ...structuredClone(editing.draft), name: `${editing.draft.name} (copy)` },
    readOnly: false, isNew: true, from: source.config_id,
  };
  $("config-version").replaceChildren();
  $("config-meta").textContent = "New profile, not saved yet.";
  showEditor();
}

function showEditor() {
  $("config-editor").hidden = false;
  $("config-title").textContent = editing.draft.name;
  $("config-save").hidden = editing.readOnly;
  $("config-archive").hidden = editing.readOnly || editing.isNew;
  $("config-errors").replaceChildren();
  renderForm();
  $("config-json").value = JSON.stringify(editing.draft, null, 2);
  $("config-json").readOnly = editing.readOnly;
}

// --- form --------------------------------------------------------------------------

function input(value, onChange, { type = "text", label, disabled }) {
  let e;
  if (type === "checkbox") {
    e = el("input"); e.type = "checkbox"; e.checked = !!value;
    e.onchange = () => onChange(e.checked);
  } else if (type === "scope") {
    e = el("select");
    for (const s of ["opportunity", "account"]) {
      const o = el("option", s === "account" ? "account (the company)" : "opportunity (the deal)");
      o.value = s; o.selected = (value || "opportunity") === s; e.append(o);
    }
    e.onchange = () => onChange(e.value);
  } else {
    e = el("input"); e.value = value ?? "";
    e.oninput = () => onChange(e.value);
  }
  e.setAttribute("aria-label", label);
  e.disabled = disabled;
  return e;
}

function fieldsTable(fields, owner) {
  const ro = editing.readOnly;
  const table = el("table", null, "fields");
  const head = el("tr");
  for (const [, title] of FIELD_COLUMNS) head.append(el("th", title));
  head.append(el("th", ro ? "" : "Order"));
  table.append(head);
  fields.forEach((f, i) => {
    const row = el("tr");
    for (const [key, title, type] of FIELD_COLUMNS) {
      const td = el("td");
      td.append(input(f[key], (v) => { f[key] = v; syncJson(); },
        { type, label: `${owner} field ${i + 1} ${title}`, disabled: ro }));
      row.append(td);
    }
    const actions = el("td");
    if (!ro) {
      const move = (delta, text) => {
        const b = el("button", text); b.type = "button";
        b.setAttribute("aria-label", `${text} ${owner} field ${i + 1}`);
        b.disabled = i + delta < 0 || i + delta >= fields.length;
        b.onclick = () => { fields.splice(i + delta, 0, fields.splice(i, 1)[0]); renderForm(); syncJson(); };
        return b;
      };
      const remove = el("button", "Remove"); remove.type = "button";
      remove.setAttribute("aria-label", `Remove ${owner} field ${i + 1}`);
      remove.onclick = () => { fields.splice(i, 1); renderForm(); syncJson(); };
      actions.append(move(-1, "Up"), move(1, "Down"), remove);
    }
    row.append(actions);
    table.append(row);
  });
  const wrap = el("div");
  wrap.append(table);
  if (!ro) {
    const add = el("button", `Add field to ${owner}`); add.type = "button";
    add.onclick = () => { fields.push({ id: "", label: "", required: false }); renderForm(); syncJson(); };
    wrap.append(add);
  }
  return wrap;
}

function renderForm() {
  const d = editing.draft;
  const ro = editing.readOnly;
  const form = $("config-form");
  form.replaceChildren();
  const labelled = (text, control) => { const l = el("label", text + " "); l.append(control); return l; };
  form.append(
    labelled("Name", input(d.name, (v) => { d.name = v; syncJson(); }, { label: "Profile name", disabled: ro })),
    labelled("Description", input(d.description, (v) => { d.description = v; syncJson(); },
      { label: "Profile description", disabled: ro })),
    el("h4", "General checklist (every call)"),
    fieldsTable(d.general.fields, "general"),
  );
  d.opportunity_types.forEach((t, i) => {
    const box = el("fieldset");
    box.append(el("legend", `Opportunity type: ${t.label || t.id || "new"}`));
    box.append(
      labelled("Id", input(t.id, (v) => { t.id = v; syncJson(); }, { label: `Type ${i + 1} id`, disabled: ro })),
      labelled("Label", input(t.label, (v) => { t.label = v; syncJson(); }, { label: `Type ${i + 1} label`, disabled: ro })),
      labelled("How to recognise it", input(t.description, (v) => { t.description = v; syncJson(); },
        { label: `Type ${i + 1} description`, disabled: ro })),
      fieldsTable(t.fields, t.id || `type ${i + 1}`),
    );
    if (!ro) {
      const remove = el("button", `Remove type ${t.id || i + 1}`); remove.type = "button";
      remove.onclick = () => { d.opportunity_types.splice(i, 1); renderForm(); syncJson(); };
      box.append(remove);
    }
    form.append(box);
  });
  if (!ro) {
    const add = el("button", "Add opportunity type"); add.type = "button";
    add.onclick = () => { d.opportunity_types.push({ id: "", label: "", description: "", fields: [] }); renderForm(); syncJson(); };
    form.append(add);
  }
}

function syncJson() {
  $("config-json").value = JSON.stringify(editing.draft, null, 2);
  $("config-title").textContent = editing.draft.name;
}

function showErrors(errors) {
  $("config-errors").replaceChildren(...errors.map((e) => el("li", `${e.path}: ${e.message}`)));
}

function selectTab(json) {
  $("config-tab-form").setAttribute("aria-selected", String(!json));
  $("config-tab-json").setAttribute("aria-selected", String(json));
  $("config-form").hidden = json;
  $("config-json-panel").hidden = !json;
}

// --- actions -----------------------------------------------------------------------

async function validateDraft() {
  const { data } = await api("POST", "/configs/validate", editing.draft);
  showErrors(data.errors);
  if (data.valid) log("config is valid");
  return data.valid;
}

async function saveDraft() {
  if (!(await validateDraft())) return;
  let entry;
  if (editing.isNew) {
    entry = (await api("POST", "/configs", editing.draft)).data;
  } else {
    // expected_version: the version this edit started from; the API refuses if someone saved since.
    entry = (await api("PUT", `/configs/${editing.entry.config_id}`,
      { ...editing.draft, expected_version: editing.entry.current_version })).data;
  }
  log(`saved ${entry.config_id} v${entry.version}`);
  await Promise.all([loadConfigList(), loadConfigPicker($("call-config").value)]);
  await openConfig(entry.config_id);
}

function initConfigs() {
  const guard = (fn) => () => fn().catch((e) => log(e.message));
  const screen = (configs) => {
    $("configs-screen").hidden = !configs;
    $("calls-screen").hidden = configs;
    $("show-configs").setAttribute("aria-pressed", String(configs));
    $("show-calls").setAttribute("aria-pressed", String(!configs));
  };
  $("show-configs").onclick = guard(async () => { screen(true); await loadConfigList(); });
  $("show-calls").onclick = guard(async () => { screen(false); await loadConfigPicker($("call-config").value); });
  $("config-show-archived").onchange = guard(loadConfigList);
  $("call-config").onchange = guard(configPicked);
  $("config-version").onchange = guard(() => openConfig(editing.entry.config_id, Number($("config-version").value)));
  $("config-clone").onclick = () => newClone();
  $("config-validate").onclick = guard(validateDraft);
  $("config-save").onclick = guard(saveDraft);
  $("config-archive").onclick = guard(async () => {
    if (!confirm("Archive this profile? It can't start new calls; past calls keep their copy.")) return;
    await api("DELETE", `/configs/${editing.entry.config_id}`);
    $("config-editor").hidden = true;
    await Promise.all([loadConfigList(), loadConfigPicker()]);
  });
  $("config-tab-form").onclick = () => selectTab(false);
  $("config-tab-json").onclick = () => selectTab(true);
  $("config-json-apply").onclick = () => {
    try {
      editing.draft = JSON.parse($("config-json").value);
      renderForm();
      showErrors([]);
      selectTab(false);
    } catch (e) {
      showErrors([{ path: "JSON", message: e.message }]);
    }
  };
  return loadConfigPicker();
}
