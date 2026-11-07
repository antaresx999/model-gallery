"""Lightroom-Plugin: Plugin-Rolle, API, RAW-Dateien und der echte Lua-Code unter Lua 5.1 mit nachgebautem Lightroom.

Braucht «lupa» (venv/bin/pip install -r requirements-dev.txt); ohne lupa wird der Test übersprungen.
"""
import io
import json
import os
import re
import sys
import urllib.request
import uuid
import zipfile

from PIL import Image

from client import B, Client, done, loc_id, ok

try:
    from lupa.lua51 import LuaRuntime
except ImportError:
    print("SKIP test_plugin: lupa fehlt (pip install -r requirements-dev.txt)")
    sys.exit(0)

FILES = os.environ["FILES"]
PLUG = os.path.abspath(os.environ["PLUG"])


def jpeg(color=(200, 80, 40)):
    b = io.BytesIO()
    Image.new("RGB", (900, 600), color).save(b, "JPEG")
    return b.getvalue()


A = Client()
ok(A.login("admin", "testadmin123") == 303, "admin login")
s, _, h = A.req("/admin/projekte/neu", {"title": "Hochzeit Meier", "csrf": A.t})
pid = loc_id(h)
s, _, h = A.req("/admin/projekte/neu", {"title": "Anderes", "csrf": A.t})
pid2 = loc_id(h)


def album(title, loc):
    s, _, h = A.req("/admin/alben/neu", {"location_ref": loc, "title": title, "published": "1",
                                         "allow_download": "1", "selection_enabled": "1", "comments_enabled": "1",
                                         "status": "auswahl", "csrf": A.t})
    return loc_id(h)


aid = album("Trauung", "p:%d" % pid)
aid2 = album("Fremd", "p:%d" % pid2)

# Plugin-Zugang
s, _, h = A.req("/admin/benutzer/neu", {"username": "lightroom", "display_name": "Lightroom iMac", "role": "plugin",
                                        "csrf": A.t})
ok(s == 303, "plugin user created")
puid = loc_id(h)
s, b, _ = A.req("/admin/benutzer/%d" % puid)
m = re.search(r'value="(mgp_[A-Za-z0-9_-]+)"', b)
ok(m is not None and "Lightroom einrichten" in b, "token shown once + setup steps")
TOKEN = m.group(1)
s, b, _ = A.req("/admin/benutzer/%d" % puid)
ok("mgp_" not in b, "token not shown again")
s, b, _ = A.req("/admin/benutzer")
ok("Lightroom-Plugin" in b and "nur Upload" in b, "users list shows plugin role")

P = Client()
P.tok("/login")
s, _, h = P.req("/login", {"username": "lightroom", "password": "!", "next": "/", "csrf": P.t})
ok(s != 303 or "login" in h.get("Location", ""), "plugin cannot web login")
auth = {"Authorization": "Bearer " + TOKEN}
s, b, _ = Client().req("/api/plugin/info", headers={"Authorization": "Bearer mgp_falsch"})
ok(s == 401 and "ungültig" in b, "wrong token 401")
s, b, _ = Client().req("/api/plugin/info", headers=auth)
ok(s == 200 and json.loads(b)["name"] == "Lightroom iMac", "info json")
for p in ["/admin", "/admin/projekte", "/album/trauung", "/"]:
    s, _, h = Client().req(p, headers=auth)
    ok(s in (303, 401, 403) or "login" in h.get("Location", ""), "token gives no web access " + p)
s, _, _ = Client().req("/admin/alben/%d/upload" % aid, files={"file": ("x.jpg", jpeg())}, headers=auth)
ok(s in (400, 401, 403), "token can't use admin upload")

# ---------------- Lua-Plugin mit nachgebautem Lightroom ----------------
lua = LuaRuntime(unpack_returned_tuples=True)


def hdrs_list(t):
    out = {}
    if t is None:
        return out
    for k in t:
        e = t[k]
        out[e["field"]] = e["value"]
    return out


