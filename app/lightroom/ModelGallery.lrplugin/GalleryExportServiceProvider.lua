--[[
  Export-Dienst «Model Gallery»: Fotos als JPEG in ein bestehendes Album hochladen, optional mit Original-RAW.

  Ablauf pro Foto:
    1. Lightroom rendert das JPEG mit den Einstellungen aus dem Export-Dialog.
    2. Das JPEG wird unter dem Dateinamen des Originals hochgeladen (z. B. IMG_1234.jpg).
       Gibt es im Album schon ein Bild mit diesem Namen, wird es als neue Version ersetzt.
    3. Optional wird die Original-RAW-Datei (z. B. IMG_1234.CR3) hochgeladen; der Server hängt sie
       dem gleichnamigen Bild an.
]]
local LrDialogs = import 'LrDialogs'
local LrFileUtils = import 'LrFileUtils'
local LrPasswords = import 'LrPasswords'
local LrPathUtils = import 'LrPathUtils'
local LrTasks = import 'LrTasks'
local LrView = import 'LrView'

local Api = require 'GalleryApi'

local provider = {}

provider.hideSections = { 'exportLocation', 'fileNaming', 'video' }
provider.allowFileFormats = { 'JPEG' }
provider.canExportVideo = false
provider.hidePrintResolution = true

provider.exportPresetFields = {
  { key = 'serverUrl', default = '' },
  { key = 'albumId', default = 0 },
  { key = 'albumLabel', default = '' },
  { key = 'uploadRaw', default = true },
  { key = 'replaceVersions', default = true },
}

-- Der API-Schlüssel liegt nicht in den Export-Vorgaben, sondern im Schlüsselbund von Lightroom.
local function tokenKey(url)
  return 'ModelGallery|' .. (url or '')
end

local RAW_FORMATS = { RAW = true, DNG = true }

-- Lightroom sperrt den Export-Button, solange LR_cantExportBecause einen Text enthält.
local function updateCanExport(props)
  if Api.normalizeUrl(props.serverUrl) == '' then
    props.LR_cantExportBecause = 'Bitte die Server-Adresse eintragen.'
  elseif not props.albumId or props.albumId == 0 then
    props.LR_cantExportBecause = 'Bitte «Verbinden» klicken und ein Album wählen.'
  else
    props.LR_cantExportBecause = nil
  end
end

