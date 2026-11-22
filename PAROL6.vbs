' Double-clic : met a jour le logiciel depuis GitHub, puis ouvre la fenetre.
' Aucun terminal. Le raccourci Bureau est cree ou rafraichi a chaque lancement.
Option Explicit

Dim fso, sh, dir, appData, logPath, pythonCmd

Set fso = CreateObject("Scripting.FileSystemObject")
Set sh = CreateObject("WScript.Shell")
dir = fso.GetParentFolderName(WScript.ScriptFullName)
sh.CurrentDirectory = dir

If WScript.Arguments.Count > 0 Then
  If LCase(WScript.Arguments(0)) = "/check" Then
    WScript.Echo FindPython(sh)
    WScript.Quit 0
  End If
End If

appData = sh.ExpandEnvironmentStrings("%APPDATA%") & "\PAROL6"
If Not fso.FolderExists(appData) Then
  fso.CreateFolder appData
End If
logPath = appData & "\update.log"

EnsureShortcut sh, dir
UpdateFromGit sh, logPath

pythonCmd = FindPython(sh)
If pythonCmd = "" Then
  MsgBox "Python est introuvable, ou Tkinter n'est pas installe." & vbCrLf & vbCrLf & _
    "Installe Python 3.11 depuis python.org et coche « Add python.exe to PATH », puis relance PAROL6.", _
    vbCritical, "PAROL6"
  WScript.Quit 1
End If

sh.Run pythonCmd & " " & Quote(dir & "\sensor_gui.py"), 0, False

Function Quote(s)
  Quote = """" & s & """"
End Function

Function TryCmd(sh, cmd)
  On Error Resume Next
  TryCmd = (sh.Run(cmd, 0, True) = 0)
  If Err.Number <> 0 Then
    Err.Clear
    TryCmd = False
  End If
  On Error GoTo 0
End Function

Function FindPython(sh)
  Dim candidates(2), i
  candidates(0) = "pyw -3.11"
  candidates(1) = "pyw -3"
  candidates(2) = "pythonw"
  FindPython = ""
  For i = 0 To 2
    If TryCmd(sh, candidates(i) & " -c ""import tkinter""") Then
      FindPython = candidates(i)
      Exit Function
    End If
  Next
End Function

Sub EnsureShortcut(sh, dir)
  Dim desktop, sc
  desktop = sh.SpecialFolders("Desktop")
  Set sc = sh.CreateShortcut(desktop & "\PAROL6.lnk")
  sc.TargetPath = "wscript.exe"
  sc.Arguments = "//nologo " & Quote(dir & "\PAROL6.vbs")
  sc.WorkingDirectory = dir
  sc.Description = "Ouvrir PAROL6"
  sc.WindowStyle = 7
  sc.Save
End Sub

Sub UpdateFromGit(sh, logPath)
  Dim env
  On Error Resume Next
  Set env = sh.Environment("PROCESS")
  env("GIT_TERMINAL_PROMPT") = "0"
  sh.Run "cmd /c git pull --ff-only > " & Quote(logPath) & " 2>&1", 0, True
  On Error GoTo 0
End Sub
