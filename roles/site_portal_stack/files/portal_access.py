"""Versioned per-template additions to the portal's configured access lists."""
import hashlib
import json
import os
import re
import stat
import tempfile
import threading
import time

MAX_BYTES = 512 * 1024
GROUP_FIELDS = ("run_groups", "approve_groups")
FIELDS = {*GROUP_FIELDS, "template", "on_portal", "confirm_again"}


def empty():
    return {"schema_version": 1, "portal_template_access": {}}


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def unique(pairs):
    result = {}
    for k, v in pairs:
        if not isinstance(k, str) or k in result:
            raise ValueError("Duplicate or invalid key.")
        result[k] = v
    return result


def label(value):
    return isinstance(value, str) and 0 < len(value) <= 512 and value == value.strip() and value.isprintable()


# The file also keeps the AAP controller its grants were saved for and a save counter (a revision never returns to an
# earlier value); a backup (export / import) carries neither - the portal's own save sets them. 1.134.0: ONE controller -
# grants saved for another one are dropped when the portal reads the file (audited), never carried over
OPTIONAL = {"controller", "saves"}


def controller_id(url):
    """the AAP controller as compared: the address as the AAP client uses it (https:// added when the setting has no scheme),
    scheme and host lower case, the default port dropped, the path kept (two controllers behind one host differ by it),
    no trailing slash. 1.133.0 (Codex r2 #5): never "" for a usable address."""
    import urllib.parse
    t = str(url or "").strip()
    if not t:
        return ""
    if "://" not in t:                                   # Neon r2 #9: http-aap.example is a host name, not a scheme
        t = "https://" + t
    try:
        u = urllib.parse.urlsplit(t)
        host = (u.hostname or "").lower()
        if not host:
            return ""
        port = u.port
    except ValueError:
        return ""
    scheme = u.scheme.lower()
    if ":" in host:
        host = "[%s]" % host                                 # director B: an IPv6 address keeps its brackets ([::1]:8443 is not ::1:8443)
    netloc = host if port in (None, {"https": 443, "http": 80}.get(scheme)) else "%s:%d" % (host, port)
    return "%s://%s%s" % (scheme, netloc, u.path.rstrip("/"))


def validate(doc, catalog=None):
    if (not isinstance(doc, dict) or not set(empty()) <= set(doc) <= set(empty()) | OPTIONAL or type(doc["schema_version"]) is not int
            or doc["schema_version"] != 1 or not isinstance(doc["portal_template_access"], dict)):
        raise ValueError("Expected schema_version 1 and a portal_template_access mapping.")
    # Codex 1340 #8: a controller stamp is "" or an address in its normalized form - anything else is a damaged file
    if "controller" in doc and not (isinstance(doc["controller"], str) and len(doc["controller"]) <= 512
                                    and (doc["controller"] == "" or controller_id(doc["controller"]) == doc["controller"])):
        raise ValueError("Invalid controller.")
    if "saves" in doc and not (type(doc["saves"]) is int and 0 <= doc["saves"] < 2 ** 53):
        raise ValueError("Invalid save counter.")
    for key, row in doc["portal_template_access"].items():
        if not isinstance(key, str) or not re.fullmatch(r"(jt|wf)_[1-9][0-9]{0,15}", key):
            raise ValueError("Invalid template ID.")
        if not isinstance(row, dict) or set(row) not in (FIELDS, FIELDS | {"controller"}) or not label(row["template"]):
            raise ValueError("Invalid template entry for " + key)
        if "controller" in row and not (isinstance(row["controller"], str) and len(row["controller"]) <= 512):
            raise ValueError("Invalid controller for " + key)
        for field in GROUP_FIELDS:
            groups = row[field]
            if not isinstance(groups, list) or len(groups) > 64 or not all(label(g) for g in groups):
                raise ValueError("Expected a list of AD group names for " + field)
            if len({g.casefold() for g in groups}) != len(groups):
                raise ValueError("Duplicate group in " + field)
        if any(type(row[field]) is not bool for field in ("on_portal", "confirm_again")):
            raise ValueError("on_portal and confirm_again must be booleans.")
        if catalog is not None and (key not in catalog or row["template"] != catalog[key]["name"]):
            raise ValueError("Unknown or renamed AAP template " + key)
        if row["approve_groups"] and (key.startswith("jt_") or catalog is not None and not catalog[key]["approval"]):
            raise ValueError("Approval groups require a workflow with an approval node.")
    if len(canonical(doc)) > MAX_BYTES:
        raise ValueError("Access settings exceed the size limit.")
    return doc