def http_get(url, headers=None, timeout=None):
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, headers=hdrs_list(headers)))
        return r.read().decode(), lua.table_from({"status": r.status})
    except urllib.error.HTTPError as e:
        return e.read().decode(), lua.table_from({"status": e.code})
    except Exception as e:  # Netzwerkfehler wie in Lightroom: kein Body, error-Tabelle
        return None, lua.table_from({"error": lua.table_from({"name": str(e)})})


def http_post_multipart(url, content, headers=None, timeout=None):
    item = content[1]
    bnd = uuid.uuid4().hex
    data = open(item["filePath"], "rb").read()
    body = ('--{}\r\nContent-Disposition: form-data; name="{}"; filename="{}"\r\nContent-Type: {}\r\n\r\n'.format(
        bnd, item["name"], item["fileName"], item["contentType"])).encode() + data + "\r\n--{}--\r\n".format(bnd).encode()
    h = hdrs_list(headers)
    h["Content-Type"] = "multipart/form-data; boundary=" + bnd
    try:
        r = urllib.request.urlopen(urllib.request.Request(url, data=body, headers=h))
        return r.read().decode(), lua.table_from({"status": r.status})
    except urllib.error.HTTPError as e:
        return e.read().decode(), lua.table_from({"status": e.code})


g = lua.globals()
g.py_get = http_get
g.py_post = http_post_multipart
g.py_exists = lambda p: os.path.exists(p)
g.py_delete = lambda p: os.remove(p) if os.path.exists(p) else None
lua.execute(r'''
package.path = "%s/?.lua;" .. package.path
passwords, dialogs, logs = {}, {}, {}
local mocks = {
  LrHttp = { get = function(u,h,t) return py_get(u,h,t) end, postMultipart = function(u,c,h,t) return py_post(u,c,h,t) end },
  LrPasswords = { store = function(k,v) passwords[k]=v end, retrieve = function(k) return passwords[k] end },
  LrTasks = { startAsyncTask = function(fn) fn() end },
  LrDialogs = { message = function(t,m,k) dialogs[#dialogs+1]=m end },
  LrFileUtils = { exists = function(p) return py_exists(p) end, delete = function(p) py_delete(p) end },
  LrPathUtils = {
    removeExtension = function(p) return (p:gsub("%%.[^%%./]*$", "")) end,
    extension = function(p) return p:match("%%.([^%%./]*)$") or "" end,
  },
  LrView = { bind = function(k) return {bind=k} end, share = function(k) return {share=k} end },
  LrLogger = function(name) return { enable = function() end, info = function(self, msg) logs[#logs+1] = msg end } end,
}
function import(name) return assert(mocks[name], "unbekanntes Modul "..name) end
''' % PLUG)
Api = lua.eval('require "GalleryApi"')
provider = lua.eval('require "GalleryExportServiceProvider"')
ok(provider.processRenderedPhotos is not None and provider.sectionsForTopOfDialog is not None,
   "Lua 5.1: plugin files load")
ok(Api.normalizeUrl(" galerie.example.ch/ ") == "https://galerie.example.ch", "normalizeUrl")
r = Api.info(B, "mgp_falsch")
ok(r[0] is False and "ungültig" in r[1], "Lua info wrong token: " + str(r[1]))
r = Api.info("http://127.0.0.1:1", "x")
ok(r[0] is False and "nicht erreichbar" in r[1], "Lua info server down")
r = Api.info(B, TOKEN)
ok(r[0] is True and r[1]["name"] == "Lightroom iMac", "Lua info ok")
r = Api.albums(B, TOKEN)
titles = [r[1][i]["title"] for i in range(1, len(r[1]) + 1)]
ok(r[0] is True and "Trauung" in titles and "Fremd" in titles, "Lua albums: " + ",".join(titles))

