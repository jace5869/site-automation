// All fields remain ordinary form controls when scripting is disabled.
"use strict";
const directory = document.querySelector("#directory");
const groupOptions = directory ? Array.from(directory.options, option => ({
  name: option.value, label: option.label || option.value, count: option.dataset.count
})) : [];

function groupPicker(input, dirty) {
  const box = input.parentElement;
  const menu = document.createElement("ul");
  menu.className = "glist";
  menu.id = input.id + "-options";
  menu.hidden = true;
  menu.setAttribute("role", "listbox");
  box.after(menu);
  input.removeAttribute("list");
  input.setAttribute("role", "combobox");
  input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("aria-controls", menu.id);
  input.setAttribute("aria-expanded", "false");
  let matches = [], active = -1;

  function close() {
    active = -1;
    menu.hidden = true;
    input.removeAttribute("aria-activedescendant");
    input.setAttribute("aria-expanded", "false");
  }
  function add(value) {
    if (!value) return;
    const existing = Array.from(box.querySelectorAll("input[type=checkbox]")).find(x => x.value === value);
    if (existing) existing.checked = true;
    else {
      const chip = document.createElement("label"), check = document.createElement("input");
      chip.className = "gchip new";
      check.type = "checkbox";
      check.checked = true;
      check.name = input.name.slice(4);
      check.value = value;
      chip.append(check, document.createTextNode(value));
      input.before(chip);
    }
    input.value = "";
    close();
    dirty(true);
  }
  function highlight(index) {
    active = index;
    Array.from(menu.children).forEach((option, i) => {
      option.classList.toggle("on", i === index);
      option.setAttribute("aria-selected", String(i === index));
    });
    input.setAttribute("aria-activedescendant", menu.children[index].id);
  }
  function suggest() {
    close();
    menu.replaceChildren();
    const q = input.value.trim().toLowerCase();
    if (!q) return;
    matches = groupOptions.filter(o => o.name.toLowerCase().includes(q)).slice(0, 8);
    matches.forEach((o, i) => {
      const option = document.createElement("li"), count = document.createElement("span");
      option.id = menu.id + "-" + i;
      option.setAttribute("role", "option");
      option.title = o.name;
      count.className = "m";
      count.textContent = o.count ? o.count + " people" : "";
      option.append(document.createTextNode(o.label), count);
      option.addEventListener("pointerdown", event => event.preventDefault());
      option.addEventListener("click", () => add(o.name));
      menu.append(option);
    });
    menu.hidden = !matches.length;
    input.setAttribute("aria-expanded", String(matches.length > 0));
  }
  input.addEventListener("input", suggest);
  input.addEventListener("blur", close);
  input.form.addEventListener("reset", close);
  input.addEventListener("keydown", event => {
    if (event.key === "Escape") close();
    else if (!menu.hidden && ["ArrowDown", "ArrowUp"].includes(event.key)) {
      event.preventDefault();
      highlight(event.key === "ArrowDown" ? (active + 1) % matches.length : (active <= 0 ? matches.length - 1 : active - 1));
    } else if (event.key === "Enter" && input.value.trim()) {
      event.preventDefault();
      add(active < 0 ? input.value.trim() : matches[active].name);
    }
  });
}
for (const form of document.querySelectorAll(".arow")) {
  const save = form.querySelector(".pri"), cancel = form.querySelector("[type=reset]");
  const state = form.querySelector(".state"), original = state.textContent;
  function changed(dirty) {
    save.disabled = !dirty;
    cancel.hidden = !dirty;
    form.classList.toggle("editing", dirty);
    state.classList.toggle("dirty", dirty);
    state.textContent = dirty ? "Changes not saved" : original;
  }
  changed(false);
  form.addEventListener("input", () => changed(true));
  form.addEventListener("reset", () => {
    form.querySelectorAll(".gchip.new").forEach(chip => chip.remove());
    changed(false);
  });
  for (const box of form.querySelectorAll("input[list]")) groupPicker(box, changed);
}

const file = document.querySelector("#import-file");
if (file) {
  document.querySelector("#import-picker").hidden = false;
  file.addEventListener("change", async () => {
    if (file.files.length) document.querySelector("textarea[name=document]").value = await file.files[0].text();
  });
}

// 1.140.0: Admin Appearance - live default portal look preview picker (progressive enhancement over the POST form).
const lookApp = document.querySelector(".look-app");
if (lookApp) {
  const current = lookApp.dataset.currentLook || "";
  const portalName = lookApp.dataset.portalName || "the portal";
  const cards = Array.from(lookApp.querySelectorAll(".look-card"));
  const stage = document.querySelector("#look-stage");
  const previewName = document.querySelector("#look-preview-name");
  const message = document.querySelector("#look-message");
  const lookValue = document.querySelector("#look-value");
  const applyBtn = document.querySelector("#look-apply");
  let selected = current;

  function selectLook(key) {
    const card = cards.find(b => b.dataset.lookKey === key);
    if (!card) return;
    selected = key;
    for (const b of cards) {
      b.setAttribute("aria-pressed", String(b.dataset.lookKey === selected));
    }
    if (stage) stage.setAttribute("data-preview-skin", selected);
    if (previewName) previewName.textContent = card.dataset.lookName || selected;
    if (lookValue) lookValue.value = selected;
    if (applyBtn) applyBtn.disabled = selected === current;
    if (message) {
      message.textContent = selected === current
        ? "This is the current site default."
        : "Apply " + (card.dataset.lookName || selected) + " as the default for " + portalName + ".";
    }
  }

  for (const card of cards) {
    card.addEventListener("click", () => selectLook(card.dataset.lookKey));
  }
  if (applyBtn) applyBtn.disabled = selected === current;
}
