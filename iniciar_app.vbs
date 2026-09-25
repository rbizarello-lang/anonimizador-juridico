Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(WScript.ScriptFullName)
pythonw = root & "\.venv\Scripts\pythonw.exe"
If Not fso.FileExists(pythonw) Then
  pythonw = root & "\.venv\Scripts\python.exe"
End If
If Not fso.FileExists(pythonw) Then
  MsgBox "Nao achei o Python do app (.venv).", 16, "Anonimizador Juridico"
  WScript.Quit 1
End If
Set sh = CreateObject("Wscript.Shell")
sh.CurrentDirectory = root
cmd = Chr(34) & pythonw & Chr(34) & " " & Chr(34) & root & "\app.py" & Chr(34)
sh.Run cmd, 0, False