# Eigenschaften wie in Lightroom: Beobachter feuern bei jeder Wertänderung
props = lua.eval('''function(url)
  local data = { serverUrl = url, albumId = 0, albumLabel = "", uploadRaw = true, replaceVersions = true }
  local obs = {}
  local p = {}
  function p.addObserver(self, key, fn) obs[key] = obs[key] or {}; table.insert(obs[key], fn) end
  setmetatable(p, { __index = data, __newindex = function(t, k, v)
    local old = data[k]; data[k] = v
    if old ~= v and obs[k] then for _, fn in ipairs(obs[k]) do fn(t, k, v) end end
  end })
  return p end''')(B)
passwords = g.passwords
dialog = lua.eval('function(provider, p) local f = setmetatable({}, {__index=function(_, k) '
                  'return function(_, t) return t end end}); return provider.sectionsForTopOfDialog(f, p) end')
provider.startDialog(props)  # noch kein Schlüssel gespeichert
ok(props.LR_cantExportBecause is not None and "Verbinden" in props.LR_cantExportBecause,
   "export locked before connect: " + str(props.LR_cantExportBecause))
props.apiToken = TOKEN
sections = dialog(provider, props)
sections[1][2][3]["action"]()  # «Verbinden»
ok("Verbunden" in props.status and props.albumId != 0, "dialog connect: " + props.status)
ok(props.LR_cantExportBecause is None, "export button enabled after connect")
provider.startDialog(props)  # Dialog erneut öffnen
ok(props.LR_cantExportBecause is None and props.apiToken == TOKEN, "reopened dialog: key restored, export enabled")
lg = [g.logs[i] for i in range(1, len(g.logs) + 1)]
ok(any("Schlüssel im Schlüsselbund gespeichert: ja" in x for x in lg)
   and any("Schlüssel im Schlüsselbund: ja" in x for x in lg), "keychain store/load logged")
ok("im Schlüsselbund gespeichert" in props.status, "status says key saved: " + props.status)
props.serverUrl = ""
ok(props.LR_cantExportBecause is not None and "Server-Adresse" in props.LR_cantExportBecause,
   "export locked without server url")
props.serverUrl = B
ok(passwords["ModelGallery|" + B] == TOKEN, "token stored in Lightroom keychain mock")
props.albumId = aid

# Export: 2 RAW-Fotos, ein JPEG-Original, eine virtuelle Kopie, ein RAW auf getrenntem Laufwerk
LR = os.path.join(FILES, "lr")
os.makedirs(LR, exist_ok=True)


def render(name, color):
    p = os.path.join(LR, "render_%s.jpg" % name)
    open(p, "wb").write(jpeg(color))
    return p


open(os.path.join(LR, "IMG_0001.CR3"), "wb").write(b"FAKE-CR3-" + os.urandom(2000))
open(os.path.join(LR, "IMG_0002.NEF"), "wb").write(b"FAKE-NEF-" + os.urandom(3000))
photos = [
    dict(fileName="IMG_0001.CR3", copyName="", fileFormat="RAW", path=os.path.join(LR, "IMG_0001.CR3"), render=render("1", (10, 10, 200))),
    dict(fileName="IMG_0002.NEF", copyName="", fileFormat="RAW", path=os.path.join(LR, "IMG_0002.NEF"), render=render("2", (10, 200, 10))),
    dict(fileName="IMG_0003.jpg", copyName="", fileFormat="JPG", path=os.path.join(LR, "IMG_0003.jpg"), render=render("3", (200, 200, 10))),
    dict(fileName="IMG_0001.CR3", copyName="SW Version", fileFormat="RAW", path=os.path.join(LR, "IMG_0001.CR3"), render=render("4", (50, 50, 50))),
    dict(fileName="IMG_0009.ARW", copyName="", fileFormat="RAW", path=os.path.join(LR, "offline", "IMG_0009.ARW"), render=render("5", (90, 10, 90))),
]
make_ctx = lua.eval(r'''function(props, list)
  local fails, rends = {}, {}
  for i = 1, #list do
    local d = list[i]
    local photo = {}
    function photo:getFormattedMetadata(k) if k == "fileName" then return d.fileName elseif k == "copyName" then return d.copyName end end
    function photo:getRawMetadata(k) if k == "fileFormat" then return d.fileFormat elseif k == "path" then return d.path end end
    local r = { photo = photo }
    function r:waitForRender() return true, d.render end
    function r:uploadFailed(msg) fails[#fails+1] = d.fileName .. ": " .. msg end
    rends[i] = r
  end
  local progress = { isCanceled = function() return false end, done = function() end }
  local ctx = { propertyTable = props, exportSession = { countRenditions = function() return #rends end } }
  function ctx:configureProgress(t) return progress end
  function ctx:renditions(opts) local i = 0; return function() i = i + 1; if rends[i] then return i, rends[i] end end end
  return ctx, fails end''')
