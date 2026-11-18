Dim fso, shell, dir
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
shell.Run """" & dir & "\.venv\Scripts\pythonw.exe"" """ & dir & "\main.pyw""", 0, False
