"""The air-gap image preflight's words (1.70.3): what the stacks' roles say when an image is not on the host. The roles ask
`podman image exists` for each image before anything is written; a missing one stops the job there, with the steps to bring
it in (the ones tools/report-db-setup.sh prints) and the setting that names it. Nothing is pulled unless the stack's
pull setting is true."""
from __future__ import absolute_import, division, print_function
__metaclass__ = type


def _file_of(image):
    """A tar name for an image: the last part of its name, the tag after a dash."""
    last = str(image).rsplit("/", 1)[-1]
    return last.replace(":", "-").replace("@", "-") + ".tar"


def airgap_pull_failures(results):
    """The images whose pull failed, from the results of the pull task's loop (each: the image's check result as its item):
    [{"var", "image", "error": the first line of podman's own words}]. A pull that was skipped (no rc) is not one."""
    out = []
    for r in results or []:
        if "rc" not in r or r.get("rc") == 0:
            continue
        it = (r.get("item") or {}).get("item") or {}
        err = (str(r.get("stderr") or r.get("msg") or "").strip().split("\n") or [""])[0]
        out.append({"var": it.get("var", "?"), "image": it.get("image", "?"), "error": err or "no reason given"})
    return out


def airgap_images_text(missing, scope="system", pull_var="", pulled=False, doc="docs/ENCLAVES.md"):
    """The job's message for the images that are not on this host.
    missing: [{"var": the setting that names the image, "image": the name, "error": podman pull's first line (when pulled)}];
    scope: system (root) or user; pull_var: the setting that allows a pull; pulled: the pull setting is true and the pull
    failed (the message says so, per image, and does not tell the reader to set what is set); doc: the page with the steps."""
    items = [m for m in (missing or []) if m and m.get("image")]
    who = "root" if scope != "user" else "the account these units run as (a rootless stack)"
    n = len(items)
    out = ["STOPPED: %s not on this host%s. Nothing was changed." % (
        "the image %s is" % items[0]["image"] if n == 1 else "%d images are" % n,
        " and could not be pulled" if pulled else "")]
    if pulled:
        out.append("%s is true, so the job tried to pull; the units themselves never pull (Pull is never)." % pull_var)
    else:
        out.append("The units never pull (Pull is never), so an air-gapped host needs each image loaded first.")
    out.append("")
    for m in items:
        out.append("  %s   (setting: %s)" % (m["image"], m.get("var", "?")))
        if pulled:
            out.append("    podman pull %s failed: %s" % (m["image"], m.get("error") or "no reason given"))
    out.append("")
    out.append("On a connected machine, for each image:")
    for m in items:
        out.append("    podman pull %s" % m["image"])
        out.append("    podman save -o %s %s" % (_file_of(m["image"]), m["image"]))
    out.append("Copy the files to this host, then as %s:" % who)
    for m in items:
        out.append("    podman load -i %s" % _file_of(m["image"]))
    out.append("The loaded name must be the one in the setting. When it is another name (your registry's copy),")
    out.append("tag it, or set the setting to the name you loaded:")
    for m in items:
        out.append("    podman tag <the name podman load printed> %s" % m["image"])
    out.append("Then run the job again. See %s, \"Images for an air-gapped enclave\"." % doc)
    if pull_var and not pulled:
        out.append("A host that reaches a registry with these images: set %s: true (it pulls what is missing)." % pull_var)
    return "\n".join(out)


class FilterModule(object):
    def filters(self):
        return {"airgap_images_text": airgap_images_text, "airgap_pull_failures": airgap_pull_failures}
