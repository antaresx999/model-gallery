"""Plugin-Zugänge in den Einstellungen: anlegen, Schlüssel einmalig anzeigen und erneuern."""
import re

from client import Client, done, ok

A = Client()
A.login("admin", "testadmin123")
b = A.tok("/admin/einstellungen")
ok('id="lightroom"' in b and "Plugin herunterladen" in b and "http://127.0.0.1:" in b, "settings has Lightroom section")

s, _, h = A.req("/admin/benutzer/neu", {"display_name": "Lightroom Laptop", "role": "plugin", "back": "einstellungen",
                                        "csrf": A.t})
ok(s == 303 and h["Location"].endswith("/admin/einstellungen#lightroom"), "create plugin from settings returns there")
s, b, _ = A.req("/admin/einstellungen")
m = re.search(r'value="(mgp_[A-Za-z0-9_-]+)"', b)
ok(m and "Lightroom Laptop" in b, "new key shown in settings")
tok = m.group(1)
auth = lambda t: {"Authorization": "Bearer " + t}  # noqa: E731
ok(A.req("/api/plugin/info", headers=auth(tok))[0] == 200, "key works")
s, b, _ = A.req("/admin/einstellungen")
ok("mgp_" not in b, "key not shown again")
s, b, _ = A.req("/admin/benutzer")
ok("plugin-lightroom-laptop" in b, "username derived")

uid = re.search(r"/admin/benutzer/(\d+)/schluessel", A.req("/admin/einstellungen")[1]).group(1)
s, _, h = A.req("/admin/benutzer/%s/schluessel" % uid, {"back": "einstellungen", "csrf": A.t})
ok(h["Location"].endswith("#lightroom"), "rotate from settings returns there")
s, b, _ = A.req("/admin/einstellungen")
new = re.search(r'value="(mgp_[A-Za-z0-9_-]+)"', b).group(1)
ok(new != tok and A.req("/api/plugin/info", headers=auth(tok))[0] == 401
   and A.req("/api/plugin/info", headers=auth(new))[0] == 200, "rotation works")
A.req("/admin/benutzer/neu", {"display_name": "Lightroom Laptop", "role": "plugin", "back": "einstellungen", "csrf": A.t})
s, b, _ = A.req("/admin/benutzer")
ok("plugin-lightroom-laptop-2" in b, "duplicate name gets suffix")
s, _, h = A.req("/admin/benutzer/neu", {"display_name": "X", "role": "model", "csrf": A.t})
ok(h["Location"].endswith("/admin/benutzer"), "customer still needs username")
done()
