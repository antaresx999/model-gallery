--[[
  Kommunikation mit dem Model-Gallery-Server.
  Anmeldung mit API-Schlüssel (Header «Authorization: Bearer …»). Antworten im einfachen Tab-Format,
  damit kein JSON-Parser nötig ist.
]]
local LrHttp = import 'LrHttp'
local LrLogger = import 'LrLogger'

local Api = {}

-- Logfile für die Fehlersuche: macOS ~/Library/Logs/Adobe/Lightroom/LrClassicLogs/ModelGallery.log,
-- Windows %LOCALAPPDATA%\Adobe\Lightroom\Logs\LrClassicLogs\ModelGallery.log
-- Der API-Schlüssel wird nie protokolliert.
local logger = LrLogger('ModelGallery')
logger:enable('logfile')
function Api.log(...)
  local parts = {}
  for i = 1, select('#', ...) do parts[#parts + 1] = tostring((select(i, ...))) end
  logger:info(table.concat(parts, ' '))
end

local function trim(s)
  s = tostring(s or '')
  s = s:gsub('^%s+', '')
  s = s:gsub('%s+$', '')
  return s
end
Api.trim = trim

-- «galerie.example.ch/» → «https://galerie.example.ch»
function Api.normalizeUrl(url)
  url = trim(url)
  if url == '' then return '' end
  if not url:lower():match('^https?://') then
    url = 'https://' .. url
  end
  url = url:gsub('/+$', '')
  return url
end

local function headers(token)
  return {
    { field = 'Authorization', value = 'Bearer ' .. trim(token) },
    { field = 'Accept', value = 'text/plain' },
  }
end

-- Fehlermeldung aus der Antwort des Servers ({"error": "…"}) oder des Netzwerks
local function errorMessage(body, hdrs)
  local status = hdrs and hdrs.status
  if body and body ~= '' then
    local msg = body:match('"error"%s*:%s*"(.-)"%s*[,}]')
    if msg and msg ~= '' then
      return msg
    end
  end
  if hdrs and hdrs.error then
    local err = hdrs.error
    return 'Server nicht erreichbar (' .. tostring(err.name or err.errorCode or 'Netzwerkfehler') .. ')'
  end
  if status == 401 then return 'API-Schlüssel ungültig.' end
  if status == 404 then return 'Adresse nicht gefunden – stimmt die Server-Adresse?' end
  if status then return 'Serverfehler (HTTP ' .. tostring(status) .. ')' end
  return 'Keine Antwort vom Server.'
end

local function okResponse(body, hdrs)
  return hdrs and tonumber(hdrs.status) == 200 and body ~= nil
end

-- Verbindung prüfen. Liefert true, { name = …, site = … } oder false, Fehlermeldung
function Api.info(url, token)
  local body, hdrs = LrHttp.get(url .. '/api/plugin/info?format=tsv', headers(token), 30)
  Api.log('info', url, 'HTTP', hdrs and hdrs.status)
  if not okResponse(body, hdrs) then
    Api.log('info fehlgeschlagen:', errorMessage(body, hdrs))
    return false, errorMessage(body, hdrs)
  end
  local ok, version, name, site = body:match('^(OK)\t(%d+)\t([^\t]*)\t([^\r\n]*)')
  if not ok then
    return false, 'Unerwartete Antwort – ist das wirklich ein Model-Gallery-Server?'
  end
  return true, { version = tonumber(version), name = name, site = site }
end

-- Alben, in die der Zugang hochladen darf: Liste von { id, path, title, images }
function Api.albums(url, token)
  local body, hdrs = LrHttp.get(url .. '/api/plugin/alben?format=tsv', headers(token), 30)
  if not okResponse(body, hdrs) then
    return false, errorMessage(body, hdrs)
  end
  local list = {}
  for line in body:gmatch('[^\r\n]+') do
    local id, path, title, count = line:match('^(%d+)\t([^\t]*)\t([^\t]*)\t(%d+)')
    if id then
      list[#list + 1] = { id = tonumber(id), path = path, title = title, images = tonumber(count) }
    end
  end
  Api.log('alben:', #list)
  return true, list
end

-- Datei hochladen. fileName bestimmt, welchem Bild eine RAW-Datei zugeordnet wird (gleicher Name ohne Endung).
function Api.upload(url, token, albumId, filePath, fileName, contentType, replace)
  local target = string.format('%s/api/plugin/alben/%d/bilder?format=tsv&ersetzen=%d',
    url, tonumber(albumId) or 0, replace and 1 or 0)
  local content = {
    { name = 'file', fileName = fileName, filePath = filePath, contentType = contentType },
  }
  local body, hdrs = LrHttp.postMultipart(target, content, headers(token), 900)
  Api.log('upload', fileName, '-> Album', albumId, 'HTTP', hdrs and hdrs.status)
  if not okResponse(body, hdrs) or not body:match('^OK\t') then
    Api.log('upload fehlgeschlagen:', fileName, errorMessage(body, hdrs))
    return false, errorMessage(body, hdrs)
  end
  local id, kind, state = body:match('^OK\t(%d+)\t(%a+)\t(%a+)')
  return true, { id = tonumber(id), kind = kind, replaced = (state == 'ersetzt') }
end

return Api