local function loadAlbums(props)
  local url = Api.normalizeUrl(props.serverUrl)
  local token = Api.trim(props.apiToken)
  if url == '' or token == '' then
    props.status = 'Bitte Server-Adresse und API-Schlüssel eintragen.'
    return
  end
  props.serverUrl = url
  props.status = 'Verbinde …'
  local ok, info = Api.info(url, token)
  if not ok then
    props.status = 'Fehler: ' .. info
    return
  end
  LrPasswords.store(tokenKey(url), token)
  local saved = LrPasswords.retrieve(tokenKey(url))
  Api.log('Schlüssel im Schlüsselbund gespeichert:', saved == token and 'ja' or 'NEIN')
  local okAlbums, albums = Api.albums(url, token)
  if not okAlbums then
    props.status = 'Fehler: ' .. albums
    return
  end
  local items, found = {}, false
  for _, a in ipairs(albums) do
    items[#items + 1] = {
      title = string.format('%s / %s  (%d Bilder)', a.path, a.title, a.images),
      value = a.id,
    }
    if a.id == props.albumId then found = true end
  end
  props.albumItems = items
  if not found then
    props.albumId = (#items > 0) and items[1].value or 0
  end
  updateCanExport(props)
  Api.log('verbunden, Album gewählt:', props.albumId, 'Export gesperrt:', props.LR_cantExportBecause or 'nein')
  if #items == 0 then
    props.status = string.format('Verbunden mit «%s», aber es gibt noch kein Album für diesen Zugang.', info.site)
  else
    props.status = string.format('Verbunden mit «%s» als %s – %d Alben. Schlüssel ist im Schlüsselbund gespeichert.', info.site, info.name, #items)
  end
end

function provider.startDialog(props)
  props.status = ''
  props.apiToken = ''
  props.albumItems = {}
  if props.albumId and props.albumId ~= 0 then
    props.albumItems = { { title = props.albumLabel ~= '' and props.albumLabel or ('Album ' .. props.albumId), value = props.albumId } }
  end

  props:addObserver('albumId', function(p)
    for _, item in ipairs(p.albumItems or {}) do
      if item.value == p.albumId then p.albumLabel = item.title end
    end
    updateCanExport(p)
  end)
  props:addObserver('serverUrl', updateCanExport)
  updateCanExport(props)

  -- Gespeicherten Schlüssel aus dem Schlüsselbund holen und Albumliste automatisch laden
  LrTasks.startAsyncTask(function()
    local url = Api.normalizeUrl(props.serverUrl)
    if url == '' then
      Api.log('Dialog geöffnet, noch keine Server-Adresse')
      props.status = 'Server-Adresse und API-Schlüssel eintragen, dann «Verbinden» klicken.'
      return
    end
    local saved = LrPasswords.retrieve(tokenKey(url))
    Api.log('Dialog geöffnet:', url, '– Schlüssel im Schlüsselbund:', (saved and saved ~= '') and 'ja' or 'nein')
    if saved and saved ~= '' then
      props.apiToken = saved
      props.status = 'Gespeicherter Schlüssel geladen – verbinde …'
      loadAlbums(props)
    else
      props.status = 'Kein gespeicherter Schlüssel für diese Adresse. Bitte eintragen und «Verbinden» klicken.'
    end
  end)
end

function provider.sectionsForTopOfDialog(f, props)
  local bind = LrView.bind
  local share = LrView.share
  local label = function(title)
    return f:static_text { title = title, alignment = 'right', width = share 'mg_label' }
  end
  return {
    {
      title = 'Model Gallery',
      synopsis = bind 'albumLabel',

      f:row {
        label 'Server-Adresse:',
        f:edit_field { value = bind 'serverUrl', width_in_chars = 38, immediate = true },
      },
      f:row {
        label 'API-Schlüssel:',
        f:password_field { value = bind 'apiToken', width_in_chars = 38 },
        f:push_button {
          title = 'Verbinden',
          action = function()
            LrTasks.startAsyncTask(function() loadAlbums(props) end)
          end,
        },
      },
      f:row {
        label '',
        f:static_text { title = bind 'status', width_in_chars = 52, height_in_lines = 2 },
      },
      f:row {
        label 'Album:',
        f:popup_menu { value = bind 'albumId', items = bind 'albumItems', width_in_chars = 48 },
      },
      f:row {
        label '',
        f:checkbox { title = 'Original-RAW zusätzlich hochladen (z. B. CR3, NEF, ARW, RAF, DNG)', value = bind 'uploadRaw' },
      },
      f:row {
        label '',
        f:checkbox { title = 'Gleichnamige Bilder im Album als neue Version ersetzen', value = bind 'replaceVersions' },
      },
      f:row {
        label '',
        f:static_text {
          title = 'Das JPEG wird mit den Einstellungen unten erzeugt (Dateieinstellungen, Bildgrösse, Schärfen, Wasserzeichen).',
          width_in_chars = 52, height_in_lines = 2, size = 'small',
        },
      },
    },
  }
end

-- Dateiname für den Upload: Originalname ohne Endung, bei virtuellen Kopien mit Kopiename
local function baseName(photo)
  local stem = LrPathUtils.removeExtension(photo:getFormattedMetadata('fileName') or 'bild')
  local copyName = photo:getFormattedMetadata('copyName')
  if copyName and copyName ~= '' then
    stem = stem .. '-' .. copyName:gsub('[^%w%-_ ]', ''):gsub('%s+', '-')
  end
  return stem
end

function provider.processRenderedPhotos(functionContext, exportContext)
  local props = exportContext.propertyTable
  local url = Api.normalizeUrl(props.serverUrl)
  local token = LrPasswords.retrieve(tokenKey(url)) or ''
  Api.log('Export: Schlüssel im Schlüsselbund:', token ~= '' and 'ja' or 'nein')
  if token == '' then
    LrDialogs.message('Model Gallery', 'Kein API-Schlüssel gespeichert. Bitte im Export-Dialog den Schlüssel eintragen und «Verbinden» klicken.', 'critical')
    return
  end
  if not props.albumId or props.albumId == 0 then
    LrDialogs.message('Model Gallery', 'Bitte im Export-Dialog ein Album wählen.', 'critical')
    return
  end

  local session = exportContext.exportSession
  local count = session:countRenditions()
  Api.log('Export startet:', count, 'Fotos nach Album', props.albumId, 'RAW:', props.uploadRaw)
  local progress = exportContext:configureProgress {
    title = count > 1 and string.format('%d Fotos an Model Gallery senden', count) or 'Foto an Model Gallery senden',
  }

  local uploaded, raws = 0, 0
  for _, rendition in exportContext:renditions { stopIfCanceled = true } do
    local photo = rendition.photo
    local success, pathOrMessage = rendition:waitForRender()
    if progress:isCanceled() then break end

    if not success then
      Api.log('Rendern fehlgeschlagen:', pathOrMessage)
      rendition:uploadFailed(pathOrMessage)
    else
      local stem = baseName(photo)
      local ok, result = Api.upload(url, token, props.albumId, pathOrMessage, stem .. '.jpg', 'image/jpeg', props.replaceVersions)
      LrFileUtils.delete(pathOrMessage)
      if not ok then
        rendition:uploadFailed(result)
      else
        uploaded = uploaded + 1
        if props.uploadRaw and RAW_FORMATS[photo:getRawMetadata('fileFormat') or ''] then
          local original = photo:getRawMetadata('path')
          if not original or not LrFileUtils.exists(original) then
            rendition:uploadFailed('JPEG hochgeladen, aber die RAW-Datei wurde nicht gefunden (Laufwerk nicht verbunden?).')
          else
            local ext = LrPathUtils.extension(original)
            local okRaw, rawResult = Api.upload(url, token, props.albumId, original, stem .. '.' .. ext,
              'application/octet-stream', true)
            if okRaw then
              raws = raws + 1
            else
              rendition:uploadFailed('JPEG hochgeladen, RAW fehlgeschlagen: ' .. rawResult)
            end
          end
        end
      end
    end
  end
  Api.log('Export fertig:', uploaded, 'Bilder,', raws, 'RAW-Dateien')
  progress:done()
end

return provider