def yaml_text(doc):
    """Block mappings with JSON-quoted values also round-trip on a stdlib-only image."""
    entries = doc["portal_template_access"]
    lines = ["schema_version: 1", "portal_template_access:" + ("" if entries else " {}")]
    for key, row in sorted(entries.items()):
        lines.append("  %s:" % key)
        for field in ("template", *GROUP_FIELDS, "on_portal", "confirm_again"):
            lines.append("    %s: %s" % (field, json.dumps(row[field], ensure_ascii=False)))
    return "\n".join(lines) + "\n"


def backup(doc):
    """the part of the document a backup carries (no controller, no save counter)"""
    return {"schema_version": 1, "portal_template_access": dict(
        (k, dict((f, v) for f, v in r.items() if f != "controller")) for k, r in doc["portal_template_access"].items())}


def portable_import(raw):
    """Read the portal's exported YAML or JSON when PyYAML is absent."""
    if raw.lstrip().startswith("{"):
        return json.loads(raw, object_pairs_hook=unique)
    doc, entries, row = {}, None, None
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.fullmatch(r"( *)([a-z_0-9]+): *(.*)", line)
        if not match:
            raise ValueError("Use the exported YAML layout on this portal image.")
        indent, key, value = match.groups()
        if not indent:
            parent, entries, row = doc, None, None
            if not value and key == "portal_template_access":
                parsed = entries = {}
            else:
                parsed = json.loads(value, object_pairs_hook=unique)
        elif indent == "  " and entries is not None and not value:
            parent, row = entries, {}
            parsed = row
        elif indent == "    " and row is not None:
            parent = row
            parsed = json.loads(value, object_pairs_hook=unique)
        else:
            raise ValueError("Invalid indentation in access settings.")
        if key in parent:
            raise ValueError("Duplicate key in access settings.")
        parent[key] = parsed
    return doc


def parse_import(raw, catalog):
    if len(raw.encode("utf-8", "surrogatepass")) > MAX_BYTES * 2:
        raise ValueError("Import is too large.")
    if raw.lstrip().startswith("{"):
        # JSON (the reviewed document of an Apply): read as JSON on every image - YAML would read a \\uXXXX pair as two
        # surrogates (Codex #15)
        try:
            return validate(backup(validate(json.loads(raw, object_pairs_hook=unique))), catalog)
        except RecursionError as exc:
            raise ValueError("Import is nested too deeply.") from exc
    try:
        import yaml
    except ImportError:
        try:
            return validate(backup(validate(portable_import(raw))), catalog)        # Neon r3 #5: every branch the same document
        except RecursionError as exc:
            raise ValueError("Import is nested too deeply.") from exc

    class Loader(yaml.SafeLoader):
        def construct_mapping(self, node, deep=False):
            return unique(self.construct_pairs(node, deep=deep))

    try:
        if any(isinstance(t, (yaml.AliasToken, yaml.AnchorToken, yaml.TagToken)) for t in yaml.scan(raw)):
            raise ValueError("Use plain YAML without tags, anchors or aliases.")
        return validate(backup(validate(yaml.load(raw, Loader=Loader))), catalog)
    except (yaml.YAMLError, RecursionError) as exc:
        raise ValueError("Invalid YAML document.") from exc


def changes(old, new):
    before, after = old["portal_template_access"], new["portal_template_access"]
    return {k: {"before": before.get(k), "after": after.get(k)} for k in sorted(before.keys() | after.keys())
            if before.get(k) != after.get(k)}


