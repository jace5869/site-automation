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
