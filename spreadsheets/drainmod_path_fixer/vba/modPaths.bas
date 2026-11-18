Attribute VB_Name = "modPaths"
'==========================================================================
' DRAINMOD PATH FIXER
'   DRAINMOD writes full paths into a project's .prj and .gen and into some
'   of its input files (.cin, .dmn, ...), so a project folder that is copied
'   or moved still points to the old place (e.g. C:\DDESIGN). This workbook
'   finds where a project used to be and where it is now, shows every line
'   that would change, lists files that are missing and crop-file paths too
'   long for DRAINMOD, rewrites the files (keeping each original as
'   <file>.bak) and can undo that from the .bak copies.
'==========================================================================
Option Explicit

' input files that may hold paths (weather and soil data files do not)
Private Const TEXTEXT As String = "|gen|cin|dmn|dmp|dms|dmf|sin|"
Private Const NFMAX As Long = 2000

' the move of the project being looked at (both "" when nothing moved)
Private oldRoot As String
Private newRoot As String

' files of the last check
Private nF As Long
Private fPath(1 To NFMAX) As String      ' the file
Private fProj(1 To NFMAX) As String      ' the project that names it
Private fOld(1 To NFMAX) As String       ' its old and new place
Private fNew(1 To NFMAX) As String
Private fLines(1 To NFMAX) As Long       ' lines that would change
Private fNote(1 To NFMAX) As String

' next free rows of the report sheets
Private rowC As Long, rowP As Long
Private nMissing As Long, nLong As Long, nCropProj As Long

'============================================================ buttons ====
Public Sub EnsureButtons()
    Dim ws As Worksheet
    Set ws = ThisWorkbook.Worksheets("Fix Paths")
    AddButton ws, "btnPrj", "Select a .prj...", "SelectProject", ws.Range("C3"), 110
    AddButton ws, "btnFolder", "Select a folder...", "SelectFolder", ws.Range("D3"), 110
    AddButton ws, "btnCheck", "1. Check paths", "CheckPaths", ws.Range("B10"), 220
    AddButton ws, "btnFix", "2. Fix paths (keeps .bak copies)", "FixPaths", ws.Range("B12"), 220
    AddButton ws, "btnUndo", "3. Undo: restore the .bak copies", "UndoFix", ws.Range("B14"), 220
End Sub

Private Sub AddButton(ws As Worksheet, ByVal nm As String, ByVal caption As String, ByVal macro As String, _
        ByVal at As Range, ByVal wd As Double)
    Dim b As Object
    On Error Resume Next
    Set b = ws.Buttons(nm)
    On Error GoTo 0
    If b Is Nothing Then
        Set b = ws.Buttons.Add(at.Left, at.Top, wd, 20)
        b.Name = nm
    End If
    b.Caption = caption
    b.OnAction = macro
End Sub

Public Sub SelectProject()
    Dim f As Variant
    f = Application.GetOpenFilename("DRAINMOD project (*.prj),*.prj", , "Select the DRAINMOD project")
    If f = False Then Exit Sub
    Cfg("Target").Value = f
End Sub

Public Sub SelectFolder()
    With Application.FileDialog(msoFileDialogFolderPicker)
        .Title = "Select the folder with the DRAINMOD projects (.prj)"
        If .Show <> -1 Then Exit Sub
        Cfg("Target").Value = .SelectedItems(1)
    End With
End Sub

Private Sub SetStatus(ByVal msg As String)
    Cfg("Status").Value = msg
    Application.StatusBar = Left$(msg, 250)
    DoEvents
End Sub

Private Function AddSlash(ByVal p As String) As String
    p = Trim$(p)
    If p <> "" And Right$(p, 1) <> "\" Then p = p & "\"
    AddSlash = p
End Function

'========================================================== projects ====
' run copies written by the calibration and design workbooks are left out
Private Function IsRunCopy(ByVal nm As String) As Boolean
    Dim b As String
    b = LCase$(BaseName(nm))
    IsRunCopy = (b Like "*_cal" Or b Like "*_cal#" Or b Like "*_cal##" Or b Like "*_dsg" Or b Like "*_dsg#" Or b Like "*_dsg##")
End Function