class Store:
    """One portal process owns the volume. Snapshots are replaced, never mutated."""
    def __init__(self, folder, log, groups_changed, controller=""):
        self.controller = controller_id(controller)
        self.path = os.path.join(folder, "access.json")
        self.lock = threading.RLock()
        self.log = log
        self.groups_changed = groups_changed
        self.stamp = object()
        self.doc = empty()
        self.error = ""
        self.good = None                    # the last document read whole (this controller's): what a damaged file still restricts
        self.dropped = None                 # 1.134.0: {"controller": was, "count": n, "at": ...} when grants of another controller were dropped

    def read(self, for_write=False):
        with self.lock:
            stamp, audit, missing = object(), None, False
            try:
                st = os.lstat(self.path)
                if not stat.S_ISREG(st.st_mode):
                    raise ValueError("expected a regular access file")
                stamp = (st.st_ino, st.st_ctime_ns, st.st_size)
                if stamp == self.stamp:
                    return self.doc, self.error
                with open(self.path, "rb") as f:
                    raw = f.read(MAX_BYTES * 5 + 1)
                if len(raw) > MAX_BYTES * 5:
                    raise ValueError("access file too large")
                doc = json.loads(raw, object_pairs_hook=unique)
                audit = doc.pop("last_change", None) if isinstance(doc, dict) else None
                validate(doc)
                if audit is not None:
                    if (not isinstance(audit, dict) or audit.get("event") != "access_changed"
                            or not all(label(audit.get(k)) for k in ("id", "user", "at")) or not isinstance(audit.get("changes"), dict)):
                        raise ValueError("invalid access audit")
                    for state in ("before", "after"):
                        entries = {}
                        for key, change in audit["changes"].items():
                            if not isinstance(change, dict) or set(change) != {"before", "after"}:
                                raise ValueError("invalid access change")
                            if change[state] is not None:
                                entries[key] = change[state]
                        validate(dict(empty(), portal_template_access=entries))
                    # Codex #13: the last change must be what the file holds - a row that differs from the change's "after"
                    # is a damaged file: the defaults only, said on the page
                    live = doc["portal_template_access"]
                    for key, change in audit["changes"].items():
                        if live.get(key) != change["after"]:
                            raise ValueError("access.json does not match its last recorded change (%s)" % key)
                error = ""
            except FileNotFoundError:
                stamp, doc, error = None, empty(), ""
                missing = True
            except (OSError, ValueError, RecursionError):
                doc = empty()
                # Codex r2 #13: the banner says exactly what holds meanwhile
                error = ("Cannot read access.json. No saved template group counts: only the lists in all.yml (run, approve) and an "
                         "action's own who list decide, and the open run default does not apply.")
            if not error and audit is not None:
                # Finish the audit after a crash between the rename and the log append.
                try:
                    recent = self.log.read("access_changed", limit=1)
                    if not recent or recent[-1].get("id") != audit["id"]:
                        self.log.add(audit)
                except OSError:
                    if for_write:
                        raise  # Preserve the pending event until the journal can be written.
                    stamp = object()  # Retry on the next read.
            if (not error and self.controller and doc["portal_template_access"] and doc.get("controller") != self.controller
                    and not for_write and not getattr(self, "_dropping", False)):
                return self._drop(doc)
            if stamp != self.stamp:
                self.doc, self.error, self.stamp = doc, error, stamp
                # Codex 1340 #6: a file gone for a moment (a restore) never clears what the last good one restricted
                if not error and doc.get("controller", self.controller) == self.controller and not (missing and self.good is not None):
                    self.good = doc
                self.groups_changed(doc["portal_template_access"])
            return self.doc, self.error

    def _drop(self, old):
        """1.134.0: the portal reads another AAP controller than the grants were saved for - template ids and names there
        mean other templates, so every saved grant is dropped: an empty file for this controller, the old rows in the audit
        (Activity shows them), said on the Admin page"""
        self._dropping = True
        try:
            at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            rows = old["portal_template_access"]
            audit = {"event": "access_changed", "id": os.urandom(16).hex(), "user": "the portal", "at": at,
                     "changes": dict((k, {"before": r, "after": None}) for k, r in sorted(rows.items())),
                     "controller": {"before": old.get("controller") or "", "after": self.controller}, "dropped": True}
            new = {"schema_version": 1, "portal_template_access": {}, "controller": self.controller,
                   "saves": int(old.get("saves") or 0) + 1}
            self.dropped = {"controller": old.get("controller") or "", "count": len(rows), "at": at}
            try:
                self._write(new, audit)
            except OSError:
                # not written (a full or read-only volume): none of them applies meanwhile; tried again at the next read
                self.doc, self.error, self.stamp = dict(empty(), controller=self.controller), "", object()
                self.good = self.doc
                self.groups_changed({})
        finally:
            self._dropping = False
        return self.doc, self.error

    def save(self, new, revision, cn, at, replace=False):
        with self.lock:
            old, error = self.read(for_write=True)
            if revision != self.revision(old, error):
                raise ValueError("Access changed since this page was opened. Reload and review the changes.")
            validate(new)
            if old["portal_template_access"] and old.get("controller") != self.controller and not error:
                raise ValueError("access.json was saved for another AAP controller. Reload the page.")
            rows = dict((k, dict((f, v) for f, v in r.items() if f != "controller")) for k, r in new["portal_template_access"].items())
            new = {"schema_version": 1, "portal_template_access": rows, "controller": self.controller, "saves": int(old.get("saves") or 0) + 1}
            validate(new)
            diff = changes(old, new)
            if not diff and not error:
                return
            audit = {"event": "access_changed", "id": os.urandom(16).hex(), "user": cn, "at": at, "changes": diff}
            if error:
                audit["recovered"] = True
            self._write(new, audit)

    def _write(self, new, audit):
        """the file replaced durably (temp, fsync, rename, fsync of the folder), then the audit line"""
        folder = os.path.dirname(self.path)
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".access-")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(dict(new, last_change=audit), f, separators=(",", ":"))
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)
            directory = os.open(folder, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
            self.log.add(audit)
            self.read()
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    @staticmethod
    def revision(doc, error):
        return digest([doc, error])
