--[[
  Model Gallery – Export-Plugin für Adobe Lightroom Classic.
  Exportiert Fotos als JPEG direkt in ein bestehendes Album und lädt auf Wunsch die Original-RAW-Datei mit.
]]
return {
  LrSdkVersion = 10.0,
  LrSdkMinimumVersion = 6.0,
  LrToolkitIdentifier = 'ch.modelgallery.lightroom',
  LrPluginName = 'Model Gallery',

  LrExportServiceProvider = {
    title = 'Model Gallery',
    file = 'GalleryExportServiceProvider.lua',
  },

  VERSION = { major = 1, minor = 0, revision = 2, build = 3 },
}