' the projects to look at: the selected .prj, or every .prj in the folder
Private Function ListProjects() As Collection
    Dim c As New Collection, t As String, folder As String, all As Boolean, f As String
    t = Trim$(CStr(Cfg("Target").Value))
    If t = "" Then Err.Raise vbObjectError + 901, "Paths", "Select a DRAINMOD project (.prj) or a folder of projects."
    If FolderExists(t) Then
        folder = AddSlash(t): all = True
    ElseIf FileExists(t) Then
        folder = FolderOf(t): all = IsYes(Cfg("AllInFolder").Value)
    Else
        Err.Raise vbObjectError + 902, "Paths", "Not found: " & t
    End If
    If all Then
        f = Dir(folder & "*.prj")
        Do While f <> ""
            If Not IsRunCopy(f) Then c.Add folder & f
            f = Dir
        Loop
    Else
        c.Add t
    End If
    If c.Count = 0 Then Err.Raise vbObjectError + 903, "Paths", "No DRAINMOD projects (.prj) in " & folder
    Set ListProjects = c
End Function

' a file to look at (each once), with the move of the project that names it
Private Sub AddFile(ByVal p As String, ByVal prj As String)
    Dim i As Long
    For i = 1 To nF
        If LCase$(fPath(i)) = LCase$(p) Then Exit Sub
    Next i
    If nF >= NFMAX Then Exit Sub
    nF = nF + 1
    fPath(nF) = p: fProj(nF) = prj: fOld(nF) = oldRoot: fNew(nF) = newRoot
    fLines(nF) = 0: fNote(nF) = ""
End Sub