c, fails = make_ctx(props, lua.table_from([lua.table_from(p) for p in photos]))
provider.processRenderedPhotos(None, c)
fl = [fails[i] for i in range(1, len(fails) + 1)]
ok(len(fl) == 1 and "IMG_0009" in fl[0] and "nicht gefunden" in fl[0], "export: only offline RAW reported: " + repr(fl))
ok(not os.path.exists(photos[0]["render"]), "rendered temp JPEG deleted")
s, b, _ = A.req("/admin/alben/%d" % aid)
ok(b.count('class="badge badge-raw"') == 3, "3 images with RAW badge (CR3, NEF, CR3 virtual copy)")
ok("IMG_0001.jpg" in b and "IMG_0001-SW-Version.jpg" in b and "IMG_0003.jpg" in b and "IMG_0009.jpg" in b,
   "JPEGs named after originals")
c, fails = make_ctx(props, lua.table_from([lua.table_from(dict(photos[0], render=render("1b", (250, 250, 250))))]))
provider.processRenderedPhotos(None, c)
s, b, _ = A.req("/admin/alben/%d" % aid)
ok(">v2<" in b and b.count('class="badge badge-raw"') == 3, "re-export creates version 2, RAW kept")
r = Api.upload(B, TOKEN, aid, os.path.join(LR, "IMG_0002.NEF"), "UNBEKANNT.NEF", "application/octet-stream", True)
ok(r[0] is False and "zuerst das JPEG" in r[1], "RAW without JPEG rejected: " + str(r[1]))
r = Api.upload(B, TOKEN, aid, os.path.join(LR, "IMG_0002.NEF"), "IMG_0002.txt", "application/octet-stream", True)
ok(r[0] is False and "nicht unterstützt" in r[1], "unknown type rejected")

# RAW-Download-Rechte
ids = [int(x) for x in re.findall(r"/media/(\d+)/thumb", b)]
rawid, body = None, b""
for i in ids:
    s, body, _ = A.req("/media/%d/raw" % i, raw=True)
    if s == 200:
        rawid = i
        break
ok(rawid is not None and body.startswith(b"FAKE-"), "admin can download RAW")
s, _, h = A.req("/admin/benutzer/neu", {"username": "kunde1", "password": "kunde12345", "role": "model", "csrf": A.t})
kuid = loc_id(h)
A.req("/admin/projekte/%d/zugriff" % pid, {"user_ids": [kuid], "csrf": A.t})
K = Client()
K.login("kunde1", "kunde12345")
s, _, _ = K.req("/media/%d/raw" % rawid)
ok(s == 403, "customer: RAW blocked by default")
s, b, _ = K.req("/album/trauung/foto/%d" % rawid)
ok("RAW herunterladen" not in b, "customer: no RAW button")
A.req("/admin/alben/%d/bearbeiten" % aid, {"location_ref": "p:%d" % pid, "title": "Trauung", "published": "1",
                                          "allow_download": "1", "allow_raw": "1", "selection_enabled": "1",
                                          "comments_enabled": "1", "status": "auswahl", "csrf": A.t})
