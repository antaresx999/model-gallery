"""Menü, Startseiten, Brotkrumen und alle Hauptseiten für Admin, Kunde und Gast."""
import io
import re

from PIL import Image

from client import Client, done, loc_id, ok


def nav(b):
    return re.search(r'<nav class="mainnav">(.*?)</nav>', b, re.S).group(1)


A = Client()
ok(A.login("admin", "testadmin123") == 303, "admin login")
s, _, h = A.req("/")
ok(s == 303 and h["Location"] == "/admin/projekte", "admin start -> projects")
s, b, _ = A.req("/admin/projekte")
nv = nav(b)
ok("Projekte" in nv and "Workflow" in nv and "Benutzer" in nv, "admin main nav")
ok("Galerien" not in nv and "Einstellungen" not in nv and "Protokoll" not in nv, "no duplicate/secondary items in main nav")
ok('href="/admin/einstellungen"' in b and 'href="/admin/protokoll"' in b and 'href="/account"' in b,
   "user menu has settings, log, account")

s, _, h = A.req("/admin/projekte/neu", {"title": "Kunde Test", "csrf": A.t})
pid = loc_id(h)
s, _, h = A.req("/admin/alben/neu", {"location_ref": "p:%d" % pid, "title": "Album Eins", "published": "1",
                                     "allow_download": "1", "selection_enabled": "1", "comments_enabled": "1",
                                     "status": "auswahl", "csrf": A.t})
aid = loc_id(h)
im = io.BytesIO()
Image.new("RGB", (800, 600), (120, 80, 40)).save(im, "JPEG")
ok(A.req("/admin/alben/%d/upload" % aid, files={"file": ("a.jpg", im.getvalue())})[0] == 200, "upload")
s, _, h = A.req("/admin/benutzer/neu", {"username": "kim", "password": "kimpass1234", "role": "model", "csrf": A.t})
uid = loc_id(h)
A.req("/admin/projekte/%d/zugriff" % pid, {"user_ids": [uid], "csrf": A.t})

for p in ["/admin", "/admin/projekte", "/admin/projekte/%d" % pid, "/admin/alben/%d" % aid,
          "/admin/alben/%d/bearbeiten" % aid, "/admin/alben/neu", "/admin/benutzer", "/admin/benutzer/%d" % uid,
          "/admin/einstellungen", "/admin/protokoll", "/admin/protokoll?aktion=zugriffe", "/account"]:
    s, b, _ = A.req(p)
    ok(s == 200, "admin page " + p)

s, b, _ = A.req("/projekt/kunde-test")
ok(s == 200 and "Kundenansicht" in b and 'href="/admin/projekte">Projekte</a>' in b, "admin customer view: bar + crumbs")
ok(re.search(r'href="/admin/projekte" class="active"', b), "projects active in customer view")
s, b, _ = A.req("/album/album-eins")
ok("Kundenansicht" in b and ">Kunde Test</a>" in b, "admin album view crumbs")
s, b, _ = A.req("/admin/projekte/%d" % pid)
ok(">Kundenansicht<" in b, "Kundenansicht button")
s, b, _ = A.req("/admin")
ok('class="layout-wide"' in b and "board-col" in b, "workflow board in wide layout")

K = Client()
K.login("kim", "kimpass1234")
s, _, h = K.req("/")
ok(s == 303 and h["Location"] == "/projekt/kunde-test", "customer redirect into single project")
s, b, _ = K.req("/projekt/kunde-test")
nv = nav(b)
ok("Meine Galerien" in nv and "Projekte" not in nv and "Workflow" not in nv, "customer nav")
ok("Kundenansicht" not in b and 'href="/admin/einstellungen"' not in b and 'href="/account"' in b, "customer user menu")
ok('<a href="/">Meine Galerien</a>' in b, "customer crumbs")
s, b, _ = K.req("/album/album-eins")
ok(s == 200 and '<a href="/">Meine Galerien</a>' in b, "customer album crumbs")
s, _, _ = K.req("/admin")
ok(s in (302, 303, 403), "customer blocked from admin")

A.req("/admin/projekte/%d/links" % pid, {"label": "L", "can_select": "1", "csrf": A.t})
tok = re.search(r'/s/([A-Za-z0-9_-]{20,})"', A.req("/admin/projekte/%d" % pid)[1]).group(1)
G = Client()
s, b, _ = G.req("/s/" + tok)
ok(s == 200 and nav(b).strip() == "" and "Kundenansicht" not in b and "usermenu" not in b, "guest: no nav, no menu")
s, b, _ = G.req("/s/%s/album/album-eins" % tok)
ok(s == 200, "guest album")
done()
