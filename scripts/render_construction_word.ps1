param(
  [Parameter(Mandatory=$true)][string]$InputDocx,
  [Parameter(Mandatory=$true)][string]$OutputPdf
)
$ErrorActionPreference = 'Stop'
$inputFull = (Resolve-Path -LiteralPath $InputDocx).Path
$outputFull = [System.IO.Path]::GetFullPath($OutputPdf)
$wordApp = $null
$wordDoc = $null
try {
  $wordApp = New-Object -ComObject Word.Application
  $wordApp.Visible = $false
  $wordApp.DisplayAlerts = 0
  $wordApp.AutomationSecurity = 3
  $wordDoc = $wordApp.Documents.Open($inputFull, $false, $false, $false)
  foreach ($toc in $wordDoc.TablesOfContents) { $toc.Update() }
  $wordDoc.Fields.Update() | Out-Null
  $wordDoc.Repaginate()
  $wordDoc.Save()
  $wordDoc.ExportAsFixedFormat($outputFull, 17)
} finally {
  if ($null -ne $wordDoc) { $wordDoc.Close(0); [void][Runtime.InteropServices.Marshal]::ReleaseComObject($wordDoc) }
  if ($null -ne $wordApp) { $wordApp.Quit(); [void][Runtime.InteropServices.Marshal]::ReleaseComObject($wordApp) }
}