s, body, _ = K.req("/media/%d/raw" % rawid, raw=True)
ok(s == 200 and body.startswith(b"FAKE-"), "customer: RAW allowed after album setting")
s, b, _ = K.req("/album/trauung/foto/%d" % rawid)
ok("RAW herunterladen" in b, "customer: RAW button")
s, b, _ = K.req("/album/trauung")
ok("Alle inkl. RAW (ZIP)" in b, "customer: ZIP incl RAW button")
s, body, _ = K.req("/album/trauung/download.zip?raw=1", raw=True)
names = zipfile.ZipFile(io.BytesIO(body)).namelist()
ok(len([x for x in names if x.lower().endswith(".jpg")]) == 5 and len([x for x in names if not x.lower().endswith(".jpg")]) == 3,
   "ZIP incl RAW: " + ",".join(sorted(names)))
s, body, _ = K.req("/album/trauung/download.zip", raw=True)
ok(len(zipfile.ZipFile(io.BytesIO(body)).namelist()) == 5, "ZIP without RAW")

# Projektbeschränkung, Deaktivieren, neuer Schlüssel
A.req("/admin/benutzer/%d" % puid, {"display_name": "Lightroom iMac", "active": "1", "project_ids": [pid2], "csrf": A.t})
r = Api.albums(B, TOKEN)
titles = [r[1][i]["title"] for i in range(1, len(r[1]) + 1)]
ok(titles == ["Fremd"], "plugin restricted to project: " + ",".join(titles))
r = Api.upload(B, TOKEN, aid, render("x", (1, 2, 3)), "X.jpg", "image/jpeg", True)
ok(r[0] is False and "kein Upload-Recht" in r[1], "upload outside scope rejected")
s, b, _ = A.req("/admin/benutzer/%d" % puid)
ok('name="project_ids" value="%d" checked' % pid2 in b and "Plugin" in b, "plugin detail shows scope")
A.req("/admin/benutzer/%d" % puid, {"display_name": "Lightroom iMac", "project_ids": [pid2], "csrf": A.t})
r = Api.info(B, TOKEN)
ok(r[0] is False and "deaktiviert" in r[1], "inactive plugin rejected")
A.req("/admin/benutzer/%d" % puid, {"display_name": "Lightroom iMac", "active": "1", "csrf": A.t})
A.req("/admin/benutzer/%d/schluessel" % puid, {"csrf": A.t})
s, b, _ = A.req("/admin/benutzer/%d" % puid)
NEW = re.search(r'value="(mgp_[A-Za-z0-9_-]+)"', b).group(1)
ok(Api.info(B, TOKEN)[0] is False and Api.info(B, NEW)[0] is True, "token rotation")
s, b, _ = A.req("/admin/benutzer/%d" % puid)
ok('value="plugin"' in b, "role stays plugin after save")

s, body, _ = A.req("/admin/plugin/lightroom.zip", raw=True)
zn = zipfile.ZipFile(io.BytesIO(body)).namelist()
ok(s == 200 and "ModelGallery.lrplugin/Info.lua" in zn and "ModelGallery.lrplugin/GalleryApi.lua" in zn,
   "plugin zip: " + ",".join(zn))
s, _, _ = K.req("/admin/plugin/lightroom.zip")
ok(s == 403, "customer can't download plugin")
s, b, _ = A.req("/admin/protokoll")
ok("Lightroom iMac" in b and "RAW-Datei hochgeladen" in b, "log shows plugin uploads")
lg = [g.logs[i] for i in range(1, len(g.logs) + 1)]
ok(any("Export fertig" in x for x in lg) and any("upload IMG_0001.jpg" in x for x in lg)
   and not any(TOKEN in x or NEW in x for x in lg), "plugin log written, without keys")
done()
