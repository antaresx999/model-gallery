"""Zugriffsprotokoll: Aufrufe von Alben und Permalinks."""
import re, sys, io, uuid, sqlite3, os, urllib.request, urllib.parse, http.cookiejar
from PIL import Image
B=os.environ.get("B", "http://127.0.0.1:8090"); DB=os.environ["DB"]
class NoRedir(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*a,**k): return None
class C:
    def __init__(s): s.op=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),NoRedir); s.t=""
    def req(s,path,data=None,files=None):
        h={"X-CSRF-Token":s.t}; body=None
        if files:
            bnd=uuid.uuid4().hex
            body=b''.join(f'--{bnd}\r\nContent-Disposition: form-data; name="{k}"; filename="{fn}"\r\nContent-Type: image/jpeg\r\n\r\n'.encode()+c+b'\r\n' for k,(fn,c) in files.items())+f'--{bnd}--\r\n'.encode()
            h['Content-Type']='multipart/form-data; boundary='+bnd
        elif data is not None: body=urllib.parse.urlencode(data,doseq=True).encode()
        try: r=s.op.open(urllib.request.Request(B+path,data=body,headers=h)); return r.status,r.read().decode(),r.headers
        except urllib.error.HTTPError as e: return e.code,e.read().decode(),e.headers
    def tok(s,path):
        b=s.req(path)[1]; s.t=re.search(r'csrf-token" content="([^"]+)',b).group(1); return b
    def login(s,u,p):
        s.tok("/login"); st,_,_=s.req("/login",{"username":u,"password":p,"next":"/","csrf":s.t}); s.tok("/account"); return st
n=[0]
def ok(c,m):
    n[0]+=1; print(("PASS " if c else "FAIL ")+m)
    if not c: sys.exit(1)
def events(where="1=1",args=()):
    c=sqlite3.connect(DB); c.row_factory=sqlite3.Row
    r=c.execute("SELECT * FROM events WHERE action IN ('album_view','share_opened') AND "+where+" ORDER BY id",args).fetchall(); c.close(); return r