' the project, its .gen and the input files it names that may hold paths
Private Sub AddProject(ByVal prj As String)
    Dim txt As String, lines As Variant, i As Long, l As String, v As String, rec As String, gen As String
    Dim ext As String, o As String, n As String
    txt = ReadAllText(prj)
    lines = SplitLines(txt)
    For i = 0 To UBound(lines)
        l = Trim$(lines(i))
        If LCase$(Right$(l, 4)) = ".gen" And InStr(l, "=") > 0 Then
            rec = Trim$(Mid$(l, InStr(l, "=") + 1))
            If InStr(rec, "\") = 0 Then rec = FolderOf(prj) & rec
            Exit For
        End If
    Next i
    ' the move: from the Setup cells if given, else found from the project
    o = Trim$(CStr(Cfg("OldRoot").Value)): n = Trim$(CStr(Cfg("NewRoot").Value))
    If o <> "" And n <> "" Then
        oldRoot = AddSlash(o): newRoot = AddSlash(n)
        If LCase$(oldRoot) = LCase$(newRoot) Then oldRoot = "": newRoot = ""
    ElseIf rec <> "" Then
        FindRoots prj, rec
    Else
        oldRoot = "": newRoot = ""
    End If
    AddFile prj, prj
    If rec <> "" Then
        gen = MapText(rec)
        If Not FileExists(gen) Then
            If FileExists(FolderOf(prj) & Mid$(gen, InStrRev(gen, "\") + 1)) Then gen = FolderOf(prj) & Mid$(gen, InStrRev(gen, "\") + 1)
        End If
        If FileExists(gen) Then AddFile gen, prj
    End If
    For i = 0 To UBound(lines)
        l = Trim$(lines(i))
        If InStr(l, "=") > 0 And Left$(l, 1) <> "[" Then
            v = MapText(Trim$(Mid$(l, InStr(l, "=") + 1)))
            If InStrRev(v, ".") > InStrRev(v, "\") Then
                ext = LCase$(Mid$(v, InStrRev(v, ".") + 1))
                If ext <> "gen" And InStr(TEXTEXT, "|" & ext & "|") > 0 Then
                    If FileExists(v) Then AddFile v, prj
                End If
            End If
        End If
    Next i
End Sub

'======================================================= crop counts ====
' DRAINMOD will not run when the .prj names a different number of crops than
' its phosphorus file (.dmp) lists in its *Crops section. Such a project is
' made corn only: the .prj keeps just its corn crop (as crop 1, with its
' CROPNAME and [CropAlias] lines) and the .dmp just that crop's row (as C1).
' The crop whose file name contains "corn" is kept (crop 1 if none does).
' Returns the number of lines that change; with apply the files are
' rewritten (each saved first as .bak), else the changes are listed.
Private Function CropFix(ByVal prj As String, ByVal apply As Boolean) As Long
    Dim pl As Variant, dl As Variant, i As Long, l As String, sec As String, key As String, v As String
    Dim numCrop As Long, nPrj As Long, nDmp As Long, prjCount As Long, dmp As String, k As Long, idx As Long
    Dim cropPath(1 To 50) As String, newP() As String, newD() As String, np As Long, nd As Long
    Dim inCrops As Boolean, changed As Long, t As Variant, nl As String, fi As Long, note As String
    fi = FileIndex(prj)
    oldRoot = "": newRoot = ""
    If fi > 0 Then oldRoot = fOld(fi): newRoot = fNew(fi)
    pl = SplitLines(ReadAllText(prj))
    ' the project's crops and its phosphorus file
    For i = 0 To UBound(pl)
        l = Trim$(pl(i))
        If Left$(l, 1) = "[" Then
            sec = LCase$(l)
        ElseIf InStr(l, "=") > 0 Then
            key = LCase$(Trim$(Left$(l, InStr(l, "=") - 1)))
            v = Trim$(Mid$(l, InStr(l, "=") + 1))
            If sec = "[crops]" And key = "numcrop" Then numCrop = Val(v)
            If sec = "[crops]" And key Like "crop#*" Then
                idx = Val(Mid$(key, 5))
                If idx >= 1 And idx <= 50 Then
                    cropPath(idx) = v
                    If idx > nPrj Then nPrj = idx
                End If
            End If
            If key = "phosphorus" And v <> "" Then dmp = MapText(v)
        End If
    Next i
    If dmp = "" Or nPrj = 0 Then Exit Function
    If InStr(dmp, "\") = 0 Then dmp = FolderOf(prj) & dmp
    If Not FileExists(dmp) Then Exit Function
    prjCount = nPrj
    If numCrop > 0 Then prjCount = numCrop
    ' crop rows of the .dmp
    dl = SplitLines(ReadAllText(dmp))
    For i = 0 To UBound(dl)
        l = Trim$(dl(i))
        If Left$(l, 1) = "*" Then
            inCrops = (LCase$(Left$(Mid$(l, 2), 5)) = "crops")
        ElseIf inCrops And IsCropRow(l) Then
            nDmp = nDmp + 1
        End If
    Next i
    If nDmp = prjCount And nDmp = nPrj Then Exit Function
    ' the crop to keep
    k = 0
    For idx = 1 To nPrj
        If k = 0 And InStr(1, BaseName(cropPath(idx)), "corn", vbTextCompare) > 0 Then k = idx
    Next idx
    If k = 0 Then
        k = 1
        note = " No crop file is named corn, so crop 1 (" & BaseName(cropPath(1)) & ") is kept."
    End If
    If Not apply Then
        nCropProj = nCropProj + 1
        AddProblem "Crop count", prj, 0, dmp, "The project names " & prjCount & " crop(s) but its phosphorus file lists " & nDmp & _
            ", so DRAINMOD will not run. ""2. Fix paths"" makes both corn only (" & BaseName(cropPath(k)) & ")." & note
    End If
    ' the .prj, corn only
    ReDim newP(0 To UBound(pl)): np = 0: sec = ""
    For i = 0 To UBound(pl)
        l = Trim$(pl(i)): nl = MapText(CStr(pl(i)))
        If Left$(l, 1) = "[" Then sec = LCase$(l)
        key = ""
        If InStr(l, "=") > 0 And Left$(l, 1) <> "[" Then key = LCase$(Trim$(Left$(l, InStr(l, "=") - 1)))
        v = ""
        If key <> "" Then v = Trim$(Mid$(nl, InStr(nl, "=") + 1))
        If sec = "[crops]" And key = "numcrop" Then
            nl = "NumCrop=1"
        ElseIf (sec = "[crops]" Or sec = "[cropalias]") And key Like "crop#*" Then
            If Val(Mid$(key, 5)) = k Then nl = "Crop1=" & v Else nl = vbNullChar
        ElseIf sec = "[crops]" And key Like "cropname#*" Then
            If Val(Mid$(key, 9)) = k Then nl = "CROPNAME1=" & v Else nl = vbNullChar
        End If
        If nl <> MapText(CStr(pl(i))) Then
            changed = changed + 1
            If Not apply Then AddChange prj, i + 1, CStr(pl(i)), IIf(nl = vbNullChar, "(line removed)", nl)
        End If
        If nl <> vbNullChar Then newP(np) = nl: np = np + 1
    Next i
    ' the .dmp, the corn row only
    ReDim newD(0 To UBound(dl)): nd = 0: inCrops = False
    For i = 0 To UBound(dl)
        l = Trim$(dl(i)): nl = MapText(CStr(dl(i)))
        If Left$(l, 1) = "*" Then
            inCrops = (LCase$(Left$(Mid$(l, 2), 5)) = "crops")
        ElseIf inCrops And IsCropRow(l) Then
            t = Tokens(l)
            If Val(Mid$(CStr(t(0)), 2)) <> k Then
                nl = vbNullChar
            ElseIf k <> 1 Then
                ' renumber as crop 1: "C<k>  <k> ..." -> "C1  1 ..."
                nl = RenumberRow(CStr(dl(i)), k)
            End If
        End If
        If nl <> MapText(CStr(dl(i))) Then
            changed = changed + 1
            If Not apply Then AddChange dmp, i + 1, CStr(dl(i)), IIf(nl = vbNullChar, "(line removed)", nl)
        End If
        If nl <> vbNullChar Then newD(nd) = nl: nd = nd + 1
    Next i
    If apply And changed > 0 Then
        For Each t In Array(prj, dmp)
            If Not FileExists(CStr(t) & ".bak") Then FileCopy CStr(t), CStr(t) & ".bak"
        Next t
        ReDim Preserve newP(0 To np - 1)
        ReDim Preserve newD(0 To nd - 1)
        WriteAllText prj, Join(newP, vbCrLf) & vbCrLf
        WriteAllText dmp, Join(newD, vbCrLf) & vbCrLf
        fi = FileIndex(prj): If fi > 0 Then fNote(fi) = "set to corn only"
        fi = FileIndex(dmp): If fi > 0 Then fNote(fi) = "set to corn only"
    End If
    CropFix = changed
End Function

' a row of the *Crops section of a .dmp: C1, C2, ...
Private Function IsCropRow(ByVal l As String) As Boolean
    Dim t As Variant
    t = Tokens(l)
    If UBound(t) < 1 Then Exit Function
    If UCase$(Left$(CStr(t(0)), 1)) <> "C" Then Exit Function
    IsCropRow = IsIntTok(Mid$(CStr(t(0)), 2))
End Function

' "C3    3  0.30 ..." as crop 1: "C1    1  0.30 ...", spacing kept
Private Function RenumberRow(ByVal ln As String, ByVal k As Long) As String
    Dim p As Long, q As Long, ks As String
    ks = CStr(k)
    p = InStr(1, ln, "C" & ks, vbTextCompare)
    RenumberRow = Left$(ln, p - 1) & "C1" & Space$(Len(ks) - 1) & Mid$(ln, p + 1 + Len(ks))
    q = InStr(p + 2, RenumberRow, " " & ks & " ")
    If q > 0 Then RenumberRow = Left$(RenumberRow, q) & Space$(Len(ks) - 1) & "1" & Mid$(RenumberRow, q + 1 + Len(ks))
End Function

Private Function FileIndex(ByVal p As String) As Long
    Dim i As Long
    For i = 1 To nF
        If LCase$(fPath(i)) = LCase$(p) Then FileIndex = i: Exit Function
    Next i
End Function

Private Sub AddChange(ByVal f As String, ByVal ln As Long, ByVal before As String, ByVal after As String)
    With ThisWorkbook.Worksheets("Changes")
        .Cells(rowC, 1).Value = f
        .Cells(rowC, 2).Value = ln
        .Cells(rowC, 3).Value = "'" & before
        .Cells(rowC, 4).Value = "'" & after
    End With
    rowC = rowC + 1
End Sub

'============================================================ report ====
Private Sub StartReport()
    With ThisWorkbook.Worksheets("Changes")
        .Cells.Clear
        .Range("A1:D1").Value = Array("File", "Line", "Now", "After the fix")
        .Rows(1).Font.Bold = True
    End With
    With ThisWorkbook.Worksheets("Problems")
        .Cells.Clear
        .Range("A1:E1").Value = Array("Problem", "File", "Line", "Path (after the fix)", "Note")
        .Rows(1).Font.Bold = True
    End With
    With ThisWorkbook.Worksheets("Files")
        .Cells.Clear
        .Range("A1:F1").Value = Array("File", "Named by project", "Old place", "New place", "Lines to change", "Status")
        .Rows(1).Font.Bold = True
    End With
    rowC = 2: rowP = 2: nMissing = 0: nLong = 0: nCropProj = 0
End Sub

Private Sub AddProblem(ByVal kind As String, ByVal f As String, ByVal ln As Long, ByVal p As String, ByVal note As String)
    With ThisWorkbook.Worksheets("Problems")
        .Cells(rowP, 1).Value = kind
        .Cells(rowP, 2).Value = f
        .Cells(rowP, 3).Value = ln
        .Cells(rowP, 4).Value = p
        .Cells(rowP, 5).Value = note
    End With
    rowP = rowP + 1
End Sub

' the paths on a line: from each "X:\" (or "\\") to a comma, tab, quote or the end
Private Function PathsIn(ByVal ln As String) As Collection
    Dim c As New Collection, pos As Long, st As Long, en As Long, k As Long, ch As String
    pos = 1
    Do
        st = 0
        k = InStr(pos, ln, ":\")
        If k > 1 Then
            ch = Mid$(ln, k - 1, 1)
            If ch Like "[A-Za-z]" Then st = k - 1
        End If
        If st = 0 Then
            k = InStr(pos, ln, "\\")
            If k > 0 Then st = k
        End If
        If st = 0 Then Exit Do
        en = Len(ln) + 1
        For k = st + 2 To Len(ln)
            ch = Mid$(ln, k, 1)
            If ch = "," Or ch = vbTab Or ch = """" Then en = k: Exit For
        Next k
        c.Add Trim$(Mid$(ln, st, en - st))
        pos = en
        If pos > Len(ln) Then Exit Do
    Loop
    Set PathsIn = c
End Function

' changes and problems of file i
Private Sub ScanFile(ByVal i As Long, ByVal maxName As Long)
    Dim lines As Variant, k As Long, ln As String, nl As String, p As Variant, isPrj As Boolean, key As String
    lines = SplitLines(ReadAllText(fPath(i)))
    oldRoot = fOld(i): newRoot = fNew(i)
    isPrj = (LCase$(Right$(fPath(i), 4)) = ".prj")
    For k = 0 To UBound(lines)
        ln = lines(k)
        nl = MapText(ln)
        If nl <> ln Then
            fLines(i) = fLines(i) + 1
            With ThisWorkbook.Worksheets("Changes")
                .Cells(rowC, 1).Value = fPath(i)
                .Cells(rowC, 2).Value = k + 1
                .Cells(rowC, 3).Value = "'" & ln
                .Cells(rowC, 4).Value = "'" & nl
            End With
            rowC = rowC + 1
        End If
        key = ""
        If InStr(nl, "=") > 0 Then key = LCase$(Trim$(Left$(nl, InStr(nl, "=") - 1)))
        For Each p In PathsIn(nl)
            If p <> "" Then
                If Not (FileExists(CStr(p)) Or FolderExists(CStr(p))) Then
                    nMissing = nMissing + 1
                    AddProblem "Missing", fPath(i), k + 1, CStr(p), "Not found, even after the fix. Copy it into the project or correct the project in DRAINMOD."
                End If
                If isPrj And key Like "crop#*" And Len(CStr(p)) > maxName Then
                    nLong = nLong + 1
                    AddProblem "Too long", fPath(i), k + 1, CStr(p), "DRAINMOD reads crop file names in " & maxName & _
                        " characters (this one has " & Len(CStr(p)) & "), so this crop would be left out. Move the project folder to a shorter path, e.g. directly under C:\."
                End If
            End If
        Next p
    Next k
End Sub

' check (and with doFix, fix) the selected projects
Private Sub RunCheck(ByVal doFix As Boolean)
    Dim projects As Collection, prj As Variant, i As Long, nFiles As Long, nLines As Long, maxName As Long
    Dim ws As Worksheet, msg As String, done As Long, txt As String, firstMove As String, ans As VbMsgBoxResult
    Dim k As Long, fi As Long, cropDone As Long, nCropSeen As Long
    Application.ScreenUpdating = False
    nF = 0
    Set projects = ListProjects()
    For Each prj In projects
        AddProject CStr(prj)
        If firstMove = "" And oldRoot <> "" Then firstMove = oldRoot & "  ->  " & newRoot
    Next prj
    maxName = CfgOpt("MaxName", 48)
    StartReport
    For i = 1 To nF
        ScanFile i, maxName
        If fLines(i) > 0 Then nFiles = nFiles + 1: nLines = nLines + fLines(i)
    Next i
    ' projects whose .prj and .dmp list different numbers of crops
    For Each prj In projects
        k = CropFix(CStr(prj), False)
        If k > 0 Then
            nLines = nLines + k: nFiles = nFiles + 1
            fi = FileIndex(CStr(prj))
            If fi > 0 Then
                If fLines(fi) = 0 Then nFiles = nFiles + 1
            End If
        End If
    Next prj
    If doFix And nLines > 0 Then
        ans = MsgBox("Rewrite " & nLines & " line(s) in " & nFiles & " file(s)?" & _
            IIf(nCropProj > 0, vbLf & vbLf & nCropProj & " project(s) list a different number of crops in the .prj and the .dmp; they will be set to corn only.", "") & vbLf & vbLf & _
            "Each file is saved first as <file>.bak (unless a .bak copy is already there), so ""3. Undo"" can restore it.", _
            vbYesNo + vbQuestion, "Fix paths")
        If ans <> vbYes Then
            doFix = False
        Else
            For i = 1 To nF
                If fLines(i) > 0 Then
                    oldRoot = fOld(i): newRoot = fNew(i)
                    txt = ReadAllText(fPath(i))
                    If Not FileExists(fPath(i) & ".bak") Then FileCopy fPath(i), fPath(i) & ".bak"
                    WriteAllText fPath(i), Replace(txt, oldRoot, newRoot, 1, -1, vbTextCompare)
                    fNote(i) = "fixed"
                    done = done + 1
                End If
            Next i
            For Each prj In projects
                If CropFix(CStr(prj), True) > 0 Then cropDone = cropDone + 1
            Next prj
        End If
    End If
    Set ws = ThisWorkbook.Worksheets("Files")
    For i = 1 To nF
        ws.Cells(i + 1, 1).Value = fPath(i)
        ws.Cells(i + 1, 2).Value = BaseName(fProj(i)) & ".prj"
        ws.Cells(i + 1, 3).Value = fOld(i)
        ws.Cells(i + 1, 4).Value = fNew(i)
        ws.Cells(i + 1, 5).Value = fLines(i)
        If fNote(i) <> "" Then
            ws.Cells(i + 1, 6).Value = fNote(i)
        ElseIf fLines(i) > 0 Then
            ws.Cells(i + 1, 6).Value = "to fix"
        Else
            ws.Cells(i + 1, 6).Value = "ok"
        End If
    Next i
    For Each ws In ThisWorkbook.Worksheets
        If ws.Name = "Changes" Or ws.Name = "Problems" Or ws.Name = "Files" Then ws.Columns("A:F").AutoFit
    Next ws
    Cfg("Moved").Value = IIf(firstMove = "", "(the paths match where the project is)", firstMove)
    Cfg("NFiles").Value = nFiles
    Cfg("NLines").Value = nLines
    Cfg("NMissing").Value = nMissing
    Cfg("NLong").Value = nLong
    Cfg("NCrop").Value = nCropProj
    nCropSeen = nCropProj
    Application.ScreenUpdating = True
    If done > 0 Or cropDone > 0 Then
        msg = "Fixed " & nLines & " line(s) in " & projects.Count & " project(s)."
        If cropDone > 0 Then msg = msg & " " & cropDone & " project(s) set to corn only (the .prj and .dmp listed different numbers of crops)."
    ElseIf nLines > 0 Then
        msg = nLines & " line(s) in " & nFiles & " file(s) of " & projects.Count & " project(s) point to the old place; see the Changes sheet."
    Else
        msg = "No paths to fix in " & projects.Count & " project(s)."
    End If
    If nMissing > 0 Or nLong > 0 Then msg = msg & " " & nMissing & " missing file(s) and " & nLong & " crop path(s) too long for DRAINMOD: see the Problems sheet."
    If nCropSeen > 0 And cropDone = 0 Then msg = msg & " " & nCropSeen & " project(s) list a different number of crops in the .prj and the .dmp, so DRAINMOD will not run them: ""2. Fix paths"" sets them to corn only."
    SetStatus msg
    Application.StatusBar = False
    If done > 0 Or cropDone > 0 Then
        ' the files now point to the new place: show what is left
        RunCheck False
        Cfg("Status").Value = msg
    End If
End Sub

Public Sub CheckPaths()
    On Error GoTo Failed
    RunCheck False
    If Cfg("NLines").Value > 0 Then ThisWorkbook.Worksheets("Changes").Activate
    Exit Sub
Failed:
    Application.ScreenUpdating = True
    Application.StatusBar = False
    MsgBox "Could not check the paths: " & Err.Description, vbExclamation
End Sub

Public Sub FixPaths()
    On Error GoTo Failed
    RunCheck True
    Exit Sub
Failed:
    Application.ScreenUpdating = True
    Application.StatusBar = False
    MsgBox "Could not fix the paths: " & Err.Description, vbExclamation
End Sub

' restore the .bak copies of the selected projects' files
Public Sub UndoFix()
    Dim projects As Collection, prj As Variant, i As Long, n As Long, lst As String
    On Error GoTo Failed
    nF = 0
    Set projects = ListProjects()
    For Each prj In projects
        AddProject CStr(prj)
    Next prj
    For i = 1 To nF
        If FileExists(fPath(i) & ".bak") Then
            n = n + 1
            If n <= 15 Then lst = lst & fPath(i) & vbLf
        End If
    Next i
    If n = 0 Then
        MsgBox "None of these projects' files has a .bak copy to restore.", vbInformation
        Exit Sub
    End If
    If MsgBox("Restore " & n & " file(s) from their .bak copies?" & vbLf & vbLf & lst & IIf(n > 15, "..." & vbLf, "") & vbLf & _
        "Each file is replaced by its .bak copy, and the .bak copy is then removed.", vbYesNo + vbQuestion, "Undo") <> vbYes Then Exit Sub
    For i = 1 To nF
        If FileExists(fPath(i) & ".bak") Then
            FileCopy fPath(i) & ".bak", fPath(i)
            Kill fPath(i) & ".bak"
        End If
    Next i
    RunCheck False
    SetStatus "Restored " & n & " file(s) from their .bak copies."
    Application.StatusBar = False
    Exit Sub
Failed:
    Application.ScreenUpdating = True
    Application.StatusBar = False
    MsgBox "Could not undo: " & Err.Description, vbExclamation
End Sub

'=========================================================== helpers ====
' a token that is a whole number (digits only)
Private Function IsIntTok(ByVal s As String) As Boolean
    IsIntTok = (Len(s) > 0 And Not (s Like "*[!0-9]*"))
End Function

' (shared with the calibration and drainage design workbooks)
'=========================================================== helpers ====
' (shared with the calibration workbook)
Private Function NumStr(ByVal x As Double, ByVal decimals As Integer) As String
    Dim fmt As String
    If decimals > 0 Then fmt = "0." & String$(decimals, "0") Else fmt = "0"
    NumStr = Replace(Format$(x, fmt), ",", ".")
    If Left$(NumStr, 1) = "-" And Val(NumStr) = 0 Then NumStr = Mid$(NumStr, 2)
End Function

Private Function RJ(ByVal v As String, ByVal w As Integer) As String
    RJ = v
    If Len(RJ) < w Then RJ = Space$(w - Len(RJ)) & RJ
End Function

Private Function Tokens(ByVal s As String) As Variant
    Dim t As String
    t = Application.WorksheetFunction.Trim(Replace(s, vbTab, " "))
    If t = "" Then
        Tokens = Array()
    Else
        Tokens = Split(t, " ")
    End If
End Function

Private Function IsYes(ByVal v As Variant) As Boolean
    Dim t As String
    t = LCase$(Trim$(CStr(v)))
    IsYes = (t = "yes" Or t = "y" Or t = "true" Or t = "1")
End Function

Private Function FolderOf(ByVal p As String) As String
    FolderOf = Left$(p, InStrRev(p, "\"))
End Function

Private Function BaseName(ByVal p As String) As String
    Dim f As String
    f = Mid$(p, InStrRev(p, "\") + 1)
    If InStrRev(f, ".") > 0 Then f = Left$(f, InStrRev(f, ".") - 1)
    BaseName = f
End Function

Private Function ReadAllText(ByVal path As String) As String
    Dim f As Integer
    f = FreeFile
    Open path For Binary Access Read As #f
    ReadAllText = Space$(LOF(f))
    Get #f, , ReadAllText
    Close #f
End Function

Private Sub WriteAllText(ByVal path As String, ByVal txt As String)
    Dim f As Integer
    If Dir(path) <> "" Then Kill path
    f = FreeFile
    Open path For Binary Access Write As #f
    Put #f, , txt
    Close #f
End Sub

Private Function SplitLines(ByVal txt As String) As Variant
    txt = Replace(txt, vbCrLf, vbLf)
    txt = Replace(txt, vbCr, vbLf)
    If Right$(txt, 1) = vbLf Then txt = Left$(txt, Len(txt) - 1)
    SplitLines = Split(txt, vbLf)
End Function

'============================================================ helpers ====
Private Function Cfg(ByVal nm As String) As Range
    Set Cfg = ThisWorkbook.Names(nm).RefersToRange
End Function

' value of an optional setting; dflt if the workbook has no such cell or it is blank
Private Function CfgOpt(ByVal nm As String, ByVal dflt As Double) As Double
    Dim v As Variant
    CfgOpt = dflt
    On Error GoTo Done
    v = ThisWorkbook.Names(nm).RefersToRange.Value
    If IsNumeric(v) And Not IsEmpty(v) Then CfgOpt = CDbl(v)
Done:
End Function

'==================================================== moved projects ====
' DRAINMOD writes full paths into the .prj and .gen (and some .cin, .dmn
' files), so a project folder copied to another place still points to the old
' one. The old place is the folder the project names for its .gen; the new
' place is the folder the .prj is in now. Common folder names at the end of
' both are kept, so C:\DDESIGN\inputs\ -> E:\Work\DDESIGN\inputs\ maps
' C:\DDESIGN\ to E:\Work\DDESIGN\ and everything under it.
Private Sub FindRoots(ByVal prjPath As String, ByVal genRecorded As String)
    Dim rec As String, cur As String, a As Variant, b As Variant, i As Long, j As Long, k As Long
    oldRoot = "": newRoot = ""
    If InStr(genRecorded, "\") = 0 Then Exit Sub
    rec = FolderOf(genRecorded)
    cur = FolderOf(prjPath)
    If LCase$(rec) = LCase$(cur) Then Exit Sub
    a = Split(Left$(rec, Len(rec) - 1), "\")
    b = Split(Left$(cur, Len(cur) - 1), "\")
    i = UBound(a): j = UBound(b)
    Do While i >= 1 And j >= 1
        If LCase$(a(i)) <> LCase$(b(j)) Then Exit Do
        i = i - 1: j = j - 1
    Loop
    If i >= 1 And j >= 1 Then
        ' a folder differs (the project folder was renamed): the roots end there
    ElseIf i < UBound(a) And j < UBound(b) Then
        ' one path ran out of folders: keep the top common folder (DDESIGN) in the roots
        i = i + 1: j = j + 1
    End If
    For k = 0 To i
        oldRoot = oldRoot & a(k) & "\"
    Next k
    For k = 0 To j
        newRoot = newRoot & b(k) & "\"
    Next k
    If LCase$(oldRoot) = LCase$(newRoot) Then oldRoot = "": newRoot = ""
End Sub

' a file or folder exists (False also for a drive that does not exist)
Private Function FileExists(ByVal p As String) As Boolean
    On Error Resume Next
    If p <> "" Then FileExists = (Dir(p) <> "")
End Function

Private Function FolderExists(ByVal p As String) As Boolean
    On Error Resume Next
    If p <> "" Then FolderExists = ((GetAttr(p) And vbDirectory) = vbDirectory)
End Function

' every mention of the old place in a line of text, moved to the new place
Private Function MapText(ByVal t As String) As String
    MapText = t
    If oldRoot = "" Then Exit Function
    MapText = Replace(t, oldRoot, newRoot, 1, -1, vbTextCompare)
    ' the old folder itself at the end of a line (no "\" after it)
    If LCase$(Right$(MapText, Len(oldRoot) - 1)) = LCase$(Left$(oldRoot, Len(oldRoot) - 1)) Then
        MapText = Left$(MapText, Len(MapText) - Len(oldRoot) + 1) & Left$(newRoot, Len(newRoot) - 1)
    End If
End Function
