"""Presentation shared by the outage review and its saved Word draft."""


def no_root_reason(read):
    if not read:
        return ""
    count = read["runs"]
    scope = " for %s" % read["object"] if read.get("object") else ""
    watch = ("%d watch run%s read, none recorded a lost datastore or a down host%s" %
             (count, "" if count == 1 else "s", scope) if count else
             "no watch runs were read for the window (is the AAP credential on the template?)")
    if read.get("events_not_read"):
        return "%s. vCenter events not read." % watch
    events = ("the vCenter events read contain no loss event%s (event read incomplete)" % scope if read.get("partial")
              else "vCenter has no loss event in the window%s" % scope)
    if read.get("unnamed_loss"):
        events = "vCenter recorded a storage loss in the window without a datastore name"
    return "%s. %s." % (watch, events)