A=C(); A.login("admin","testadmin123")
s,_,h=A.req("/admin/projekte/neu",{"title":"Familie Muster","csrf":A.t}); pid=int(h["Location"].split("/")[-1])
s,_,h=A.req("/admin/alben/neu",{"location_ref":"p:%d"%pid,"title":"Portraits","published":"1","allow_download":"1","selection_enabled":"1","comments_enabled":"1","status":"auswahl","csrf":A.t}); aid=int(h["Location"].split("/")[-1].split("#")[0])
im=io.BytesIO(); Image.new("RGB",(600,400),(90,120,150)).save(im,"JPEG"); A.req(f"/admin/alben/{aid}/upload",files={"file":("a.jpg",im.getvalue())})
s,_,h=A.req("/admin/benutzer/neu",{"username":"mia","display_name":"Mia Muster","password":"miapass1234","role":"model","csrf":A.t}); mid=int(h["Location"].split("/")[-1])
A.req(f"/admin/alben/{aid}/zugriff",{"user_ids":[mid],"csrf":A.t})
# Admin-Aufrufe zählen nicht
A.req("/album/portraits"); ok(len(events())==0,"admin album view not logged")
# Kunde direkt, zweimal → ein Eintrag
M=C(); M.login("mia","miapass1234")
M.req("/album/portraits"); M.req("/album/portraits"); M.req("/album/portraits/foto/1")
ev=events("album_id=?",(aid,)); ok(len(ev)==1 and ev[0]["action"]=="album_view" and ev[0]["actor_name"]=="Mia Muster" and ev[0]["detail"]=="direkt" and ev[0]["share_id"] is None,"customer view logged once (throttle)")
# Album-Link: anonymer Besucher
A.req(f"/admin/alben/{aid}/links",{"label":"Oma","can_select":"1","allow_download":"1","csrf":A.t})
tok=re.search(r'/s/([A-Za-z0-9_-]{20,})"',A.req(f"/admin/alben/{aid}")[1]).group(1)
s,b,_=A.req(f"/admin/alben/{aid}"); ok("noch nie geöffnet" in b,"link shows never opened")
G=C(); G.req("/s/"+tok); G.req("/s/"+tok)
ev=events("share_id IS NOT NULL")
ok(len(ev)==1 and ev[0]["action"]=="share_opened" and ev[0]["album_id"]==aid and ev[0]["actor_id"] is None and "Oma" in ev[0]["detail"],"anonymous link open logged once")
# Admin öffnet Link: weder Zähler noch Eintrag
A.req("/s/"+tok); c=sqlite3.connect(DB); views=c.execute("SELECT views,last_used FROM share_links").fetchone(); c.close()
ok(views[0]==1 and views[1] and len(events("share_id IS NOT NULL"))==1,"admin link open not counted")
# Projekt-Link mit Gast-Identität
A.req(f"/admin/projekte/{pid}/links",{"label":"Familie","can_select":"1","csrf":A.t})
s,b,_=A.req(f"/admin/projekte/{pid}"); ptok=[t for t in re.findall(r'/s/([A-Za-z0-9_-]{20,})"',b) if t!=tok][0]
P=C(); P.req("/s/"+ptok)
P.t=re.search(r'csrf-token" content="([^"]+)',P.req(f"/s/{ptok}/album/portraits")[1]).group(1)
P.req(f"/s/{ptok}/gast",{"name":"Tante Eva","email":"eva@example.ch","back":f"/s/{ptok}/album/portraits","csrf":P.t})
ev=events("share_id=(SELECT id FROM share_links WHERE token=?)",(ptok,))
acts=[(e["action"],e["album_id"],e["actor_name"]) for e in ev]
ok(acts[0][0]=="share_opened" and acts[0][1] is None and any(a=="album_view" and al==aid for a,al,_ in acts),"project link: landing + album view logged: "+repr(acts))
# Album-Verwaltung: Aufrufe
s,b,_=A.req(f"/admin/alben/{aid}")
ok('id="aufrufe"' in b and "Mia Muster" in b and "direkt (angemeldet)" in b and "Unbekannter Besucher" in b and "Link «Oma»" in b and "Link «Familie»" in b,"album admin shows visitors")
ok("zuletzt geöffnet" in b,"share list shows last opened")
# Protokoll-Filter
s,b,_=A.req(f"/admin/protokoll?aktion=zugriffe&album={aid}")
tb=b.split("<tbody>")[1].split("</tbody>")[0]
ok(s==200 and "Nur Album" in b and "Album angesehen" in tb and "Permalink geöffnet" in tb and "Bild hochgeladen" not in tb,"protocol filter Zugriffe + album")
s,b,_=A.req("/admin/protokoll?aktion=zugriffe"); ok("Projekt «Familie Muster»" in b and "Unbekannter Besucher" in b,"protocol shows project link landing + anonymous")
# Kundenseite
s,b,_=A.req(f"/admin/benutzer/{mid}"); ok("Zuletzt angesehen" in b and "Portraits" in b and "direkt" in b,"customer detail shows recent views")
# Widerrufener Link bleibt im Protokoll erkennbar
sid=re.search(r'/admin/links/(\d+)/loeschen',[x for x in A.req(f"/admin/alben/{aid}")[1].split('<li class=') if tok in x][0]).group(1)
A.req(f"/admin/links/{sid}/loeschen",{"csrf":A.t}); s,b,_=A.req(f"/admin/alben/{aid}"); ok("widerrufener Link" in b,"revoked link labelled")
# Workflow-Board breit
s,b,_=A.req("/admin"); ok('class="layout-wide"' in b,"dashboard uses wide layout")
print("ALL",n[0],"PASSED")
