' Start the toolbox with no console window (used by Windows autostart).
'
' Why not a plain shortcut to python.exe:
'   It shows a black console window, and closing that window kills the server.
' Why not pythonw.exe:
'   pythonw has no stdout, so work\server.log would stay empty and we lose logs.
'
' This launcher asks Windows Script Host to run cmd with window style 0 (hidden),
' so nothing appears on screen while run.py output still goes to work\server.log.
' Register it under HKCU\Software\Microsoft\Windows\CurrentVersion\Run as:
'   "C:\Windows\System32\wscript.exe" //B "E:\Course_Learn\xhs-summarizer\start-hidden.vbs"

Option Explicit

Dim shell, fso, root, logFile, pythonExe, quote, command

Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' The script sits in the project root, so resolve paths from its own location
root = fso.GetParentFolderName(WScript.ScriptFullName)
logFile = root & "\work\server.log"
If Not fso.FolderExists(root & "\work") Then
  fso.CreateFolder root & "\work"
End If

' python comes from PATH (Anaconda); override with XHS_PYTHON if needed
pythonExe = shell.ExpandEnvironmentStrings("%XHS_PYTHON%")
If pythonExe = "%XHS_PYTHON%" Or pythonExe = "" Then
  pythonExe = "python"
End If

quote = Chr(34)
command = "cmd /c " & quote & quote & pythonExe & quote & " run.py --no-browser >> " _
        & quote & logFile & quote & " 2>&1" & quote

' Run in the project directory, hidden window, do not wait for exit
shell.CurrentDirectory = root
shell.Run command, 0, False
