Attribute VB_Name = "modCalib"
'==========================================================================
' DRAINMOD CALIBRATION
'   Finds the parameter values that maximize the Kling-Gupta efficiency
'   (KGE) between measured and simulated daily drain flow and/or water
'   table depth, using Dynamically Dimensioned Search (DDS; Tolson and
'   Shoemaker 2007).
'
'   The user's DRAINMOD project (.prj/.gen) is never changed: each run
'   writes <name>_cal.gen / <name>_cal.prj next to it, runs dmhydro.exe,
'   and reads the daily output file (drain flow = column 5, water table
'   depth = column 8). Dates come from the first column(s): decimal year
'   as in DRAINMOD's .PLT, y m d, m d y, d m y or year + day of year,
'   found by testing each layout on the first 40 lines; otherwise lines
'   are counted day by day from the start of the run.
'   A run that fails during the search is logged and skipped.
'==========================================================================
Option Explicit

Private Const NPARMAX As Integer = 20
Private Const NWMAX As Integer = 16            ' most DRAINMOD runs side by side
Private Const SYNC_TERMINATE As Long = &H100001 ' SYNCHRONIZE Or PROCESS_TERMINATE
Private Const WAIT_TIMEOUT As Long = &H102
Private Const MAXNAME As Integer = 48         ' longest file name DRAINMOD reads for crops
Private Const ZEROQ As Double = 0.0001       ' drain flows below this (cm/day) count as zero

' running DRAINMOD without blocking Excel, so several runs can go side by side
#If VBA7 Then
Private Declare PtrSafe Function OpenProcess Lib "kernel32" (ByVal dwDesiredAccess As Long, ByVal bInheritHandle As Long, ByVal dwProcessId As Long) As LongPtr
Private Declare PtrSafe Function WaitForSingleObject Lib "kernel32" (ByVal hHandle As LongPtr, ByVal dwMilliseconds As Long) As Long
Private Declare PtrSafe Function TerminateProcess Lib "kernel32" (ByVal hProcess As LongPtr, ByVal uExitCode As Long) As Long
Private Declare PtrSafe Function CloseHandle Lib "kernel32" (ByVal hObject As LongPtr) As Long
Private Declare PtrSafe Function GetShortPathName Lib "kernel32" Alias "GetShortPathNameA" (ByVal lpszLongPath As String, ByVal lpszShortPath As String, ByVal cchBuffer As Long) As Long
Private Declare PtrSafe Sub Sleep Lib "kernel32" (ByVal dwMilliseconds As Long)
Private wHandle(1 To NWMAX) As LongPtr
#Else
Private Declare Function OpenProcess Lib "kernel32" (ByVal dwDesiredAccess As Long, ByVal bInheritHandle As Long, ByVal dwProcessId As Long) As Long
Private Declare Function WaitForSingleObject Lib "kernel32" (ByVal hHandle As Long, ByVal dwMilliseconds As Long) As Long
Private Declare Function TerminateProcess Lib "kernel32" (ByVal hProcess As Long, ByVal uExitCode As Long) As Long
Private Declare Function CloseHandle Lib "kernel32" (ByVal hObject As Long) As Long
Private Declare Function GetShortPathName Lib "kernel32" Alias "GetShortPathNameA" (ByVal lpszLongPath As String, ByVal lpszShortPath As String, ByVal cchBuffer As Long) As Long
Private Declare Sub Sleep Lib "kernel32" (ByVal dwMilliseconds As Long)
Private wHandle(1 To NWMAX) As Long
#End If
Private wStart(1 To NWMAX) As Date      ' when each run slot was started
Private wFail(1 To NWMAX) As String     ' why it failed (blank if it ran)
Private nWork As Integer                ' run slots in use

' original project
Private genLines() As String
Private nGen As Long
Private prjText As String
' calibration parameters (selected rows of the Parameters sheet)
Private pN As Integer
Private pRow(1 To NPARMAX) As Long
Private pKey(1 To NPARMAX) As String
Private pMin(1 To NPARMAX) As Double
Private pMax(1 To NPARMAX) As Double
Private pLog(1 To NPARMAX) As Boolean
' measured data (calibration window)
Private nObs As Long
Private oDate() As Long
Private oQ() As Double
Private oW() As Double
Private oHasQ() As Boolean
Private oHasW() As Boolean
' last simulation
Private sN As Long
Private sDate() As Long
Private sQ() As Double
Private sW() As Double
' statistics of the last evaluation
Private stQ(1 To 9) As Double
Private stW(1 To 9) As Double
Private runStart As Date
Private lastX() As Double
' a project that was moved: its files still name oldRoot (e.g. C:\DDESIGN\),
' it now sits under newRoot; both "" when the paths match
Private oldRoot As String
Private newRoot As String
Private layersFixed As Boolean  ' the last ParseLayers read fixed 10-character columns

'============================================================ buttons ====
Public Sub EnsureButtons()
    Dim ws As Worksheet
    Set ws = ThisWorkbook.Worksheets("Setup")
    AddButton ws, "btnBrowsePrj", "Browse...", "BrowseProject", ws.Range("D3")
    AddButton ws, "btnBrowseExe", "Browse...", "BrowseProgram", ws.Range("D4")
    AddButton ws, "btnBrowseOut", "Browse...", "BrowseOutput", ws.Range("D5")
    AddButton ws, "btnFixPaths", "Fix paths...", "FixProjectPaths", ws.Range("D6")
    AddButton ws, "btnRead", "1. Read project parameters", "ReadProjectParameters", ws.Range("D8")
    AddButton ws, "btnEval", "2. Evaluate current values", "EvaluateCurrent", ws.Range("D10")
    AddButton ws, "btnSens", "3. Sensitivity analysis", "RunSensitivity", ws.Range("D12")
    AddButton ws, "btnCalib", "4. Run calibration", "RunCalibration", ws.Range("D14")
    AddButton ws, "btnWrite", "5. Write calibrated project", "WriteCalibratedProject", ws.Range("D16")
End Sub

Private Sub AddButton(ws As Worksheet, ByVal nm As String, ByVal caption As String, ByVal macro As String, ByVal at As Range)
    Dim b As Object
    On Error Resume Next
    Set b = ws.Buttons(nm)
    On Error GoTo 0
    If b Is Nothing Then
        Set b = ws.Buttons.Add(at.Left, at.Top, 190, 22)
        b.Name = nm
    Else
        b.Left = at.Left: b.Top = at.Top
    End If
    b.caption = caption
    b.OnAction = macro
End Sub

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

Private Function SciStr(ByVal x As Double) As String
    SciStr = Replace(Format$(x, "0.000000E+000"), ",", ".")
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

Private Function NTok(ByVal s As String) As Long
    NTok = UBound(Tokens(s)) + 1
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

Private Sub SetStatus(ByVal msg As String)
    Cfg("Status").Value = msg
    Application.StatusBar = Left$(msg, 250)        ' Excel's status bar takes at most 255 characters
    DoEvents
End Sub

'=========================================================== browsing ====
Public Sub BrowseProject()
    Dim f As Variant
    f = Application.GetOpenFilename("DRAINMOD project (*.prj),*.prj", , "Select the DRAINMOD project to calibrate")
    If f = False Then Exit Sub
    Cfg("PrjFile").Value = f
    Cfg("OutFile").Value = ""
    Cfg("OutAuto").Value = "Yes"
    If Trim$(CStr(Cfg("DmExe").Value)) = "" Then GuessProgram
End Sub

Public Sub BrowseProgram()
    Dim f As Variant
    f = Application.GetOpenFilename("DRAINMOD program (dmhydro.exe),*.exe", , "Select dmhydro.exe")
    If f = False Then Exit Sub
    Cfg("DmExe").Value = f
End Sub

Public Sub BrowseOutput()
    Dim f As Variant
    f = Application.GetOpenFilename("DRAINMOD daily output (*.*),*.*", , "Select the DRAINMOD daily output file")
    If f = False Then Exit Sub
    Cfg("OutFile").Value = f
    Cfg("OutAuto").Value = "No"
End Sub

Private Sub GuessProgram()
    Dim p As String, cand As Variant
    p = FolderOf(CStr(Cfg("PrjFile").Value))
    For Each cand In Array(p & "dmhydro.exe", p & "..\program\dmhydro.exe", p & "program\dmhydro.exe", _
                           p & "..\..\program\dmhydro.exe")
        If Dir(CStr(cand)) <> "" Then
            Cfg("DmExe").Value = CStr(cand)
            Exit Sub
        End If
    Next cand
End Sub

'======================================================= project files ====
Private Function GenPathFromPrj(ByVal prjPath As String) As String
    Dim lines As Variant, i As Long, l As String, p As Long, g As String
    prjText = ReadAllText(prjPath)
    lines = SplitLines(prjText)
    For i = 0 To UBound(lines)
        l = Trim$(lines(i))
        If LCase$(Right$(l, 4)) = ".gen" And InStr(l, "=") > 0 Then
            g = Trim$(Mid$(l, InStr(l, "=") + 1))
            If InStr(g, "\") = 0 Then g = FolderOf(prjPath) & g
            GenPathFromPrj = g
            Exit Function
        End If
    Next i
    Err.Raise vbObjectError + 700, "Project", "No .gen file is named in " & prjPath
End Function

Private Sub LoadProject()
    Dim prj As String, gp As String, lines As Variant, i As Long, alt As String, rec As String
    prj = Trim$(CStr(Cfg("PrjFile").Value))
    If prj = "" Or Dir(prj) = "" Then Err.Raise vbObjectError + 701, "Project", "Select the DRAINMOD project (.prj) on the Setup sheet."
    rec = GenPathFromPrj(prj)
    FindRoots prj, rec
    gp = MapPath(rec)
    If Not FileExists(gp) Then
        ' a .gen next to the project, under the same name
        alt = FolderOf(prj) & Mid$(gp, InStrRev(gp, "\") + 1)
        If FileExists(alt) Then
            gp = alt
        ElseIf FileExists(rec) Then
            ' the .gen is kept where the project says, outside the project folder
            oldRoot = "": newRoot = ""
            gp = rec
        End If
    End If
    If Not FileExists(gp) Then Err.Raise vbObjectError + 702, "Project", "The .gen file named in the project was not found: " & gp
    lines = SplitLines(ReadAllText(gp))
    nGen = UBound(lines) + 1
    ReDim genLines(0 To nGen - 1)
    For i = 0 To nGen - 1
        genLines(i) = MapText(CStr(lines(i)))
    Next i
    Cfg("GenFile").Value = gp
    RemapCell "DmExe"
    RemapCell "OutFile"
End Sub

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

' a path from the project, moved to where the project is now
Private Function MapPath(ByVal p As String) As String
    MapPath = p
    If oldRoot = "" Then Exit Function
    If LCase$(Left$(p, Len(oldRoot))) = LCase$(oldRoot) Then
        MapPath = newRoot & Mid$(p, Len(oldRoot) + 1)
    ElseIf LCase$(p & "\") = LCase$(oldRoot) Then
        MapPath = Left$(newRoot, Len(newRoot) - 1)
    End If
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

' rewrite the paths in the project's own files (.prj, .gen and the input files
' the .prj names: crops, nitrogen, phosphorus, salinity, forest), each saved
' first as <file>.bak; returns the files changed, one per line
Private Function DoFixPaths() As String
    Dim files As New Collection, lines As Variant, i As Long, l As String, v As String, ext As String
    Dim f As Variant, txt As String, done As String
    On Error Resume Next
    files.Add CStr(Cfg("PrjFile").Value), LCase$(CStr(Cfg("PrjFile").Value))
    files.Add CStr(Cfg("GenFile").Value), LCase$(CStr(Cfg("GenFile").Value))
    lines = SplitLines(prjText)
    For i = 0 To UBound(lines)
        l = Trim$(lines(i))
        If InStr(l, "=") > 0 Then
            v = MapPath(Trim$(Mid$(l, InStr(l, "=") + 1)))
            ext = LCase$(Mid$(v, InStrRev(v, ".") + 1))
            If InStr(v, "\") > 0 And (ext = "cin" Or ext = "dmn" Or ext = "dmp" Or ext = "dms" Or ext = "dmf") Then
                If FileExists(v) Then files.Add v, LCase$(v)
            End If
        End If
    Next i
    On Error GoTo 0
    For Each f In files
        txt = ReadAllText(CStr(f))
        If InStr(1, txt, oldRoot, vbTextCompare) > 0 Then
            If Dir(CStr(f) & ".bak") = "" Then FileCopy CStr(f), CStr(f) & ".bak"
            WriteAllText CStr(f), Replace(txt, oldRoot, newRoot, 1, -1, vbTextCompare)
            done = done & CStr(f) & vbLf
        End If
    Next f
    DoFixPaths = done
End Function

' ask, then rewrite the project's files for where it is now
Private Sub OfferFixPaths(ByVal always As Boolean)
    Dim ans As VbMsgBoxResult, done As String
    If oldRoot = "" Then
        If always Then MsgBox "The paths in the project already match where it is.", vbInformation
        Exit Sub
    End If
    ans = MsgBox("The project's files point to " & oldRoot & " but the project is now in " & newRoot & "." & vbLf & vbLf & _
        "Rewrite the paths in the project's files (.prj, .gen, crop and other input files) to " & newRoot & "?" & vbLf & _
        "Each file that changes is saved first as <file>.bak." & vbLf & vbLf & _
        "If you choose No, the design runs still use the right paths; only your project files stay as they are " & _
        "(and DRAINMOD itself will still look in " & oldRoot & ").", vbYesNo + vbQuestion, "Project was moved")
    If ans <> vbYes Then Exit Sub
    done = DoFixPaths()
    LoadProject
    If done = "" Then
        MsgBox "No file needed changing.", vbInformation
    Else
        MsgBox "Paths rewritten in:" & vbLf & done, vbInformation
    End If
End Sub

Public Sub FixProjectPaths()
    On Error GoTo Failed
    LoadProject
    OfferFixPaths True
    Exit Sub
Failed:
    MsgBox "Could not fix the paths: " & Err.Description, vbExclamation
End Sub

' a file named on the Setup sheet under the project's old place, moved
Private Sub RemapCell(ByVal nm As String)
    Dim v As String
    v = Trim$(CStr(Cfg(nm).Value))
    If v = "" Or oldRoot = "" Then Exit Sub
    If Not FileExists(v) And FileExists(MapPath(v)) Then Cfg(nm).Value = MapPath(v)
End Sub

Private Function FindSection(g() As String, ByVal title As String, Optional ByVal startAt As Long = 0) As Long
    Dim i As Long
    For i = startAt To UBound(g)
        If InStr(1, g(i), "***", vbTextCompare) > 0 And InStr(1, g(i), title, vbTextCompare) > 0 Then
            FindSection = i
            Exit Function
        End If
    Next i
    FindSection = -1
End Function

' ---- locations of the lines that hold the parameters
Private Function DrainLineIdx(g() As String) As Long
    Dim k As Long
    k = FindSection(g, "Drainage System Design")
    If k < 0 Then Err.Raise vbObjectError + 710, "gen", "No '*** Drainage System Design ***' section in the .gen file."
    DrainLineIdx = k + 2
    If NTok(g(k + 2)) < 8 Then Err.Raise vbObjectError + 711, "gen", "Unexpected drainage line in the .gen file: " & g(k + 2)
End Function

Private Function SoilsIdx(g() As String) As Long
    SoilsIdx = FindSection(g, "Soils")
    If SoilsIdx < 0 Then Err.Raise vbObjectError + 712, "gen", "No '*** Soils ***' section in the .gen file."
End Function

Private Function ClimateIdx(g() As String) As Long
    ClimateIdx = FindSection(g, "Climate")
    If ClimateIdx < 0 Then Err.Raise vbObjectError + 713, "gen", "No '*** Climate ***' section in the .gen file."
End Function

' ---- drainage line: 8 fields, 10 characters each
Private Function DrainField(g() As String, ByVal k As Integer) As Double
    DrainField = Val(Tokens(g(DrainLineIdx(g)))(k - 1))
End Function

Private Sub SetDrainField(g() As String, ByVal k As Integer, ByVal v As Double)
    Dim i As Long, t As Variant, j As Integer, out As String
    i = DrainLineIdx(g)
    t = Tokens(g(i))
    t(k - 1) = NumStr(v, 2)
    For j = 0 To 7
        out = out & RJ(CStr(t(j)), 10)
    Next j
    For j = 8 To UBound(t)
        out = out & RJ(CStr(t(j)), 10)
    Next j
    g(i) = out
End Sub

' ---- soils: line 1 = impermeable depth, effective radius; line 2 = layers
'      layer line: fixed 10-character columns per layer (depth 5, Ksat 5),
'      as DRAINMOD reads it; space-separated pairs are accepted as well
Private Function ParseLayers(ByVal ln As String, dep() As String, kv() As Double) As Integer
    Dim n As Integer, m As Integer, j As Integer, d As String, k As String, ok As Boolean, t As Variant
    layersFixed = False
    ln = RTrim$(ln)
    n = (Len(ln) + 9) \ 10
    If n > 0 And n <= 10 Then
        ReDim dep(1 To n): ReDim kv(1 To n)
        ok = True
        For j = 1 To n
            d = Trim$(Mid$(ln, (j - 1) * 10 + 1, 5)): k = Trim$(Mid$(ln, (j - 1) * 10 + 6, 5))
            If Not IsNumeric(d) Or Not IsNumeric(k) Or k = "" Then ok = False: Exit For
            ' unused layers are written with depth 0 (DRAINMOD 7); the list ends there
            If Val(d) <= 0 Then Exit For
            dep(j) = d: kv(j) = Val(k): m = j
        Next j
        If ok And m > 0 Then
            layersFixed = True
            ParseLayers = m
            Exit Function
        End If
    End If
    t = Tokens(ln)
    n = (UBound(t) + 1) \ 2
    If n = 0 Then Exit Function
    ReDim dep(1 To n): ReDim kv(1 To n)
    m = 0
    For j = 1 To n
        If Val(t(2 * j - 2)) <= 0 Then Exit For
        dep(j) = t(2 * j - 2): kv(j) = Val(t(2 * j - 1)): m = j
    Next j
    ParseLayers = m
End Function

' layer depth in at most 5 characters
Private Function DepStr5(ByVal d As Double) As String
    If d < 1000 Then DepStr5 = NumStr(d, 1) Else DepStr5 = CStr(CLng(d)) & "."
End Function

' write the layer line (depth 5, Ksat 5 per layer); in fixed columns the
' unused layers after the last one are kept as they were. Call right after
' the ParseLayers that read dep/kv.
Private Sub WriteLayers(g() As String, dep() As String, kv() As Double, ByVal m As Integer)
    Dim i As Long, j As Integer, out As String, d As String, orig As String, fixedCols As Boolean, ks As String
    fixedCols = layersFixed
    i = SoilsIdx(g) + 2
    orig = RTrim$(g(i))
    For j = 1 To m
        d = dep(j)
        If InStr(d, ".") = 0 Then d = d & "."
        ' a Ksat that has not changed keeps the text it had
        ks = ""
        If fixedCols Then ks = Trim$(Mid$(orig, (j - 1) * 10 + 6, 5))
        If ks = "" Or Val(ks) <> kv(j) Then ks = KStr5(kv(j))
        out = out & RJ(d, 5) & RJ(ks, 5)
    Next j
    If fixedCols And Len(orig) > m * 10 Then out = out & Mid$(orig, m * 10 + 1)
    g(i) = out
    SyncKsatCopy g, dep, kv, m
End Sub

' DRAINMOD 7 keeps a second copy of the layers under
' "** Initial Depth - Ksat Layer Data **"; keep its Ksat values the same
Private Sub SyncKsatCopy(g() As String, dep() As String, kv() As Double, ByVal m As Integer)
    Dim i As Long, j As Integer, ln As String
    ' only the Ksat values: DRAINMOD keeps this copy's depths as they were
    ' (it does not move them when the impermeable layer changes)
    For i = 0 To UBound(g) - 1
        If InStr(1, g(i), "Initial Depth - Ksat", vbTextCompare) > 0 Then
            ln = g(i + 1)
            If Len(ln) < m * 10 Then ln = ln & Space$(m * 10 - Len(ln))
            For j = 1 To m
                If Val(Mid$(ln, (j - 1) * 10 + 6, 5)) <> kv(j) Then Mid$(ln, (j - 1) * 10 + 6, 5) = RJ(KStr5(kv(j)), 5)
            Next j
            g(i + 1) = ln
            Exit Sub
        End If
    Next i
End Sub



Private Function LayerCount(g() As String) As Integer
    Dim dep() As String, kv() As Double
    LayerCount = ParseLayers(g(SoilsIdx(g) + 2), dep, kv)
End Function

Private Function LayerK(g() As String, ByVal n As Integer) As Double
    Dim dep() As String, kv() As Double
    If n > ParseLayers(g(SoilsIdx(g) + 2), dep, kv) Then Err.Raise vbObjectError + 714, "gen", "The project has fewer than " & n & " soil layers."
    LayerK = kv(n)
End Function

' Ksat in at most 5 characters, with as many decimals as fit after rounding
' (so 9.9996 becomes "10.00", not the 6-character "10.000")
Private Function KStr5(ByVal k As Double) As String
    Dim dec As Integer, s As String
    If k >= 99999.5 Then k = 99999
    If k < 0.0001 Then k = 0.0001
    For dec = 4 To 0 Step -1
        s = NumStr(k, dec)
        If Left$(s, 2) = "0." Then s = Mid$(s, 2)
        If Len(s) <= 5 Then
            KStr5 = s
            Exit Function
        End If
    Next dec
    KStr5 = "99999"
End Function

Private Sub SetLayerK(g() As String, ByVal n As Integer, ByVal k As Double)
    Dim dep() As String, kv() As Double, m As Integer
    m = ParseLayers(g(SoilsIdx(g) + 2), dep, kv)
    kv(n) = k
    WriteLayers g, dep, kv, m
End Sub


Private Function ImpDepth(g() As String) As Double
    ImpDepth = Val(Tokens(g(SoilsIdx(g) + 1))(0))
End Function

Private Function EffRadius(g() As String) As Double
    EffRadius = Val(Tokens(g(SoilsIdx(g) + 1))(1))
End Function

Private Sub SetEffRadius(g() As String, ByVal r As Double)
    Dim i As Long, t As Variant
    i = SoilsIdx(g) + 1
    t = Tokens(g(i))
    g(i) = RJ(CStr(t(0)), 10) & RJ(NumStr(r, 3), 10)
End Sub


' depth of the impermeable layer from the surface (soils line 1)
Private Sub SetImpDepth(g() As String, ByVal h As Double)
    Dim i As Long, t As Variant, hs As String, dep() As String, kv() As Double, m As Integer
    i = SoilsIdx(g) + 1
    t = Tokens(g(i))
    ' whole cm in projects that write it that way (County Simulations), else 2 decimals
    If InStr(CStr(t(0)), ".") > 0 Then hs = NumStr(h, 2) Else hs = CStr(CLng(h))
    g(i) = RJ(hs, 10) & RJ(CStr(t(1)), 10)
    ' as DRAINMOD does, the deepest soil layer ends at the impermeable layer
    m = ParseLayers(g(SoilsIdx(g) + 2), dep, kv)
    If m > 0 Then
        If m > 1 Then
            If Val(hs) <= Val(dep(m - 1)) Then Err.Raise vbObjectError + 735, "gen", "The impermeable layer (" & hs & _
                " cm) must be below the bottom of soil layer " & (m - 1) & " (" & dep(m - 1) & " cm)."
        End If
        dep(m) = DepStr5(Val(hs))
        WriteLayers g, dep, kv, m
    End If
End Sub





' Equivalent depth (Moody/Hooghoudt) and Kirkham's G for the drain geometry,
' as DRAINMOD 7 computes them (both reproduce DRAINMOD's values to 0.01).
' County Simulations differs in two places: its Moody alpha has 2*(2/L)^2
' instead of 2*(d/L)^2, and its Kirkham G uses the distance from the drain to
' the impermeable layer where DRAINMOD uses the drain depth.
Private Sub RecomputeDrainGeometry(g() As String)
    Dim h As Double, b As Double, l As Double, r As Double, dd As Double, alpha As Double, de As Double
    Dim sumc As Double, p As Long, P10 As Double, P11 As Double, P12 As Double, P13 As Double, P14 As Double
    Dim P18 As Double, c1 As Double, c2 As Double, c As Double, gee As Double
    Const PI As Double = 3.141593
    h = ImpDepth(g): b = DrainField(g, 1): l = DrainField(g, 3): r = EffRadius(g)
    dd = h - b
    If dd <= 0 Then Err.Raise vbObjectError + 718, "gen", "The drain (depth " & NumStr(b, 1) & " cm) is not above the impermeable layer (depth " & NumStr(h, 0) & " cm)."
    If l <= 0 Or r <= 0 Then Err.Raise vbObjectError + 719, "gen", "The drain spacing and effective radius must be above zero."
    If dd / l < 0.3 Then
        alpha = 3.55 - 1.6 * dd / l + 2 * (dd / l) ^ 2
        de = dd / (1 + (dd / l) * (2.546479 * Log(dd / r) - alpha))
    Else
        de = l * PI / (8 * (Log(l / r) - 1.15))
    End If
    P10 = Tan(PI * (2 * b - r) / (4 * h))
    P11 = Tan(PI * r / (4 * h))
    P13 = Cos(PI * r / (2 * h))
    P14 = Cos(PI * (2 * b - r) / 2 / h)
    Do
        p = p + 1
        P12 = PI * p * l / (2 * h)
        P18 = (Exp(P12) + Exp(-P12)) / 2
        c1 = (P18 + P13) * (P18 - P14): c2 = (P18 - P13) * (P18 + P14)
        c = Log(c1 / c2)
        sumc = sumc + c
        If c < 0.1 Or p > 1000 Then Exit Do
    Loop
    gee = 2 * Log(Abs(P10) / P11) + 2 * sumc
    SetDrainField g, 2, de
    SetDrainField g, 7, Int(gee * 100) / 100
End Sub

' ---- seepage lines below the drainage line (flag 1 = on, then values):
'      1 downslope (hill slope, ...), 2 vertical/deep (piezometric head,
'      thickness, conductivity), 3 lateral (transmissive layer thickness, ...)
Private Function SeepName(ByVal which As Integer) As String
    SeepName = Choose(which, "Downslope", "Vertical (deep)", "Lateral")
End Function

Private Function SeepLineOn(g() As String, ByVal which As Integer) As Boolean
    Dim t As Variant
    t = Tokens(g(DrainLineIdx(g) + which))
    SeepLineOn = (UBound(t) >= 2 And Val(t(0)) = 1)
End Function

Private Function SeepVal(g() As String, ByVal which As Integer, ByVal k As Integer) As Double
    SeepVal = Val(Tokens(g(DrainLineIdx(g) + which))(k))
End Function

Private Sub SetSeepVal(g() As String, ByVal which As Integer, ByVal k As Integer, ByVal v As Double)
    Dim i As Long, t As Variant, j As Integer, out As String
    If Not SeepLineOn(g, which) Then Err.Raise vbObjectError + 715, "gen", SeepName(which) & _
        " seepage is turned off in this project, so its parameters cannot be calibrated."
    i = DrainLineIdx(g) + which
    t = Tokens(g(i))
    If k > UBound(t) Then Err.Raise vbObjectError + 715, "gen", "Unexpected " & LCase$(SeepName(which)) & " seepage line in the .gen file: " & g(i)
    out = Space$(11) & "1"
    For j = 1 To UBound(t)
        If j = k Then out = out & "    " & SciStr(v) Else out = out & "    " & SciStr(Val(t(j)))
    Next j
    g(i) = out
End Sub

' vertical (deep) seepage
Private Function SeepOn(g() As String) As Boolean
    SeepOn = SeepLineOn(g, 2)
End Function

Private Function SeepValue(g() As String, ByVal k As Integer) As Double
    SeepValue = SeepVal(g, 2, k)
End Function

Private Sub SetSeepValue(g() As String, ByVal k As Integer, ByVal v As Double)
    SetSeepVal g, 2, k, v
End Sub

' ---- PET factors (line 4 of the climate section, 12 values)
Private Sub ScalePET(g() As String, ByVal m As Double)
    Dim i As Long, t As Variant, j As Integer, out As String
    i = ClimateIdx(g) + 4
    t = Tokens(g(i))
    If UBound(t) < 11 Then Err.Raise vbObjectError + 716, "gen", "Unexpected PET factor line in the .gen file: " & g(i)
    For j = 0 To 11
        out = out & NumStr(Val(t(j)) * m, 2) & " "
    Next j
    g(i) = out
    ' DRAINMOD 7 repeats the factors under "** THORNTHWAITE MONTHLY PET **"
    For i = 0 To UBound(g) - 1
        If InStr(1, g(i), "THORNTHWAITE MONTHLY PET", vbTextCompare) > 0 Then
            t = Tokens(g(i + 1))
            If UBound(t) >= 11 Then
                out = ""
                For j = 0 To 11
                    out = out & RJ(NumStr(Val(t(j)) * m, 2), 5)
                Next j
                g(i + 1) = out
            End If
            Exit For
        End If
    Next i
End Sub


' ---- root depth table in every Crop section (entries: MMDD + F6.2)
Private Sub ScaleRoots(g() As String, ByVal m As Double)
    Dim k As Long, n As Long, r As Long, ln As String, out As String, j As Long, per As Long, p As Long
    Dim dt As String, found As Boolean
    k = FindSection(g, "Crop")
    Do While k >= 0
        n = Val(g(k + 4))
        r = k + 5: j = 0
        Do While j < n
            If r > UBound(g) Then Err.Raise vbObjectError + 717, "gen", "The root depth table in the Crop section has fewer than " & n & " entries."
            ln = RTrim$(g(r)): out = "": per = 0
            Do While j < n And per * 10 < Len(ln)
                p = per * 10 + 1
                dt = Mid$(ln, p, 4)
                out = out & dt & RJ(NumStr(Val(Mid$(ln, p + 4, 6)) * m, 2), 6)
                j = j + 1: per = per + 1
            Loop
            If per = 0 Then Err.Raise vbObjectError + 717, "gen", "The root depth table in the Crop section has fewer than " & n & " entries."
            g(r) = out
            r = r + 1
        Loop
        found = True
        k = FindSection(g, "Crop", k + 1)
    Loop
    If Not found Then Err.Raise vbObjectError + 717, "gen", "No '*** Crop ***' section in the .gen file."
End Sub


' ---- simulation period (climate line 3: year1 month1 year2 month2 ...)
Private Sub ClimatePeriod(g() As String, ByRef y1 As Long, ByRef m1 As Long, ByRef y2 As Long, ByRef m2 As Long)
    Dim ln As String
    ln = g(ClimateIdx(g) + 3)
    y1 = Val(Mid$(ln, 1, 4)): m1 = Val(Mid$(ln, 5, 3)): y2 = Val(Mid$(ln, 8, 5)): m2 = Val(Mid$(ln, 13, 3))
End Sub

Private Sub SetClimatePeriod(g() As String, ByVal y1 As Long, ByVal m1 As Long, ByVal y2 As Long, ByVal m2 As Long)
    Dim i As Long
    i = ClimateIdx(g) + 3
    g(i) = RJ(CStr(y1), 4) & RJ(CStr(m1), 3) & RJ(CStr(y2), 5) & RJ(CStr(m2), 3) & Mid$(g(i), 16)
End Sub

' value of a parameter in a .gen
Private Function GenValue(g() As String, ByVal key As String) As Variant
    Select Case key
        Case "KSAT1", "KSAT2", "KSAT3", "KSAT4", "KSAT5"
            If CInt(Right$(key, 1)) > LayerCount(g) Then GenValue = "(no layer)" Else GenValue = LayerK(g, CInt(Right$(key, 1)))
        Case "KMULT", "PETMULT", "ROOTMULT": GenValue = 1
        Case "SMAX": GenValue = DrainField(g, 4)
        Case "DC": GenValue = DrainField(g, 5)
        Case "KIRK": GenValue = DrainField(g, 6)
        Case "RADIUS": GenValue = EffRadius(g)
        Case "DDEPTH": GenValue = DrainField(g, 1)
        Case "DSPACE": GenValue = DrainField(g, 3)
        Case "DIMP": GenValue = ImpDepth(g)
        Case "INITWT": GenValue = DrainField(g, 8)
        Case "LSEEPT"
            If SeepLineOn(g, 3) Then GenValue = SeepVal(g, 3, 1) Else GenValue = "(lateral seepage off)"
        Case "SEEPK"
            If SeepOn(g) Then GenValue = SeepValue(g, 3) Else GenValue = "(seepage off)"
        Case "SEEPH"
            If SeepOn(g) Then GenValue = SeepValue(g, 1) Else GenValue = "(seepage off)"
        Case Else: GenValue = ""
    End Select
End Function

Private Sub ApplyParam(g() As String, ByVal key As String, ByVal v As Double, k0() As Double)
    Dim n As Integer
    Select Case key
        Case "KSAT1", "KSAT2", "KSAT3", "KSAT4", "KSAT5": SetLayerK g, CInt(Right$(key, 1)), v
        Case "KMULT"
            For n = 1 To LayerCount(g)
                SetLayerK g, n, k0(n) * v
            Next n
        Case "SMAX": SetDrainField g, 4, v
        Case "DC": SetDrainField g, 5, v
        Case "KIRK": SetDrainField g, 6, v
        Case "RADIUS": SetEffRadius g, v
        Case "DDEPTH": SetDrainField g, 1, v
        Case "DSPACE": SetDrainField g, 3, v
        Case "DIMP": SetImpDepth g, v
        Case "INITWT": SetDrainField g, 8, v
        Case "LSEEPT": SetSeepVal g, 3, 1, v
        Case "SEEPK": SetSeepValue g, 3, v
        Case "SEEPH": SetSeepValue g, 1, v
        Case "PETMULT": ScalePET g, v
        Case "ROOTMULT": ScaleRoots g, v
    End Select
End Sub

'================================================ parameters & settings ====
Public Sub ReadProjectParameters()
    Dim r As Long, key As String, v As Variant, tbl As Range, y1 As Long, m1 As Long, y2 As Long, m2 As Long
    On Error GoTo Failed
    LoadProject
    OfferFixPaths False
    Set tbl = Cfg("ParamTable")
    For r = 1 To tbl.Rows.Count
        key = CStr(tbl.Cells(r, 3).Value)
        If key <> "" Then
            v = GenValue(genLines, key)
            tbl.Cells(r, 4).Value = v
        End If
    Next r
    ClimatePeriod genLines, y1, m1, y2, m2
    Cfg("SimPeriod").Value = y1 & "-" & Format$(m1, "00") & " to " & y2 & "-" & Format$(m2, "00")
    ' calibration period = simulation period (first day of the first month to
    ' the last day of the last month); the user can narrow it afterwards
    Cfg("CalStart").Value = DateSerial(y1, m1, 1)
    Cfg("CalEnd").Value = DateSerial(y2, m2 + 1, 0)
    If Cfg("CalStart").NumberFormat = "General" Then Cfg("CalStart").NumberFormat = "yyyy-mm-dd"
    If Cfg("CalEnd").NumberFormat = "General" Then Cfg("CalEnd").NumberFormat = "yyyy-mm-dd"
    If Trim$(CStr(Cfg("DmExe").Value)) = "" Then GuessProgram
    SetStatus "Read " & Cfg("GenFile").Value
    Application.StatusBar = False
    Exit Sub
Failed:
    Application.StatusBar = False
    MsgBox "Could not read the project: " & Err.Description, vbExclamation
End Sub

Private Sub LoadSelectedParams()
    Dim tbl As Range, r As Long, key As String, hasKsat As Boolean, hasMult As Boolean, i As Integer, iD As Integer, iH As Integer
    Set tbl = Cfg("ParamTable")
    pN = 0
    For r = 1 To tbl.Rows.Count
        key = CStr(tbl.Cells(r, 3).Value)
        If key <> "" And IsYes(tbl.Cells(r, 1).Value) Then
            pN = pN + 1
            If pN > NPARMAX Then Err.Raise vbObjectError + 720, "Parameters", "Too many parameters selected."
            pRow(pN) = r: pKey(pN) = key
            If Not IsNumeric(tbl.Cells(r, 5).Value) Or Not IsNumeric(tbl.Cells(r, 6).Value) Or IsEmpty(tbl.Cells(r, 5).Value) Or IsEmpty(tbl.Cells(r, 6).Value) Then
                Err.Raise vbObjectError + 721, "Parameters", "Enter a minimum and maximum for " & tbl.Cells(r, 2).Value & "."
            End If
            pMin(pN) = tbl.Cells(r, 5).Value: pMax(pN) = tbl.Cells(r, 6).Value
            If pMax(pN) <= pMin(pN) Then Err.Raise vbObjectError + 722, "Parameters", "The maximum must be larger than the minimum for " & tbl.Cells(r, 2).Value & "."
            pLog(pN) = (LCase$(Left$(Trim$(CStr(tbl.Cells(r, 7).Value)), 3)) = "log")
            If pLog(pN) And pMin(pN) <= 0 Then Err.Raise vbObjectError + 723, "Parameters", "A log-scale range must be above zero for " & tbl.Cells(r, 2).Value & "."
            If Left$(key, 4) = "KSAT" Then hasKsat = True
            If key = "KMULT" Then hasMult = True
            If Left$(key, 4) = "KSAT" Then
                If CInt(Right$(key, 1)) > LayerCount(genLines) Then Err.Raise vbObjectError + 724, "Parameters", tbl.Cells(r, 2).Value & ": the project has only " & LayerCount(genLines) & " layer(s)."
            End If
            If (key = "SEEPK" Or key = "SEEPH") And Not SeepOn(genLines) Then Err.Raise vbObjectError + 725, "Parameters", "Vertical (deep) seepage is off in this project; turn it on in DRAINMOD or untick " & tbl.Cells(r, 2).Value & "."
            If key = "LSEEPT" And Not SeepLineOn(genLines, 3) Then Err.Raise vbObjectError + 725, "Parameters", "Lateral seepage is off in this project; turn it on in DRAINMOD or untick " & tbl.Cells(r, 2).Value & "."
        End If
    Next r
    If hasKsat And hasMult Then Err.Raise vbObjectError + 726, "Parameters", "Use either the layer Ksat values or the Ksat multiplier, not both."
    ' drain geometry: the drain must stay above the impermeable layer
    For i = 1 To pN
        If pKey(i) = "DDEPTH" Then iD = i
        If pKey(i) = "DIMP" Then iH = i
    Next i
    If iD > 0 Then
        If pMin(iD) <= 0 Then Err.Raise vbObjectError + 728, "Parameters", "The minimum drain depth must be above zero."
    End If
    For i = 1 To pN
        If pKey(i) = "DSPACE" And pMin(i) <= 0 Then Err.Raise vbObjectError + 736, "Parameters", "The minimum drain spacing must be above zero."
    Next i
    If iD > 0 And iH > 0 Then
        If pMax(iD) >= pMin(iH) Then Err.Raise vbObjectError + 729, "Parameters", "The maximum drain depth (" & pMax(iD) & _
            " cm) must be less than the minimum depth to the impermeable layer (" & pMin(iH) & " cm)."
    ElseIf iD > 0 Then
        If pMax(iD) >= ImpDepth(genLines) Then Err.Raise vbObjectError + 729, "Parameters", "The maximum drain depth (" & pMax(iD) & _
            " cm) must be less than the depth to the impermeable layer (" & ImpDepth(genLines) & " cm), or calibrate that depth as well."
    ElseIf iH > 0 Then
        If pMin(iH) <= DrainField(genLines, 1) Then Err.Raise vbObjectError + 727, "Parameters", "The minimum depth to the impermeable layer (" & _
            pMin(iH) & " cm) must be greater than the drain depth (" & DrainField(genLines, 1) & " cm)."
    End If
    CheckKirkRange
End Sub

Private Function TargetMode() As Integer
    ' 1 = drain flow, 2 = water table depth, 3 = both
    Dim t As String
    t = LCase$(CStr(Cfg("Target").Value))
    If Left$(t, 4) = "both" Then
        TargetMode = 3
    ElseIf InStr(t, "water") > 0 Then
        TargetMode = 2
    Else
        TargetMode = 1
    End If
End Function

Private Function FlowFactor() As Double
    ' measured flow units -> cm/day (DRAINMOD)
    Select Case LCase$(Trim$(CStr(Cfg("FlowUnits").Value)))
        Case "mm/day": FlowFactor = 0.1
        Case "in/day": FlowFactor = 2.54
        Case Else: FlowFactor = 1
    End Select
End Function

Private Function WtdFactor() As Double
    ' measured water table depth units -> cm (DRAINMOD)
    Select Case LCase$(Trim$(CStr(Cfg("WtdUnits").Value)))
        Case "mm": WtdFactor = 0.1
        Case "m": WtdFactor = 100
        Case "in": WtdFactor = 2.54
        Case "ft": WtdFactor = 30.48
        Case Else: WtdFactor = 1
    End Select
    If InStr(LCase$(CStr(Cfg("WtdSign").Value)), "negative") > 0 Then WtdFactor = -WtdFactor
End Function

' a date cell as a whole day number (the time of day, if any, is dropped;
' CLng would round noon to the nearest even day)
Private Function DayOf(ByVal v As Variant, ByRef d As Long) As Boolean
    If IsError(v) Then Exit Function
    If IsEmpty(v) Then Exit Function
    If Not IsDate(v) Then Exit Function
    d = CLng(Int(CDbl(CDate(v))))
    DayOf = True
End Function

' a measured value: False for blanks, text and error cells such as #N/A
Private Function CellNum(ByVal v As Variant, ByRef x As Double) As Boolean
    If IsError(v) Then Exit Function
    If IsEmpty(v) Then Exit Function
    If VarType(v) = vbString Then
        If Trim$(v) = "" Then Exit Function
    End If
    If Not IsNumeric(v) Then Exit Function
    x = CDbl(v)
    CellNum = True
End Function

' measured data inside the calibration window, sorted by date
Private Sub LoadMeasured()
    Dim ws As Worksheet, last As Long, r As Long, d As Long, c1 As Long, c2 As Long, mode As Integer
    Dim nq As Long, nw As Long, fq As Double, fw As Double, x As Double, vals As Variant
    Dim i As Long, j As Long, td As Long, tq As Double, tw As Double, thq As Boolean, thw As Boolean
    Set ws = ThisWorkbook.Worksheets("Measured")
    If Not DayOf(Cfg("CalStart").Value, c1) Or Not DayOf(Cfg("CalEnd").Value, c2) Then Err.Raise vbObjectError + 730, "Measured", "Enter the calibration start and end dates on the Setup sheet."
    If c2 <= c1 Then Err.Raise vbObjectError + 731, "Measured", "The calibration end date must be after the start date."
    mode = TargetMode(): fq = FlowFactor(): fw = WtdFactor()
    last = ws.Cells(ws.Rows.Count, 1).End(xlUp).Row
    If last < 2 Then last = 2
    ReDim oDate(1 To last): ReDim oQ(1 To last): ReDim oW(1 To last): ReDim oHasQ(1 To last): ReDim oHasW(1 To last)
    vals = ws.Range(ws.Cells(1, 1), ws.Cells(last, 3)).Value
    nObs = 0
    For r = 2 To last
        If DayOf(vals(r, 1), d) Then
            If d >= c1 And d <= c2 Then
                nObs = nObs + 1
                oDate(nObs) = d
                If CellNum(vals(r, 2), x) Then oQ(nObs) = x * fq: oHasQ(nObs) = True: nq = nq + 1
                If CellNum(vals(r, 3), x) Then oW(nObs) = x * fw: oHasW(nObs) = True: nw = nw + 1
            End If
        End If
    Next r
    ' the comparison with the simulation walks both series forward, so sort
    ' by date (insertion sort: fast when the data are already nearly in order)
    For i = 2 To nObs
        If oDate(i) < oDate(i - 1) Then
            td = oDate(i): tq = oQ(i): tw = oW(i): thq = oHasQ(i): thw = oHasW(i)
            j = i - 1
            Do While j >= 1
                If oDate(j) <= td Then Exit Do
                oDate(j + 1) = oDate(j): oQ(j + 1) = oQ(j): oW(j + 1) = oW(j)
                oHasQ(j + 1) = oHasQ(j): oHasW(j + 1) = oHasW(j)
                j = j - 1
            Loop
            oDate(j + 1) = td: oQ(j + 1) = tq: oW(j + 1) = tw: oHasQ(j + 1) = thq: oHasW(j + 1) = thw
        End If
    Next i
    For i = 2 To nObs
        If oDate(i) = oDate(i - 1) Then Err.Raise vbObjectError + 734, "Measured", _
            "The date " & Format$(CDate(oDate(i)), "yyyy-mm-dd") & " appears more than once on the Measured sheet. Keep one row per day."
    Next i
    If (mode = 1 Or mode = 3) And nq < 10 Then Err.Raise vbObjectError + 732, "Measured", "Fewer than 10 measured drain flow values fall in the calibration period."
    If (mode = 2 Or mode = 3) And nw < 10 Then Err.Raise vbObjectError + 733, "Measured", "Fewer than 10 measured water table depths fall in the calibration period."
End Sub

'========================================================= running ====
' run slot w writes <name>_cal.gen/.prj (slot 1) or <name>_cal<w>.gen/.prj
Private Function WorkSuffix(ByVal w As Integer) As String
    If w > 1 Then WorkSuffix = "_cal" & w Else WorkSuffix = "_cal"
End Function

Private Function WorkGenPath(Optional ByVal w As Integer = 1) As String
    WorkGenPath = FolderOf(CStr(Cfg("GenFile").Value)) & BaseName(CStr(Cfg("GenFile").Value)) & WorkSuffix(w) & ".gen"
End Function

Private Function WorkPrjPath(Optional ByVal w As Integer = 1) As String
    WorkPrjPath = FolderOf(CStr(Cfg("PrjFile").Value)) & BaseName(CStr(Cfg("PrjFile").Value)) & WorkSuffix(w) & ".prj"
End Function

' DRAINMOD reads crop (and some other) file names in 48 characters, so a long
' path, e.g. C:\Users\...\Downloads\DDESIGN\crops\soybeans.cin, is cut short
' and that crop silently left out. In the run's .prj such a path is written
' relative to the folder DRAINMOD runs in (the program's folder), or else as
' the Windows short (8.3) name.
Private Function FitName(ByVal p As String) As String
    Dim r As String, sh As String
    FitName = p
    If Len(p) <= MAXNAME Then Exit Function
    r = RelPath(p, FolderOf(Trim$(CStr(Cfg("DmExe").Value))))
    If r <> "" And Len(r) < Len(FitName) Then FitName = r
    If Len(FitName) <= MAXNAME Then Exit Function
    sh = ShortName(p)
    If sh <> "" And Len(sh) < Len(FitName) Then FitName = sh
End Function

' p relative to the folder base ("" if they are on different drives)
Private Function RelPath(ByVal p As String, ByVal baseDir As String) As String
    Dim a As Variant, b As Variant, i As Long, n As Long, s As String
    If baseDir = "" Or Mid$(p, 2, 1) <> ":" Then Exit Function
    If LCase$(Left$(p, 2)) <> LCase$(Left$(baseDir, 2)) Then Exit Function
    If Right$(baseDir, 1) = "\" Then baseDir = Left$(baseDir, Len(baseDir) - 1)
    a = Split(p, "\")
    b = Split(baseDir, "\")
    i = 0
    Do While i < UBound(a) And i <= UBound(b)
        If LCase$(a(i)) <> LCase$(b(i)) Then Exit Do
        i = i + 1
    Loop
    For n = i To UBound(b)
        s = s & "..\"
    Next n
    For n = i To UBound(a)
        s = s & a(n)
        If n < UBound(a) Then s = s & "\"
    Next n
    RelPath = s
End Function

' the Windows short (8.3) form of an existing path ("" if there is none)
Private Function ShortName(ByVal p As String) As String
    Dim buf As String, n As Long
    On Error Resume Next
    buf = String$(520, vbNullChar)
    n = GetShortPathName(p, buf, 520)
    If n > 0 And n < 520 Then ShortName = Left$(buf, n)
End Function

' a .prj line with a long file path rewritten so DRAINMOD can read it
Private Function FitPrjLine(ByVal ln As String) As String
    Dim eq As Long, pk As String, pv As String, nv As String
    FitPrjLine = ln
    eq = InStr(ln, "=")
    If eq = 0 Or Left$(Trim$(ln), 1) = "[" Then Exit Function
    pk = LCase$(Trim$(Left$(ln, eq - 1)))
    pv = Trim$(Mid$(ln, eq + 1))
    If pk = "hydrology" Or Len(pv) <= MAXNAME Then Exit Function
    If Not (Mid$(pv, 2, 1) = ":" Or Left$(pv, 2) = "\\") Then Exit Function
    nv = FitName(pv)
    If pk Like "crop#*" And Len(nv) > MAXNAME Then Err.Raise vbObjectError + 760, "Project", _
        "The crop file " & pv & " has a longer path than DRAINMOD can read (" & MAXNAME & " characters), and no shorter form of it fits. " & _
        "Move the project folder to a shorter path (for example C:\DDESIGN) or put dmhydro.exe in the project's Program folder."
    FitPrjLine = Left$(ln, eq) & nv
End Function

' value of a selected parameter in x(), if it is selected
Private Function KeyValue(ByVal key As String, x() As Double, ByRef v As Double) As Boolean
    Dim i As Integer
    For i = 1 To pN
        If pKey(i) = key Then
            v = x(i)
            KeyValue = True
            Exit Function
        End If
    Next i
End Function

' Kirkham's depth for flow to drains (surface depth that must be ponded before
' surface water flows to the drains) cannot exceed the maximum surface storage.
' Brings x() (natural units) inside that limit: with both calibrated the
' Kirkham depth is held at or below the surface storage; with only one of them
' calibrated it is kept on the right side of the project's value of the other.
Private Function LimitKirk(x() As Double) As Boolean
    Dim iK As Integer, iSm As Integer, i As Integer
    For i = 1 To pN
        If pKey(i) = "KIRK" Then iK = i
        If pKey(i) = "SMAX" Then iSm = i
    Next i
    If iK > 0 And iSm > 0 Then
        If x(iK) > x(iSm) Then x(iK) = x(iSm): LimitKirk = True
    ElseIf iK > 0 Then
        If x(iK) > DrainField(genLines, 4) Then x(iK) = DrainField(genLines, 4): LimitKirk = True
    ElseIf iSm > 0 Then
        If x(iSm) < DrainField(genLines, 6) Then x(iSm) = DrainField(genLines, 6): LimitKirk = True
    End If
End Function

' error if no value in the ranges can meet Kirkham depth <= surface storage
Private Sub CheckKirkRange()
    Dim iK As Integer, iSm As Integer, i As Integer
    For i = 1 To pN
        If pKey(i) = "KIRK" Then iK = i
        If pKey(i) = "SMAX" Then iSm = i
    Next i
    If iK > 0 And iSm > 0 Then
        If pMin(iK) > pMax(iSm) Then Err.Raise vbObjectError + 737, "Parameters", "The minimum Kirkham depth (" & pMin(iK) & _
            " cm) is above the maximum surface storage (" & pMax(iSm) & " cm); the Kirkham depth cannot exceed the surface storage."
    ElseIf iK > 0 Then
        If pMin(iK) > DrainField(genLines, 4) Then Err.Raise vbObjectError + 737, "Parameters", "The minimum Kirkham depth (" & pMin(iK) & _
            " cm) is above the project's maximum surface storage (" & DrainField(genLines, 4) & " cm); lower it or calibrate the surface storage too."
    ElseIf iSm > 0 Then
        If pMax(iSm) < DrainField(genLines, 6) Then Err.Raise vbObjectError + 737, "Parameters", "The maximum surface storage (" & pMax(iSm) & _
            " cm) is below the project's Kirkham depth (" & DrainField(genLines, 6) & " cm); raise it or calibrate the Kirkham depth too."
    End If
End Sub

' write a .gen/.prj pair with parameter values x() (natural units)
Private Sub WriteProject(xIn() As Double, ByVal genOut As String, ByVal prjOut As String, ByVal shorten As Boolean)
    Dim g() As String, i As Long, k0() As Double, n As Integer, nl As Integer, lines As Variant, txt As String
    Dim y1 As Long, m1 As Long, y2 As Long, m2 As Long, cy1 As Long, cy2 As Long
    Dim l As String, v As Double, parts As Variant, x() As Double
    x = xIn
    LimitKirk x
    ReDim g(0 To nGen - 1)
    For i = 0 To nGen - 1
        g(i) = genLines(i)
    Next i
    ' original Ksat of every layer (the multiplier scales these)
    nl = LayerCount(g)
    If nl < 1 Then ReDim k0(1 To 1) Else ReDim k0(1 To nl)
    For n = 1 To nl
        k0(n) = LayerK(g, n)
    Next n
    For i = 1 To pN
        ApplyParam g, pKey(i), x(i), k0
    Next i
    ' equivalent depth and Kirkham's G follow the drain geometry
    For i = 1 To pN
        If pKey(i) = "RADIUS" Or pKey(i) = "DDEPTH" Or pKey(i) = "DIMP" Or pKey(i) = "DSPACE" Then
            RecomputeDrainGeometry g
            Exit For
        End If
    Next i
    If shorten Then
        ' run only the warm-up years plus the calibration period
        ClimatePeriod g, y1, m1, y2, m2
        cy1 = Year(CDate(Cfg("CalStart").Value)) - Val(Cfg("WarmupYears").Value)
        cy2 = Year(CDate(Cfg("CalEnd").Value))
        If cy1 < y1 Then cy1 = y1
        If cy2 > y2 Then cy2 = y2
        If cy2 >= cy1 Then
            If cy1 > y1 Then m1 = 1
            If cy2 < y2 Then m2 = 12
            SetClimatePeriod g, cy1, m1, cy2, m2
        End If
    End If
    txt = Join(g, vbCrLf) & vbCrLf
    WriteAllText genOut, txt
    ' project file: same as the user's, pointing at the new .gen
    lines = SplitLines(prjText)
    For i = 0 To UBound(lines)
        lines(i) = FitPrjLine(MapText(CStr(lines(i))))  ' paths of where the project is now, short enough for DRAINMOD
        l = LCase$(Trim$(lines(i)))
        If Right$(l, 4) = ".gen" And InStr(l, "=") > 0 Then
            lines(i) = Left$(lines(i), InStr(lines(i), "=")) & genOut
        ' DRAINMOD 7 also keeps these in the [Analysis] section of the .prj
        ElseIf Left$(l, 7) = "dstmax=" And KeyValue("SMAX", x, v) Then
            lines(i) = "DStmax=" & RJ(NumStr(v, 2), 6)
        ElseIf Left$(l, 8) = "dstorro=" And KeyValue("KIRK", x, v) Then
            lines(i) = "DStorro=" & RJ(NumStr(v, 2), 6)
        ElseIf Left$(l, 11) = "draindepth=" And KeyValue("DDEPTH", x, v) Then
            parts = Split(Mid$(Trim$(lines(i)), 12), ",")
            If UBound(parts) >= 2 Then lines(i) = "DrainDepth=" & RJ(NumStr(v, 2), Len(parts(0))) & "," & RJ(NumStr(v, 2), Len(parts(1))) & "," & parts(2)
        ElseIf Left$(l, 11) = "drainspace=" And KeyValue("DSPACE", x, v) Then
            parts = Split(Mid$(Trim$(lines(i)), 12), ",")
            If UBound(parts) >= 2 Then lines(i) = "DrainSpace=" & RJ(NumStr(v, 2), Len(parts(0))) & "," & RJ(NumStr(v, 2), Len(parts(1))) & "," & parts(2)
        End If
    Next i
    WriteAllText prjOut, Join(lines, vbCrLf) & vbCrLf
End Sub

' start DRAINMOD for run slot w without waiting for it
Private Sub StartRun(ByVal w As Integer, ByVal prjPath As String)
    Dim exe As String, cmd As String, pid As Double
    exe = Trim$(CStr(Cfg("DmExe").Value))
    If exe = "" Or Dir(exe) = "" Then Err.Raise vbObjectError + 740, "DRAINMOD", "Select dmhydro.exe on the Setup sheet."
    On Error Resume Next
    ChDrive Left$(exe, 1)
    ChDir FolderOf(exe)
    On Error GoTo 0
    cmd = """" & exe & """ "
    If InStr(prjPath, " ") > 0 Then cmd = cmd & """" & prjPath & """" Else cmd = cmd & prjPath
    wStart(w) = Now
    pid = Shell(cmd, vbHide)
    wHandle(w) = OpenProcess(SYNC_TERMINATE, 0, CLng(pid))
End Sub

' wait until run slots 1..nb have finished; a run longer than the time limit
' on the Setup sheet is stopped and counted as failed
Private Sub WaitRuns(ByVal nb As Integer)
    Dim w As Integer, busy As Integer, limit As Double
    limit = CfgOpt("RunTimeout", 10)
    If limit <= 0 Then limit = 10
    Do
        busy = 0
        For w = 1 To nb
            If wHandle(w) <> 0 Then
                If WaitForSingleObject(wHandle(w), 0) <> WAIT_TIMEOUT Then
                    CloseHandle wHandle(w)
                    wHandle(w) = 0
                ElseIf (Now - wStart(w)) * 1440 > limit Then
                    TerminateProcess wHandle(w), 1
                    CloseHandle wHandle(w)
                    wHandle(w) = 0
                    wFail(w) = "DRAINMOD was still running after " & limit & " minutes and was stopped (time limit on the Setup sheet)."
                ElseIf busy = 0 Then
                    busy = w
                End If
            End If
        Next w
        If busy = 0 Then Exit Do
        WaitForSingleObject wHandle(busy), 100
        DoEvents
    Loop
End Sub

' stop every DRAINMOD run still going
Private Sub KillRuns()
    Dim w As Integer
    On Error Resume Next
    For w = 1 To NWMAX
        If wHandle(w) <> 0 Then
            TerminateProcess wHandle(w), 1
            CloseHandle wHandle(w)
            wHandle(w) = 0
        End If
    Next w
End Sub

' daily output file of run slot w: the slot-1 file with the slot's project name.
' "" if the output name does not follow the project name. With strict, also ""
' for slot 1 then (so a file the user picked is never deleted).
Private Function WorkerOutFile(ByVal w As Integer, Optional ByVal strict As Boolean = False) As String
    Dim p As String, fn As String, b As String, ext As String, nb As String
    p = Trim$(CStr(Cfg("OutFile").Value))
    If p = "" Then Exit Function
    fn = Mid$(p, InStrRev(p, "\") + 1)
    If InStrRev(fn, ".") > 0 Then
        ext = Mid$(fn, InStrRev(fn, "."))
        b = Left$(fn, InStrRev(fn, ".") - 1)
    Else
        b = fn
    End If
    If LCase$(b) = LCase$(BaseName(WorkPrjPath(1))) Then
        nb = BaseName(WorkPrjPath(w))
    ElseIf LCase$(b) = LCase$(BaseName(WorkGenPath(1))) Then
        nb = BaseName(WorkGenPath(w))
    Else
        If w = 1 And Not strict Then WorkerOutFile = p
        Exit Function
    End If
    WorkerOutFile = FolderOf(p) & nb & ext
End Function

' write run slot w's project with values x() and start DRAINMOD on it
Private Sub LaunchRun(ByVal w As Integer, x() As Double)
    Dim op As String
    If wHandle(w) <> 0 Then
        TerminateProcess wHandle(w), 1
        CloseHandle wHandle(w)
        wHandle(w) = 0
    End If
    wFail(w) = ""
    WriteProject x, WorkGenPath(w), WorkPrjPath(w), IsYes(Cfg("ShortenRun").Value)
    ' remove the slot's previous output, so only this run's output can be read
    op = WorkerOutFile(w, True)
    If op <> "" Then
        If Dir(op) <> "" Then
            On Error Resume Next
            Kill op
            On Error GoTo 0
        End If
    End If
    StartRun w, WorkPrjPath(w)
End Sub

' as LaunchRun, but a failure (bad values, ...) is noted for the slot instead of
' stopping the calibration; Esc (error 18) is passed on
Private Sub SafeLaunch(ByVal w As Integer, x() As Double)
    On Error GoTo Failed
    LaunchRun w, x
    Exit Sub
Failed:
    If Err.Number = 18 Then Err.Raise 18
    wFail(w) = Err.Description
    If wFail(w) = "" Then wFail(w) = "error " & Err.Number
End Sub

' objective of run slot w after it finished; -1E+30 if it failed
Private Function CollectRun(ByVal w As Integer) As Double
    On Error GoTo Failed
    If wFail(w) <> "" Then GoTo Bad
    If Not WaitFileDone(WorkerOutFile(w)) Then
        wFail(w) = "DRAINMOD did not finish writing " & WorkerOutFile(w)
        GoTo Bad
    End If
    ReadDailyOutput WorkerOutFile(w), wStart(w), WorkGenPath(w)
    CollectRun = Objective()
    If CollectRun <= -1E+29 Then wFail(w) = "no KGE (too few matching days, or no variation in the data)"
    Exit Function
Failed:
    If Err.Number = 18 Then Err.Raise 18
    wFail(w) = Err.Description
    If wFail(w) = "" Then wFail(w) = "error " & Err.Number
Bad:
    stQ(9) = 0: stW(9) = 0
    CollectRun = -1E+30
End Function

' number of run slots to use; note says why parallel runs are off, if they are
Private Function ParallelCount(ByRef note As String) As Integer
    Dim p As Integer
    p = CfgOpt("ParallelRuns", 1)
    If p < 1 Then p = 1
    If p > NWMAX Then p = NWMAX
    If p > 1 And WorkerOutFile(2) = "" Then
        note = "Parallel runs are off: the daily output file (" & CStr(Cfg("OutFile").Value) & _
            ") is not named after the project, so runs side by side would overwrite each other's output."
        p = 1
    End If
    ParallelCount = p
End Function

' number of run slots to use: ParallelRuns, after every slot has rerun x() and
' reproduced the single run's objective f0 exactly; note says why it is 1
Private Function CheckParallel(x() As Double, ByVal f0 As Double, ByRef note As String) As Integer
    Dim n As Integer, w As Integer, z As Double, ok As Boolean
    n = ParallelCount(note)
    If n > 1 Then
        SetStatus "Checking " & n & " DRAINMOD runs side by side ..."
        For w = 1 To n
            SafeLaunch w, x
        Next w
        WaitRuns n
        ok = True
        For w = 1 To n
            z = CollectRun(w)
            If wFail(w) <> "" And InStr(wFail(w), "no KGE") = 0 Then
                ok = False: note = "run slot " & w & ": " & wFail(w)
            ElseIf Abs(z - f0) > 0.000001 Then
                ok = False: note = "run slot " & w & " gave a different result than the single run"
            End If
        Next w
        If Not ok Then
            note = "Parallel runs are off: DRAINMOD runs side by side did not reproduce the single run (" & note & ")."
            n = 1
        End If
    End If
    CheckParallel = n
End Function

' daily output folder from the .gen "Printout and Input Control" line
Private Function OutputFolder() As String
    Dim k As Long, ln As String, p As Long
    k = FindSection(genLines, "Printout")
    If k >= 0 Then
        ln = genLines(k + 1)
        p = InStr(ln, ":\")
        If p > 1 Then
            OutputFolder = Trim$(Mid$(ln, p - 1))
        ElseIf InStr(ln, "\\") > 0 Then
            OutputFolder = Trim$(Mid$(ln, InStr(ln, "\\")))
        End If
    End If
    If OutputFolder = "" Then OutputFolder = FolderOf(CStr(Cfg("PrjFile").Value))
    If Right$(OutputFolder, 1) <> "\" Then OutputFolder = OutputFolder & "\"
End Function

Private Function CountDataLines(ByVal path As String) As Long
    Dim lines As Variant, i As Long, n As Long
    On Error GoTo Done
    If FileLen(path) > 200000000 Then Exit Function
    lines = SplitLines(ReadAllText(path))
    For i = 0 To UBound(lines)
        If IsDataLine(CStr(lines(i))) Then n = n + 1
    Next i
Done:
    CountDataLines = n
End Function

Private Function IsDataLine(ByVal ln As String) As Boolean
    Dim t As Variant, j As Integer
    t = Tokens(ln)
    If UBound(t) < 7 Then Exit Function
    For j = 0 To 7
        If Not IsNumeric(t(j)) Then Exit Function
    Next j
    IsDataLine = True
End Function

' find the daily output written by the last run
Private Sub DetectOutputFile()
    Dim folder As String, f As String, best As String, bestN As Long, n As Long, ext As String, score As Long, bestScore As Long
    folder = OutputFolder()
    f = Dir(folder & "*.*")
    Do While f <> ""
        If FileDateTime(folder & f) >= runStart - TimeSerial(0, 0, 5) Then
            n = CountDataLines(folder & f)
            If n >= 300 Then
                ext = LCase$(Mid$(f, InStrRev(f, ".") + 1))
                score = n
                If ext = "plt" Or InStr(ext, "day") > 0 Or InStr(ext, "dly") > 0 Or ext = "dy" Then score = score + 10000000
                If score > bestScore Then bestScore = score: best = folder & f
            End If
        End If
        f = Dir
    Loop
    If best = "" Then Err.Raise vbObjectError + 741, "DRAINMOD", "No daily output file was written in " & folder & _
        ". Turn on daily output in the DRAINMOD project, or select the output file on the Setup sheet."
    Cfg("OutFile").Value = best
End Sub

' ---- dates in the daily output
' a token that is a whole number (digits only)
Private Function IsIntTok(ByVal s As String) As Boolean
    IsIntTok = (Len(s) > 0 And Not (s Like "*[!0-9]*"))
End Function

' a valid calendar date from year, month and day tokens
Private Function YmdDate(ByVal ys As String, ByVal ms As String, ByVal ds As String, ByRef d As Long) As Boolean
    Dim y As Long, m As Long, dd As Long
    If Not (IsIntTok(ys) And IsIntTok(ms) And IsIntTok(ds)) Then Exit Function
    If Len(ys) > 5 Or Len(ms) > 3 Or Len(ds) > 3 Then Exit Function
    y = CLng(ys): m = CLng(ms): dd = CLng(ds)
    If y < 1900 Or y > 2200 Or m < 1 Or m > 12 Or dd < 1 Or dd > 31 Then Exit Function
    If Day(DateSerial(y, m, dd)) <> dd Then Exit Function
    d = CLng(DateSerial(y, m, dd))
    YmdDate = True
End Function

' date of one daily line in the given layout:
'   decyear = year + (day of year - 1) / days in year, as in DRAINMOD's .PLT
'             (e.g. 1980.0030 = 2 Jan 1980; 3 decimals are enough to tell days apart)
'   ymd, mdy, dmy = year, month and day in the first three columns
'   yj = year and day of year in the first two columns
Private Function LineDate(t As Variant, ByVal fmt As String, ByRef d As Long) As Boolean
    Dim v As Double, y As Long, n As Long, doy As Long, s As String
    Select Case fmt
        Case "decyear"
            s = CStr(t(0))
            If Not (s Like "*#.#*") Then Exit Function
            v = Val(s): y = Int(v)
            If y < 1900 Or y > 2200 Then Exit Function
            n = CLng(DateSerial(y + 1, 1, 1)) - CLng(DateSerial(y, 1, 1))
            doy = Int((v - y) * n + 0.5) + 1
            If doy < 1 Or doy > n Then Exit Function
            d = CLng(DateSerial(y, 1, 1)) + doy - 1
            LineDate = True
        Case "ymd": LineDate = YmdDate(CStr(t(0)), CStr(t(1)), CStr(t(2)), d)
        Case "mdy": LineDate = YmdDate(CStr(t(2)), CStr(t(0)), CStr(t(1)), d)
        Case "dmy": LineDate = YmdDate(CStr(t(2)), CStr(t(1)), CStr(t(0)), d)
        Case "yj"
            If Not (IsIntTok(CStr(t(0))) And IsIntTok(CStr(t(1)))) Then Exit Function
            y = CLng(t(0)): doy = CLng(t(1))
            If y < 1900 Or y > 2200 Then Exit Function
            n = CLng(DateSerial(y + 1, 1, 1)) - CLng(DateSerial(y, 1, 1))
            If doy < 1 Or doy > n Then Exit Function
            d = CLng(DateSerial(y, 1, 1)) + doy - 1
            LineDate = True
    End Select
End Function

' the date layout of the output: the first one that gives a valid date on
' each of the first 40 lines, one day apart; "seq" if none does
Private Function DateLayout(dl() As String, ByVal n As Long) As String
    Dim fmts As Variant, f As Variant, i As Long, m As Long, d As Long, dPrev As Long, ok As Boolean
    fmts = Array("decyear", "ymd", "mdy", "dmy", "yj")
    m = n: If m > 40 Then m = 40
    For Each f In fmts
        ok = True
        For i = 1 To m
            If Not LineDate(Tokens(dl(i)), CStr(f), d) Then ok = False: Exit For
            If i > 1 And d <> dPrev + 1 Then ok = False: Exit For
            dPrev = d
        Next i
        If ok Then
            DateLayout = CStr(f)
            Exit Function
        End If
    Next f
    DateLayout = "seq"
End Function

' read the daily output: drain flow = column 5, water table depth = column 8
Private Sub ReadDailyOutput(ByVal path As String, ByVal t0 As Date, ByVal genPath As String)
    Dim lines As Variant, i As Long, t As Variant, n As Long, d0 As Long, nDays As Long
    Dim y1 As Long, m1 As Long, y2 As Long, m2 As Long, g() As String, fmt As String, dl() As String
    If path = "" Or Dir(path) = "" Then Err.Raise vbObjectError + 742, "DRAINMOD", "The daily output file was not found: " & path
    If FileDateTime(path) < t0 - TimeSerial(0, 0, 5) Then Err.Raise vbObjectError + 743, "DRAINMOD", _
        "DRAINMOD did not write a new daily output file (" & path & "). Run the project in DRAINMOD to check it for errors."
    lines = SplitLines(ReadAllText(path))
    If UBound(lines) < 0 Then Err.Raise vbObjectError + 745, "DRAINMOD", "The daily output file is empty: " & path
    ' the data lines (header and summary lines are skipped)
    ReDim dl(1 To UBound(lines) + 1)
    n = 0
    For i = 0 To UBound(lines)
        If IsDataLine(CStr(lines(i))) Then
            n = n + 1
            dl(n) = CStr(lines(i))
        End If
    Next i
    sN = n
    If sN = 0 Then Err.Raise vbObjectError + 744, "DRAINMOD", "No daily values found in " & path
    ReDim sDate(1 To sN): ReDim sQ(1 To sN): ReDim sW(1 To sN)
    ' start and length of the run
    g = ReadGen(genPath)
    ClimatePeriod g, y1, m1, y2, m2
    d0 = CLng(DateSerial(y1, m1, 1))
    nDays = CLng(DateSerial(y2, m2 + 1, 1)) - d0
    fmt = DateLayout(dl, sN)
    For i = 1 To sN
        t = Tokens(dl(i))
        If fmt = "seq" Then
            sDate(i) = d0 + i - 1
        ElseIf Not LineDate(t, fmt, sDate(i)) Then
            Err.Raise vbObjectError + 747, "DRAINMOD", "Line " & i & " of the daily values in " & path & _
                " does not start with a date like the lines before it: " & dl(i)
        ElseIf i > 1 Then
            If sDate(i) <= sDate(i - 1) Then Err.Raise vbObjectError + 748, "DRAINMOD", _
                "The dates in " & path & " go backwards or repeat at " & Format$(CDate(sDate(i)), "yyyy-mm-dd") & _
                ". Select the daily output file on the Setup sheet."
        End If
        sQ(i) = Val(t(4))
        sW(i) = Val(t(7))
    Next i
    If fmt = "seq" And sN <> nDays Then Err.Raise vbObjectError + 746, "DRAINMOD", _
        path & " has " & sN & " daily lines but the run covers " & nDays & _
        " days, so the dates cannot be matched. Select the daily output file on the Setup sheet."
End Sub

Private Function ReadGen(ByVal path As String) As String()
    Dim lines As Variant, g() As String, i As Long
    lines = SplitLines(ReadAllText(path))
    ReDim g(0 To UBound(lines))
    For i = 0 To UBound(lines)
        g(i) = lines(i)
    Next i
    ReadGen = g
End Function

'=============================================================== KGE ====
' st(): 1 KGE, 2 r, 3 alpha (or gamma), 4 beta, 5 NSE, 6 PBIAS %, 7 RMSE, 8 n, 9 valid(1/0)
' Setup: leave out days when measured and simulated drain flow are both zero
Private Function ZeroFlowSkip() As Boolean
    On Error Resume Next
    ZeroFlowSkip = IsYes(ThisWorkbook.Names("ZeroFlowSkip").RefersToRange.Value)
End Function

' Setup: depth of the water table measuring instrument (cm below the surface);
' the water table statistics use only days when the simulated depth is at or
' above it. 0 = no limit
Private Function InstrDepth() As Double
    Dim v As Variant
    On Error Resume Next
    v = ThisWorkbook.Names("InstrDepth").RefersToRange.Value
    If IsNumeric(v) And Not IsEmpty(v) Then
        If v > 0 Then InstrDepth = v
    End If
End Function

Private Sub Stats(ByVal useW As Boolean, st() As Double)
    Dim i As Long, j As Long, n As Long, so As Double, ss As Double, soo As Double, sss As Double, sos As Double
    Dim mo As Double, ms As Double, vo As Double, vs As Double, cov As Double, r As Double, a As Double, b As Double
    Dim o As Double, sim As Double, sse As Double, sst As Double, ver As String, skipZero As Boolean, lim As Double
    j = 1
    For i = 1 To 9: st(i) = 0: Next i
    If useW Then lim = InstrDepth() Else skipZero = ZeroFlowSkip()
    For i = 1 To nObs
        If (useW And oHasW(i)) Or (Not useW And oHasQ(i)) Then
            Do While j < sN And sDate(j) < oDate(i)
                j = j + 1
            Loop
            If sDate(j) = oDate(i) Then
                If useW Then o = oW(i): sim = sW(j) Else o = oQ(i): sim = sQ(j)
                ' optionally leave out days with no measured and no simulated drain flow,
                ' and days when the simulated water table is below the measuring instrument
                If Not (skipZero And Abs(o) < ZEROQ And Abs(sim) < ZEROQ) And Not (lim > 0 And sim > lim) Then
                    n = n + 1
                    so = so + o: ss = ss + sim: soo = soo + o * o: sss = sss + sim * sim: sos = sos + o * sim
                    sse = sse + (sim - o) ^ 2
                End If
            End If
        End If
    Next i
    st(8) = n
    If n < 3 Then Exit Sub
    mo = so / n: ms = ss / n
    vo = soo / n - mo * mo: vs = sss / n - ms * ms: cov = sos / n - mo * ms
    If vo <= 0 Or mo = 0 Then Exit Sub
    If vs <= 0 Then r = 0 Else r = cov / Sqr(vo * vs)
    b = ms / mo
    ver = Trim$(CStr(Cfg("KgeVersion").Value))
    If InStr(ver, "2009") = 0 Then         ' 2012 (CV ratio) unless 2009 is chosen
        If ms = 0 Then a = 0 Else a = (Sqr(Abs(vs)) / ms) / (Sqr(vo) / mo)
    Else
        a = Sqr(Abs(vs)) / Sqr(vo)
    End If
    st(1) = 1 - Sqr((r - 1) ^ 2 + (a - 1) ^ 2 + (b - 1) ^ 2)
    st(2) = r: st(3) = a: st(4) = b
    sst = vo * n
    st(5) = 1 - sse / sst
    st(6) = 100 * (ss - so) / so
    st(7) = Sqr(sse / n)
    st(9) = 1
End Sub

Private Function Objective() As Double
    Dim mode As Integer, w As Double
    mode = TargetMode()
    Stats False, stQ
    Stats True, stW
    w = Val(Cfg("WFlow").Value)
    If w < 0 Then w = 0
    If w > 1 Then w = 1
    Select Case mode
        Case 1: If stQ(9) = 1 Then Objective = stQ(1) Else Objective = -1E+30
        Case 2: If stW(9) = 1 Then Objective = stW(1) Else Objective = -1E+30
        Case Else
            If stQ(9) = 1 And stW(9) = 1 Then Objective = w * stQ(1) + (1 - w) * stW(1) Else Objective = -1E+30
    End Select
End Function

' seconds since t (a Timer value), across midnight
Private Function SecondsSince(ByVal t As Double) As Double
    SecondsSince = Timer - t
    If SecondsSince < 0 Then SecondsSince = SecondsSince + 86400
End Function

' DRAINMOD has finished a file: it exists, nothing else has it open, and its
' size did not change for a moment
Private Function FileDone(ByVal p As String) As Boolean
    Dim f As Integer, n1 As Long
    If Not FileExists(p) Then Exit Function
    On Error GoTo Busy
    n1 = FileLen(p)
    f = FreeFile
    Open p For Binary Access Read Lock Read Write As #f
    Close #f
    Sleep 300
    If FileLen(p) <> n1 Then Exit Function
    f = FreeFile
    Open p For Binary Access Read Lock Read Write As #f
    Close #f
    FileDone = True
    Exit Function
Busy:
End Function

' wait (up to the run time limit) until DRAINMOD has finished writing a file
Private Function WaitFileDone(ByVal p As String) As Boolean
    Dim t As Double, limit As Double
    limit = CfgOpt("RunTimeout", 10) * 60
    t = Timer
    Do
        If FileDone(p) Then
            WaitFileDone = True
            Exit Function
        End If
        If SecondsSince(t) > limit Then Exit Function
        If SecondsSince(t) > 15 And Not FileExists(p) Then Exit Function
        DoEvents
        Sleep 200
    Loop
End Function

' one model evaluation in run slot 1 (waits for DRAINMOD; errors are raised)
Private Function Evaluate(x() As Double) As Double
    lastX = x
    LimitKirk lastX
    LaunchRun 1, x
    WaitRuns 1
    If wFail(1) <> "" Then Err.Raise vbObjectError + 749, "DRAINMOD", wFail(1)
    If IsYes(Cfg("OutAuto").Value) Or Trim$(CStr(Cfg("OutFile").Value)) = "" Then
        runStart = wStart(1)
        DetectOutputFile
        Cfg("OutAuto").Value = "No"
    End If
    If Not WaitFileDone(CStr(Cfg("OutFile").Value)) Then Err.Raise vbObjectError + 744, "DRAINMOD", _
        "DRAINMOD did not finish writing " & Cfg("OutFile").Value & " within the time limit on the Setup sheet."
    ReadDailyOutput CStr(Cfg("OutFile").Value), wStart(1), WorkGenPath(1)
    Evaluate = Objective()
End Function

Private Function KgeQ() As Variant
    If stQ(9) = 1 Then KgeQ = stQ(1) Else KgeQ = ""
End Function

Private Function KgeW() As Variant
    If stW(9) = 1 Then KgeW = stW(1) Else KgeW = ""
End Function

'=============================================================== DDS ====
Private Function RandNormal() As Double
    Dim u1 As Double, u2 As Double
    Do
        u1 = Rnd
    Loop While u1 <= 0
    u2 = Rnd
    RandNormal = Sqr(-2 * Log(u1)) * Cos(6.28318530718 * u2)
End Function

Private Function ToSearch(ByVal i As Integer, ByVal v As Double) As Double
    If pLog(i) Then ToSearch = Log(v) / Log(10#) Else ToSearch = v
End Function

Private Function FromSearch(ByVal i As Integer, ByVal z As Double) As Double
    If pLog(i) Then FromSearch = 10 ^ z Else FromSearch = z
End Function

Private Sub LogRun(ByVal k As Long, ByVal f As Double, x() As Double, ByVal kq As Variant, ByVal kw As Variant, ByVal note As String)
    Dim ws As Worksheet, r As Long, i As Integer
    Set ws = ThisWorkbook.Worksheets("Run Log")
    r = k + 1
    ws.Cells(r, 1).Value = k
    If f > -1E+29 Then ws.Cells(r, 2).Value = f Else ws.Cells(r, 2).Value = "failed"
    ws.Cells(r, 3).Value = kq
    ws.Cells(r, 4).Value = kw
    ws.Cells(r, 5).Value = note
    For i = 1 To pN
        ws.Cells(r, 5 + i).Value = x(i)
    Next i
End Sub

Private Function RunNote(ByVal w As Integer, ByVal accepted As Boolean, ByVal phase As String) As String
    Dim s As String
    s = phase
    If accepted Then
        If s <> "" Then s = s & "; "
        s = s & "best so far"
    End If
    If wFail(w) <> "" Then
        If s <> "" Then s = s & "; "
        s = s & "failed: " & wFail(w)
    End If
    RunNote = s
End Function

' read and log the nb runs that just finished (runs k+1 .. k+nb); returns the
' slot of the best one that beats fBest, or 0
Private Function CollectBatch(ByVal k As Long, ByVal nb As Integer, xc() As Double, ByVal fBest As Double, _
        fc() As Double, ByRef nFail As Long, ByVal phase As String) As Integer
    Dim w As Integer, i As Integer, bestW As Integer, xt() As Double, kq() As Variant, kw() As Variant
    ReDim xt(1 To pN): ReDim kq(1 To nb): ReDim kw(1 To nb)
    For w = 1 To nb
        fc(w) = CollectRun(w)
        kq(w) = KgeQ(): kw(w) = KgeW()
        If wFail(w) <> "" Then nFail = nFail + 1
        If fc(w) > fBest Then
            If bestW = 0 Then
                bestW = w
            ElseIf fc(w) > fc(bestW) Then
                bestW = w
            End If
        End If
    Next w
    For w = 1 To nb
        For i = 1 To pN
            xt(i) = xc(w, i)
        Next i
        LogRun k + w, fc(w), xt, kq(w), kw(w), RunNote(w, w = bestW, phase)
    Next w
    CollectBatch = bestW
End Function

Private Sub StartRunLog()
    Dim ws As Worksheet, i As Integer
    Set ws = ThisWorkbook.Worksheets("Run Log")
    ws.Cells.Clear
    ws.Range("A1:E1").Value = Array("Run", "Objective", "KGE drain flow", "KGE water table", "Note")
    For i = 1 To pN
        ws.Cells(1, 5 + i).Value = Cfg("ParamTable").Cells(pRow(i), 2).Value
    Next i
    ws.Rows(1).Font.Bold = True
End Sub

' DDS (Tolson and Shoemaker 2007), with up to ParallelRuns candidates run side
' by side: each is drawn around the current best, and the best of the batch
' is kept if it improves. With 1 parallel run this is standard DDS. Then a
' compass search ("polish") steps each parameter up and down from the best
' point, halving the step when nothing improves.
Public Sub RunCalibration()
    Dim m As Long, k As Long, i As Integer, w As Integer, nb As Integer, nsel As Integer, prob As Double, fBest As Double
    Dim xb() As Double, zb() As Double, zmin() As Double, zmax() As Double, sel() As Boolean
    Dim zc() As Double, xc() As Double, xt() As Double, fc() As Double, z As Double
    Dim rr As Double, cur As Variant, seed As Variant, t0 As Date
    Dim started As Boolean, nFail As Long, msg As String, errText As String, parNote As String, ok As Boolean
    Dim np As Long, usedP As Long, stepP As Double, nc As Integer, ci As Integer, c As Integer
    Dim improved As Boolean, bestW As Integer, pIdx() As Integer, pDir() As Integer, mTot As Long
    On Error GoTo Failed
    Application.EnableCancelKey = xlErrorHandler
    LoadProject
    LoadSelectedParams
    If pN = 0 Then Err.Raise vbObjectError + 750, "Calibration", "Tick at least one parameter (column A) on the Parameters sheet."
    LoadMeasured
    m = Val(CStr(Cfg("NRuns").Value)): If m < 2 Then m = 2
    np = CfgOpt("PolishRuns", 0): If np < 0 Then np = 0
    mTot = m + np
    rr = Val(CStr(Cfg("DdsR").Value)): If rr <= 0 Then rr = 0.2
    seed = Cfg("Seed").Value
    If IsNumeric(seed) And Not IsEmpty(seed) Then
        Rnd -1
        Randomize CDbl(seed)
    Else
        Randomize
    End If
    ReDim xb(1 To pN): ReDim zb(1 To pN): ReDim zmin(1 To pN): ReDim zmax(1 To pN): ReDim sel(1 To pN)
    ReDim zc(1 To NWMAX, 1 To pN): ReDim xc(1 To NWMAX, 1 To pN): ReDim xt(1 To pN): ReDim fc(1 To NWMAX)
    ReDim pIdx(1 To 2 * pN): ReDim pDir(1 To 2 * pN)
    For i = 1 To pN
        zmin(i) = ToSearch(i, pMin(i)): zmax(i) = ToSearch(i, pMax(i))
        cur = Cfg("ParamTable").Cells(pRow(i), 4).Value
        If IsNumeric(cur) And Not IsEmpty(cur) Then
            If cur >= pMin(i) And cur <= pMax(i) Then xb(i) = cur Else xb(i) = FromSearch(i, zmin(i) + Rnd * (zmax(i) - zmin(i)))
        Else
            xb(i) = FromSearch(i, zmin(i) + Rnd * (zmax(i) - zmin(i)))
        End If
        zb(i) = ToSearch(i, xb(i))
    Next i
    StartRunLog
    ThisWorkbook.Worksheets("Run Log").Activate
    ThisWorkbook.Worksheets("Setup").Activate
    t0 = Now
    Cfg("RunsDone").Value = 0
    SetStatus "Calibration run 1 of " & mTot & " (starting values) ..."
    ' the starting run is not protected: if it fails, the setup is wrong
    ' (program, project, daily output), so the calibration stops with the reason
    fBest = Evaluate(xb)
    LogRun 1, fBest, xb, KgeQ(), KgeW(), "starting values; best so far"
    Cfg("BestKGE").Value = fBest
    Cfg("RunsDone").Value = 1
    started = True
    k = 1

    ' ---- parallel runs: every slot must reproduce the single run exactly
    nWork = CheckParallel(xb, fBest, parNote)
    If parNote <> "" Then ThisWorkbook.Worksheets("Run Log").Cells(1, 7 + pN).Value = parNote

    ' ---- DDS
    Do While k < m
        nb = nWork
        If nb > m - k Then nb = m - k
        For w = 1 To nb
            prob = 1 - Log(k + w - 1) / Log(m)
            nsel = 0
            For i = 1 To pN
                sel(i) = (Rnd < prob)
                If sel(i) Then nsel = nsel + 1
            Next i
            If nsel = 0 Then sel(Int(Rnd * pN) + 1) = True
            For i = 1 To pN
                z = zb(i)
                If sel(i) Then
                    z = zb(i) + rr * (zmax(i) - zmin(i)) * RandNormal()
                    If z < zmin(i) Then
                        z = zmin(i) + (zmin(i) - z)
                        If z > zmax(i) Then z = zmin(i)
                    ElseIf z > zmax(i) Then
                        z = zmax(i) - (z - zmax(i))
                        If z < zmin(i) Then z = zmax(i)
                    End If
                End If
                zc(w, i) = z: xc(w, i) = FromSearch(i, z): xt(i) = xc(w, i)
            Next i
            SafeLaunch w, xt
        Next w
        SetStatus "Calibration run " & (k + 1) & IIf(nb > 1, "-" & (k + nb), "") & " of " & mTot & _
            IIf(nWork > 1, " (" & nWork & " side by side)", "") & ";  best objective so far " & Format$(fBest, "0.0000") & _
            ";  failed runs " & nFail & ";  elapsed " & Format$(Now - t0, "h:nn:ss") & "  (press Esc to stop)"
        WaitRuns nb
        bestW = CollectBatch(k, nb, xc, fBest, fc, nFail, "")
        If bestW > 0 Then
            fBest = fc(bestW)
            For i = 1 To pN
                zb(i) = zc(bestW, i): xb(i) = xc(bestW, i)
            Next i
            Cfg("BestKGE").Value = fBest
        End If
        k = k + nb
        Cfg("RunsDone").Value = k
    Loop

    ' ---- polish: compass search around the best point
    usedP = 0: stepP = 0.05
    Do While usedP < np And stepP >= 0.002
        nc = 0
        For i = 1 To pN
            nc = nc + 1: pIdx(nc) = i: pDir(nc) = 1
            nc = nc + 1: pIdx(nc) = i: pDir(nc) = -1
        Next i
        improved = False
        ci = 1
        Do While ci <= nc And usedP < np And Not improved
            nb = 0
            Do While ci <= nc And nb < nWork And usedP + nb < np
                i = pIdx(ci)
                z = zb(i) + pDir(ci) * stepP * (zmax(i) - zmin(i))
                If z > zmax(i) Then z = zmax(i)
                If z < zmin(i) Then z = zmin(i)
                If z <> zb(i) Then
                    nb = nb + 1
                    For c = 1 To pN
                        zc(nb, c) = zb(c): xc(nb, c) = xb(c)
                    Next c
                    zc(nb, i) = z: xc(nb, i) = FromSearch(i, z)
                    For c = 1 To pN
                        xt(c) = xc(nb, c)
                    Next c
                    SafeLaunch nb, xt
                End If
                ci = ci + 1
            Loop
            If nb > 0 Then
                SetStatus "Polish run " & (k + 1) & IIf(nb > 1, "-" & (k + nb), "") & " of " & mTot & _
                    " (step " & Format$(stepP * 100, "0.0") & "% of each range);  best objective so far " & Format$(fBest, "0.0000") & _
                    ";  elapsed " & Format$(Now - t0, "h:nn:ss") & "  (press Esc to stop)"
                WaitRuns nb
                bestW = CollectBatch(k, nb, xc, fBest, fc, nFail, "polish")
                If bestW > 0 Then
                    fBest = fc(bestW)
                    For c = 1 To pN
                        zb(c) = zc(bestW, c): xb(c) = xc(bestW, c)
                    Next c
                    Cfg("BestKGE").Value = fBest
                    improved = True
                End If
                usedP = usedP + nb
                k = k + nb
                Cfg("RunsDone").Value = k
            End If
        Loop
        If Not improved Then stepP = stepP / 2
    Loop
Finish:
    On Error GoTo Failed2
    KillRuns
    LimitKirk xb
    For i = 1 To pN
        Cfg("ParamTable").Cells(pRow(i), 8).Value = xb(i)
    Next i
    SetStatus "Re-running the best parameter set ..."
    fBest = Evaluate(xb)
    Cfg("BestKGE").Value = fBest
    WriteResults
    msg = "Calibration finished: best objective " & Format$(fBest, "0.0000") & " after " & Cfg("RunsDone").Value & " runs"
    If nFail > 0 Then msg = msg & " (" & nFail & " failed; see the Note column of the Run Log)"
    If parNote <> "" Then msg = msg & ". " & parNote
    SetStatus msg & IIf(Right$(msg, 1) = ".", "", ".")
    Application.StatusBar = False
    Application.EnableCancelKey = xlInterrupt
    ThisWorkbook.Worksheets("Results").Activate
    Exit Sub
Failed:
    If Err.Number = 18 Then
        If MsgBox("Stop the calibration and keep the best values found so far?", vbYesNo + vbQuestion) = vbNo Then Resume
        KillRuns
        If started Then Resume Finish
        Application.StatusBar = False
        Application.EnableCancelKey = xlInterrupt
        Exit Sub
    End If
    msg = Err.Description
    errText = msg
    KillRuns
    ' keep what the search found before the error (this clears Err)
    If started Then
        WriteBestColumn xb
        msg = msg & vbLf & vbLf & "The best values from the " & Cfg("RunsDone").Value & _
            " completed runs are in the Best value column of the Parameters sheet."
    End If
    Application.StatusBar = False
    SetStatus "Calibration stopped: " & errText
    Application.StatusBar = False
    Application.EnableCancelKey = xlInterrupt
    MsgBox "Calibration stopped: " & msg, vbExclamation
    Exit Sub
Failed2:
    KillRuns
    Application.StatusBar = False
    Application.EnableCancelKey = xlInterrupt
    MsgBox "Could not finish: " & Err.Description, vbExclamation
End Sub

Private Sub WriteBestColumn(xIn() As Double)
    Dim i As Integer, xb() As Double
    On Error Resume Next
    xb = xIn
    LimitKirk xb
    For i = 1 To pN
        Cfg("ParamTable").Cells(pRow(i), 8).Value = xb(i)
    Next i
End Sub

' run once with the values in the "Current" column and report the fit
Public Sub EvaluateCurrent()
    Dim x() As Double, i As Integer, cur As Variant, f As Double
    On Error GoTo Failed
    LoadProject
    LoadSelectedParams
    LoadMeasured
    If pN > 0 Then
        ReDim x(1 To pN)
        For i = 1 To pN
            cur = Cfg("ParamTable").Cells(pRow(i), 4).Value
            If Not IsNumeric(cur) Or IsEmpty(cur) Then Err.Raise vbObjectError + 751, "Evaluate", "Click 'Read project parameters' first."
            x(i) = cur
        Next i
    Else
        ReDim x(1 To 1)
    End If
    SetStatus "Running DRAINMOD with the current values ..."
    f = Evaluate(x)
    WriteResults
    SetStatus "Current values: objective " & Format$(f, "0.0000") & "  (daily output: " & Cfg("OutFile").Value & ")"
    Application.StatusBar = False
    ThisWorkbook.Worksheets("Results").Activate
    Exit Sub
Failed:
    Application.StatusBar = False
    SetStatus "Evaluation failed: " & Err.Description
    Application.StatusBar = False
    MsgBox "Evaluation failed: " & Err.Description, vbExclamation
End Sub

'======================================================= sensitivity ====
' parameters for the sensitivity analysis: every row of the Parameters sheet
' with a usable range that applies to this project, ticked or not. Rows left
' out are listed in skipRow()/skipWhy() (nSkip of them).
Private Sub LoadSensParams(skipRow() As Long, skipWhy() As String, ByRef nSkip As Long)
    Dim tbl As Range, r As Long, key As String, why As String, i As Integer, j As Integer
    Dim hasKsat As Boolean, iD As Integer, iH As Integer, nm As String
    Set tbl = Cfg("ParamTable")
    ReDim skipRow(1 To tbl.Rows.Count): ReDim skipWhy(1 To tbl.Rows.Count)
    nSkip = 0: pN = 0
    For r = 1 To tbl.Rows.Count
        key = CStr(tbl.Cells(r, 3).Value)
        nm = CStr(tbl.Cells(r, 2).Value)
        why = ""
        If key <> "" Then
            If Not IsNumeric(tbl.Cells(r, 5).Value) Or Not IsNumeric(tbl.Cells(r, 6).Value) Or IsEmpty(tbl.Cells(r, 5).Value) Or IsEmpty(tbl.Cells(r, 6).Value) Then
                why = "no minimum and maximum on the Parameters sheet"
            ElseIf tbl.Cells(r, 6).Value <= tbl.Cells(r, 5).Value Then
                why = "the maximum is not larger than the minimum"
            ElseIf LCase$(Left$(Trim$(CStr(tbl.Cells(r, 7).Value)), 3)) = "log" And tbl.Cells(r, 5).Value <= 0 Then
                why = "a log-scale range must be above zero"
            ElseIf Left$(key, 4) = "KSAT" Then
                If CInt(Right$(key, 1)) > LayerCount(genLines) Then why = "the project has only " & LayerCount(genLines) & " soil layer(s)"
            ElseIf (key = "SEEPK" Or key = "SEEPH") And Not SeepOn(genLines) Then
                why = "vertical (deep) seepage is off in this project"
            ElseIf key = "LSEEPT" And Not SeepLineOn(genLines, 3) Then
                why = "lateral seepage is off in this project"
            ElseIf (key = "DDEPTH" Or key = "DSPACE") And tbl.Cells(r, 5).Value <= 0 Then
                why = "the minimum must be above zero"
            End If
            If why = "" Then
                If pN >= NPARMAX Then
                    why = "more than " & NPARMAX & " parameters"
                Else
                    pN = pN + 1
                    pRow(pN) = r: pKey(pN) = key
                    pMin(pN) = tbl.Cells(r, 5).Value: pMax(pN) = tbl.Cells(r, 6).Value
                    pLog(pN) = (LCase$(Left$(Trim$(CStr(tbl.Cells(r, 7).Value)), 3)) = "log")
                    If Left$(key, 4) = "KSAT" Then hasKsat = True
                End If
            End If
            If why <> "" Then
                nSkip = nSkip + 1: skipRow(nSkip) = r: skipWhy(nSkip) = why
            End If
        End If
    Next r
    ' the multiplier and the layer values change the same thing: keep the layers
    If hasKsat Then
        i = 1
        Do While i <= pN
            If pKey(i) = "KMULT" Then
                nSkip = nSkip + 1: skipRow(nSkip) = pRow(i)
                skipWhy(nSkip) = "the layer Ksat values are tested instead (they change the same thing)"
                For j = i To pN - 1
                    pRow(j) = pRow(j + 1): pKey(j) = pKey(j + 1): pMin(j) = pMin(j + 1): pMax(j) = pMax(j + 1): pLog(j) = pLog(j + 1)
                Next j
                pN = pN - 1
            Else
                i = i + 1
            End If
        Loop
    End If
    ' a drain-depth range reaching the impermeable layer cannot run
    For i = 1 To pN
        If pKey(i) = "DDEPTH" Then iD = i
        If pKey(i) = "DIMP" Then iH = i
    Next i
    If iD > 0 And iH = 0 Then
        If pMax(iD) >= ImpDepth(genLines) Then Err.Raise vbObjectError + 729, "Sensitivity", "The maximum drain depth (" & pMax(iD) & _
            " cm) must be less than the depth to the impermeable layer (" & ImpDepth(genLines) & " cm)."
    ElseIf iH > 0 And iD = 0 Then
        If pMin(iH) <= DrainField(genLines, 1) Then Err.Raise vbObjectError + 727, "Sensitivity", "The minimum depth to the impermeable layer (" & _
            pMin(iH) & " cm) must be greater than the drain depth (" & DrainField(genLines, 1) & " cm)."
    ElseIf iD > 0 And iH > 0 Then
        If pMax(iD) >= pMin(iH) Then Err.Raise vbObjectError + 729, "Sensitivity", "The maximum drain depth (" & pMax(iD) & _
            " cm) must be less than the minimum depth to the impermeable layer (" & pMin(iH) & " cm)."
    End If
    CheckKirkRange
End Sub

Private Function MorrisShape(ByVal muStar As Double, ByVal sigma As Double) As String
    Dim q As Double
    If muStar <= 0.0000001 Then
        MorrisShape = "no effect seen"
        Exit Function
    End If
    q = sigma / muStar
    If q < 0.1 Then
        MorrisShape = "linear"
    ElseIf q < 0.5 Then
        MorrisShape = "monotonic"
    ElseIf q < 1 Then
        MorrisShape = "almost monotonic"
    Else
        MorrisShape = "non-linear and/or interacts with other parameters"
    End If
End Function

' Morris elementary effects (Morris 1991; mu* of Campolongo et al. 2007).
' Each parameter range is mapped to 0..1 (log scale in powers of ten) on a
' 4-level grid. A trajectory starts at a random grid point and moves one
' parameter at a time by 2/3 of its range, in random order, so it costs
' (parameters + 1) runs. The elementary effect of a step is the change in the
' objective divided by the step (as a fraction of the range); mu* is the mean
' of their absolute values over the trajectories and ranks the parameters.
Public Sub RunSensitivity()
    Const DLT As Double = 2# / 3#
    Dim r As Long, nPt As Long, q As Long, t As Long, i As Integer, j As Integer, w As Integer, nb As Integer, c As Integer
    Dim zmin() As Double, zmax() As Double, u() As Double, xs() As Double, xt() As Double, x0() As Double
    Dim fv() As Double, kqv() As Variant, kwv() As Variant, okv() As Boolean, stepI() As Integer, stepD() As Double
    Dim ord() As Integer, tmp As Integer, cur As Variant, seed As Variant, f0 As Double
    Dim skipRow() As Long, skipWhy() As String, nSkip As Long, parNote As String, msg As String
    Dim qDone As Long, nFail As Long, t0 As Date, stopped As Boolean, k As Long, note As String
    Dim nE() As Long, sAbs() As Double, sSum() As Double, sSq() As Double, e As Double
    Dim nQ() As Long, aQ() As Double, nW() As Long, aW() As Double
    Dim muS() As Double, mu() As Double, sg() As Double, rk() As Integer, order() As Integer, best As Integer
    Dim ws As Worksheet, tbl As Range, row0 As Long, maxMu As Double, started As Boolean
    On Error GoTo Failed
    Application.EnableCancelKey = xlErrorHandler
    LoadProject
    LoadSensParams skipRow, skipWhy, nSkip
    If pN = 0 Then Err.Raise vbObjectError + 770, "Sensitivity", "No parameter on the Parameters sheet has a minimum and maximum that applies to this project."
    LoadMeasured
    Set tbl = Cfg("ParamTable")
    r = CfgOpt("SensTrajectories", 10)
    If r < 2 Then r = 2
    If r > 100 Then r = 100
    nPt = r * (pN + 1)
    seed = Cfg("Seed").Value
    If IsNumeric(seed) And Not IsEmpty(seed) Then
        Rnd -1
        Randomize CDbl(seed)
    Else
        Randomize
    End If
    ReDim zmin(1 To pN): ReDim zmax(1 To pN): ReDim u(1 To pN): ReDim xt(1 To pN): ReDim x0(1 To pN): ReDim ord(1 To pN)
    ReDim xs(1 To nPt, 1 To pN): ReDim fv(1 To nPt): ReDim kqv(1 To nPt): ReDim kwv(1 To nPt): ReDim okv(1 To nPt)
    ReDim stepI(1 To nPt): ReDim stepD(1 To nPt)
    For i = 1 To pN
        zmin(i) = ToSearch(i, pMin(i)): zmax(i) = ToSearch(i, pMax(i))
        cur = tbl.Cells(pRow(i), 4).Value
        If Not IsNumeric(cur) Or IsEmpty(cur) Then Err.Raise vbObjectError + 771, "Sensitivity", "Click '1. Read project parameters' first (" & tbl.Cells(pRow(i), 2).Value & " has no current value)."
        x0(i) = cur
    Next i
    ' the design: r trajectories of pN + 1 points
    q = 0
    For t = 1 To r
        For i = 1 To pN
            u(i) = Int(Rnd * 4) / 3
            ord(i) = i
        Next i
        For i = pN To 2 Step -1
            j = Int(Rnd * i) + 1
            tmp = ord(i): ord(i) = ord(j): ord(j) = tmp
        Next i
        For c = 0 To pN
            If c > 0 Then
                i = ord(c)
                If u(i) < 0.5 Then stepD(q + 1) = DLT Else stepD(q + 1) = -DLT
                u(i) = u(i) + stepD(q + 1)
                If u(i) < 0 Then u(i) = 0
                If u(i) > 1 Then u(i) = 1
                stepI(q + 1) = i
            End If
            q = q + 1
            For i = 1 To pN
                xs(q, i) = FromSearch(i, zmin(i) + u(i) * (zmax(i) - zmin(i)))
            Next i
        Next c
    Next t
    StartRunLog
    ThisWorkbook.Worksheets("Setup").Activate
    t0 = Now
    Cfg("RunsDone").Value = 0
    ' setup check with the current values (not part of the design): finds the
    ' daily output file and shows the reason if DRAINMOD cannot run at all
    SetStatus "Sensitivity: checking the setup with the current values ..."
    f0 = Evaluate(x0)
    LogRun 1, f0, x0, KgeQ(), KgeW(), "sensitivity: current values (setup check, not used in the ranking)"
    k = 1
    started = True
    nWork = CheckParallel(x0, f0, parNote)
    If parNote <> "" Then ThisWorkbook.Worksheets("Run Log").Cells(1, 7 + pN).Value = parNote
    ' the design runs
    q = 0
    Do While q < nPt
        nb = nWork
        If nb > nPt - q Then nb = nPt - q
        For w = 1 To nb
            For i = 1 To pN
                xt(i) = xs(q + w, i)
            Next i
            SafeLaunch w, xt
        Next w
        SetStatus "Sensitivity run " & (q + 1) & IIf(nb > 1, "-" & (q + nb), "") & " of " & nPt & " (" & r & " trajectories x " & (pN + 1) & " runs" & _
            IIf(nWork > 1, ", " & nWork & " side by side", "") & ");  failed runs " & nFail & ";  elapsed " & Format$(Now - t0, "h:nn:ss") & "  (press Esc to stop)"
        WaitRuns nb
        For w = 1 To nb
            fv(q + w) = CollectRun(w)
            kqv(q + w) = KgeQ(): kwv(q + w) = KgeW()
            okv(q + w) = (fv(q + w) > -1E+29)
            If Not okv(q + w) Then nFail = nFail + 1
            For i = 1 To pN
                xt(i) = xs(q + w, i)
            Next i
            t = Int((q + w - 1) / (pN + 1)) + 1
            If stepI(q + w) = 0 Then
                note = "sensitivity: trajectory " & t & ", start"
            Else
                note = "sensitivity: trajectory " & t & ", " & tbl.Cells(pRow(stepI(q + w)), 2).Value & IIf(stepD(q + w) > 0, " up", " down")
            End If
            If wFail(w) <> "" Then note = note & "; failed: " & wFail(w)
            LogRun k + q + w, fv(q + w), xt, kqv(q + w), kwv(q + w), note
        Next w
        q = q + nb
        qDone = q
        Cfg("RunsDone").Value = k + q
    Loop
Analyse:
    On Error GoTo Failed2
    KillRuns
    ' elementary effects, per change in the whole range
    ReDim nE(1 To pN): ReDim sAbs(1 To pN): ReDim sSum(1 To pN): ReDim sSq(1 To pN)
    ReDim nQ(1 To pN): ReDim aQ(1 To pN): ReDim nW(1 To pN): ReDim aW(1 To pN)
    ReDim muS(1 To pN): ReDim mu(1 To pN): ReDim sg(1 To pN): ReDim rk(1 To pN): ReDim order(1 To pN)
    For q = 2 To qDone
        i = stepI(q)
        If i > 0 Then
            If okv(q) And okv(q - 1) Then
                e = (fv(q) - fv(q - 1)) / stepD(q)
                nE(i) = nE(i) + 1: sAbs(i) = sAbs(i) + Abs(e): sSum(i) = sSum(i) + e: sSq(i) = sSq(i) + e * e
                If Not IsEmpty(kqv(q)) And Not IsEmpty(kqv(q - 1)) And CStr(kqv(q)) <> "" And CStr(kqv(q - 1)) <> "" Then
                    nQ(i) = nQ(i) + 1: aQ(i) = aQ(i) + Abs((kqv(q) - kqv(q - 1)) / stepD(q))
                End If
                If Not IsEmpty(kwv(q)) And Not IsEmpty(kwv(q - 1)) And CStr(kwv(q)) <> "" And CStr(kwv(q - 1)) <> "" Then
                    nW(i) = nW(i) + 1: aW(i) = aW(i) + Abs((kwv(q) - kwv(q - 1)) / stepD(q))
                End If
            End If
        End If
    Next q
    For i = 1 To pN
        If nE(i) > 0 Then
            muS(i) = sAbs(i) / nE(i): mu(i) = sSum(i) / nE(i)
            If nE(i) > 1 Then
                sg(i) = (sSq(i) - nE(i) * mu(i) * mu(i)) / (nE(i) - 1)
                If sg(i) < 0 Then sg(i) = 0
                sg(i) = Sqr(sg(i))
            End If
        End If
        order(i) = i
    Next i
    ' rank by mu*, largest first; parameters without effects go last, unranked
    For i = 1 To pN - 1
        best = i
        For j = i + 1 To pN
            If nE(order(j)) > 0 And (nE(order(best)) = 0 Or muS(order(j)) > muS(order(best))) Then best = j
        Next j
        tmp = order(i): order(i) = order(best): order(best) = tmp
    Next i
    For i = 1 To pN
        If nE(order(i)) > 0 Then rk(order(i)) = i
        If muS(order(i)) > maxMu Then maxMu = muS(order(i))
    Next i
    ' report
    Set ws = SensSheet()
    ws.Cells.Clear
    For c = ws.ChartObjects.Count To 1 Step -1
        ws.ChartObjects(c).Delete
    Next c
    ws.Range("A1").Value = "Parameter sensitivity (Morris elementary effects)"
    ws.Range("A1").Font.Bold = True: ws.Range("A1").Font.Size = 13
    msg = qDone & " of " & nPt & " runs (" & r & " trajectories x " & (pN + 1) & " runs), " & Format$(Now, "yyyy-mm-dd hh:nn") & "; objective as set on the Setup sheet (" & _
        CStr(Cfg("Target").Value) & ", KGE " & CStr(Cfg("KgeVersion").Value) & ")"
    If nFail > 0 Then msg = msg & "; " & nFail & " failed run(s) left out (see the Run Log)"
    If stopped Then msg = msg & "; stopped early"
    ws.Range("A2").Value = msg
    ws.Range("A3").Value = "mu* = mean absolute change in the objective when the parameter moves across its whole range (Minimum to Maximum on the Parameters sheet); rank 1 is the most sensitive. " & _
        "sigma = spread of those changes: large compared with mu* means non-linear or interacting with other parameters."
    row0 = 5
    ws.Range(ws.Cells(row0, 1), ws.Cells(row0, 11)).Value = Array("Rank", "Parameter", "Key", "mu* (objective)", "Relative mu* (%)", "mu (objective)", "sigma (objective)", _
        "mu* KGE drain flow", "mu* KGE water table", "Effects used", "Response")
    ws.Rows(row0).Font.Bold = True
    For c = 1 To pN
        i = order(c)
        ws.Cells(row0 + c, 2).Value = tbl.Cells(pRow(i), 2).Value
        ws.Cells(row0 + c, 3).Value = pKey(i)
        ws.Cells(row0 + c, 10).Value = nE(i)
        If nE(i) > 0 Then
            ws.Cells(row0 + c, 1).Value = rk(i)
            ws.Cells(row0 + c, 4).Value = muS(i)
            If maxMu > 0 Then ws.Cells(row0 + c, 5).Value = 100 * muS(i) / maxMu
            ws.Cells(row0 + c, 6).Value = mu(i)
            ws.Cells(row0 + c, 7).Value = sg(i)
            If nQ(i) > 0 Then ws.Cells(row0 + c, 8).Value = aQ(i) / nQ(i)
            If nW(i) > 0 Then ws.Cells(row0 + c, 9).Value = aW(i) / nW(i)
            If nE(i) > 1 Then ws.Cells(row0 + c, 11).Value = MorrisShape(muS(i), sg(i))
        Else
            ws.Cells(row0 + c, 1).Value = "-"
            ws.Cells(row0 + c, 11).Value = "not ranked: no pair of finished runs"
        End If
    Next c
    ws.Range(ws.Cells(row0 + 1, 4), ws.Cells(row0 + pN, 4)).NumberFormat = "0.0000"
    ws.Range(ws.Cells(row0 + 1, 5), ws.Cells(row0 + pN, 5)).NumberFormat = "0"
    ws.Range(ws.Cells(row0 + 1, 6), ws.Cells(row0 + pN, 9)).NumberFormat = "0.0000"
    If nSkip > 0 Then
        ws.Cells(row0 + pN + 2, 1).Value = "Not tested"
        ws.Cells(row0 + pN + 2, 1).Font.Bold = True
        For c = 1 To nSkip
            ws.Cells(row0 + pN + 2 + c, 2).Value = tbl.Cells(skipRow(c), 2).Value
            ws.Cells(row0 + pN + 2 + c, 3).Value = tbl.Cells(skipRow(c), 3).Value
            ws.Cells(row0 + pN + 2 + c, 4).Value = skipWhy(c)
        Next c
    End If
    If parNote <> "" Then ws.Cells(row0 + pN + nSkip + 4, 1).Value = parNote
    ws.Columns("A").ColumnWidth = 7
    ws.Columns("B").ColumnWidth = 52
    ws.Columns("C").ColumnWidth = 10
    ws.Columns("D:J").ColumnWidth = 13
    ws.Columns("K").ColumnWidth = 44
    ws.Range(ws.Cells(row0, 1), ws.Cells(row0, 11)).WrapText = True
    SensChart ws, row0, pN
    ' rank column on the Parameters sheet
    tbl.Worksheet.Cells(tbl.Row - 1, tbl.Column - 1).Value = "Sensitivity rank"
    tbl.Worksheet.Cells(tbl.Row - 1, tbl.Column - 1).Font.Bold = True
    tbl.Columns(1).Offset(0, -1).ClearContents
    For i = 1 To pN
        If rk(i) > 0 Then tbl.Worksheet.Cells(tbl.Row + pRow(i) - 1, tbl.Column - 1).Value = rk(i)
    Next i
    msg = "Sensitivity analysis finished: " & qDone & " runs"
    If pN > 0 Then
        If rk(order(1)) > 0 Then msg = msg & "; most sensitive: " & tbl.Cells(pRow(order(1)), 2).Value
    End If
    If nFail > 0 Then msg = msg & " (" & nFail & " failed runs left out)"
    SetStatus msg & ". Ranks are on the Sensitivity sheet and in column A of the Parameters sheet."
    Application.StatusBar = False
    Application.EnableCancelKey = xlInterrupt
    ws.Activate
    Exit Sub
Failed:
    If Err.Number = 18 Then
        If MsgBox("Stop the sensitivity analysis and rank the parameters from the runs finished so far?", vbYesNo + vbQuestion) = vbNo Then Resume
        KillRuns
        If started And qDone > 0 Then
            stopped = True
            Resume Analyse
        End If
        Application.StatusBar = False
        Application.EnableCancelKey = xlInterrupt
        SetStatus "Sensitivity analysis stopped."
        Exit Sub
    End If
    msg = Err.Description
    KillRuns
    Application.StatusBar = False
    SetStatus "Sensitivity analysis stopped: " & msg
    Application.StatusBar = False
    Application.EnableCancelKey = xlInterrupt
    MsgBox "Sensitivity analysis stopped: " & msg, vbExclamation
    Exit Sub
Failed2:
    KillRuns
    Application.StatusBar = False
    Application.EnableCancelKey = xlInterrupt
    MsgBox "Could not finish the sensitivity report: " & Err.Description, vbExclamation
End Sub

' the Sensitivity sheet, added after the Parameters sheet if missing
Private Function SensSheet() As Worksheet
    Dim ws As Worksheet
    On Error Resume Next
    Set ws = ThisWorkbook.Worksheets("Sensitivity")
    On Error GoTo 0
    If ws Is Nothing Then
        Set ws = ThisWorkbook.Worksheets.Add(After:=ThisWorkbook.Worksheets("Parameters"))
        ws.Name = "Sensitivity"
    End If
    Set SensSheet = ws
End Function

' bar chart of relative mu*, rank 1 at the top
Private Sub SensChart(ws As Worksheet, ByVal row0 As Long, ByVal n As Integer)
    Dim co As Object, ch As Object, top As Double
    On Error Resume Next
    If n < 1 Then Exit Sub
    top = ws.Cells(row0 + n + 2, 1).Top
    If ws.Cells(row0 + n + 2, 1).Value <> "" Then top = ws.Cells(ws.Cells(ws.Rows.Count, 2).End(xlUp).Row + 3, 1).Top
    Set co = ws.ChartObjects.Add(ws.Cells(1, 2).Left, top, 560, 60 + 22 * n)
    co.Name = "SensChart"
    Set ch = co.Chart
    ch.ChartType = xlBarClustered
    Do While ch.SeriesCollection.Count > 0
        ch.SeriesCollection(1).Delete
    Loop
    With ch.SeriesCollection.NewSeries
        .Name = "Relative mu* (%)"
        .Values = ws.Range(ws.Cells(row0 + 1, 5), ws.Cells(row0 + n, 5))
        .XValues = ws.Range(ws.Cells(row0 + 1, 2), ws.Cells(row0 + n, 2))
        .Format.Fill.ForeColor.RGB = RGB(46, 117, 182)
    End With
    ch.HasTitle = True
    ch.ChartTitle.Text = "Relative sensitivity (mu*, % of the most sensitive)"
    ch.ChartTitle.Font.Size = 12
    ch.HasLegend = False
    ch.Axes(xlCategory).ReversePlotOrder = True
    ch.Axes(xlValue).MinimumScale = 0
    ch.Axes(xlValue).MaximumScale = 100
    ch.ChartGroups(1).GapWidth = 60
End Sub

' write the calibrated .prj/.gen, named by the user, with the best values (full simulation period)
Public Sub WriteCalibratedProject()
    Dim x() As Double, i As Integer, v As Variant, g As String, p As String, ans As Variant
    On Error GoTo Failed
    LoadProject
    LoadSelectedParams
    If pN = 0 Then Err.Raise vbObjectError + 760, "Write", "No parameters are ticked on the Parameters sheet."
    ReDim x(1 To pN)
    For i = 1 To pN
        v = Cfg("ParamTable").Cells(pRow(i), 8).Value
        If Not IsNumeric(v) Or IsEmpty(v) Then Err.Raise vbObjectError + 761, "Write", "Run the calibration first (the Best column is empty)."
        x(i) = v
    Next i
    ' ask for the name; the .gen gets the same name in the same folder
    p = FolderOf(CStr(Cfg("PrjFile").Value)) & BaseName(CStr(Cfg("PrjFile").Value)) & "_calibrated.prj"
    Do
        ans = Application.GetSaveAsFilename(InitialFileName:=p, FileFilter:="DRAINMOD project (*.prj),*.prj", _
            Title:="Name for the calibrated project (.prj and .gen)")
        If VarType(ans) = vbBoolean Then Exit Sub
        p = CStr(ans)
        If LCase$(Right$(p, 4)) <> ".prj" Then p = p & ".prj"
        g = FolderOf(p) & BaseName(p) & ".gen"
        If LCase$(p) = LCase$(CStr(Cfg("PrjFile").Value)) Or LCase$(g) = LCase$(CStr(Cfg("GenFile").Value)) Then
            MsgBox "Choose a different name: this would overwrite the original project files.", vbExclamation
        ElseIf Dir$(p) <> "" Or Dir$(g) <> "" Then
            If MsgBox(BaseName(p) & ".prj/.gen already exist in this folder. Replace them?", vbYesNo + vbQuestion) = vbYes Then Exit Do
        Else
            Exit Do
        End If
    Loop
    WriteProject x, g, p, False
    MsgBox "Calibrated project written:" & vbLf & p & vbLf & g, vbInformation
    Exit Sub
Failed:
    MsgBox "Could not write the calibrated project: " & Err.Description, vbExclamation
End Sub

'=========================================================== results ====
Private Sub WriteResults()
    Dim ws As Worksheet, i As Long, j As Long, r As Long, labels As Variant, nrow As Long, data() As Variant
    Dim ver As String, cumO As Double, cumS As Double
    Set ws = ThisWorkbook.Worksheets("Results")
    ws.Cells.Clear
    Do While ws.ChartObjects.Count > 0
        ws.ChartObjects(1).Delete
    Loop
    ver = IIf(InStr(CStr(Cfg("KgeVersion").Value), "2009") = 0, "gamma (CV ratio)", "alpha (std. dev. ratio)")
    ws.Range("A1").Value = "Fit for the calibration period " & Format$(Cfg("CalStart").Value, "yyyy-mm-dd") & " to " & Format$(Cfg("CalEnd").Value, "yyyy-mm-dd")
    ws.Range("A1").Font.Bold = True
    ws.Range("A2:C2").Value = Array("Statistic", "Drain flow (cm/day)", "Water table depth (cm)")
    ws.Range("A2:C2").Font.Bold = True
    labels = Array("KGE", "r (correlation)", ver, "beta (mean ratio)", "NSE", "PBIAS (%)", "RMSE", "Days compared")
    For i = 0 To 7
        ws.Cells(3 + i, 1).Value = labels(i)
        If stQ(9) = 1 Or i = 7 Then ws.Cells(3 + i, 2).Value = stQ(i + 1)
        If stW(9) = 1 Or i = 7 Then ws.Cells(3 + i, 3).Value = stW(i + 1)
    Next i
    ws.Range("B3:C9").NumberFormat = "0.000"
    ver = ""
    If ZeroFlowSkip() Then ver = "Drain flow: days when measured and simulated flow are both zero are left out. "
    If InstrDepth() > 0 Then ver = ver & "Water table: only days when the simulated depth is at or above the measuring instrument depth (" & InstrDepth() & " cm) are used."
    ws.Range("A11").Value = Trim$(ver)
    ws.Range("A12").Value = "Parameter values used"
    ws.Range("A12").Font.Bold = True
    For i = 1 To pN
        ws.Cells(12 + i, 1).Value = Cfg("ParamTable").Cells(pRow(i), 2).Value
        ws.Cells(12 + i, 2).Value = lastX(i)
    Next i

    ' daily table
    r = 14 + pN
    ws.Cells(r, 1).Resize(1, 7).Value = Array("Date", "Measured drain flow (cm/day)", "Simulated drain flow (cm/day)", _
        "Measured water table depth (cm)", "Simulated water table depth (cm)", _
        "Cumulative measured drain flow (cm)", "Cumulative simulated drain flow (cm)")
    ws.Cells(r, 1).Resize(1, 7).Font.Bold = True
    ReDim data(1 To nObs, 1 To 7)
    j = 1: nrow = 0
    For i = 1 To nObs
        Do While j < sN And sDate(j) < oDate(i)
            j = j + 1
        Loop
        nrow = nrow + 1
        data(nrow, 1) = CDate(oDate(i))
        If oHasQ(i) Then data(nrow, 2) = oQ(i)
        If oHasW(i) Then data(nrow, 4) = oW(i)
        If sDate(j) = oDate(i) Then
            data(nrow, 3) = sQ(j): data(nrow, 5) = sW(j)
        End If
        ' cumulative flows add only the days with both a measured and a simulated flow
        If oHasQ(i) And sDate(j) = oDate(i) Then
            cumO = cumO + oQ(i): cumS = cumS + sQ(j)
        End If
        data(nrow, 6) = cumO: data(nrow, 7) = cumS
    Next i
    If nrow > 0 Then
        ws.Cells(r + 1, 1).Resize(nrow, 7).Value = data
        ws.Cells(r + 1, 1).Resize(nrow, 1).NumberFormat = "yyyy-mm-dd"
        ws.Cells(r + 1, 6).Resize(nrow, 2).NumberFormat = "0.00"
        AddChart ws, "Drain flow", ws.Cells(r + 1, 1).Resize(nrow, 1), ws.Cells(r + 1, 2).Resize(nrow, 1), _
            ws.Cells(r + 1, 3).Resize(nrow, 1), "Drain flow (cm/day)", 0, False
        If cumO > 0 Or cumS > 0 Then AddCumulative ws.ChartObjects(ws.ChartObjects.Count).Chart, ws.Cells(r + 1, 1).Resize(nrow, 1), _
            ws.Cells(r + 1, 6).Resize(nrow, 1), ws.Cells(r + 1, 7).Resize(nrow, 1)
        AddChart ws, "Water table depth", ws.Cells(r + 1, 1).Resize(nrow, 1), ws.Cells(r + 1, 4).Resize(nrow, 1), _
            ws.Cells(r + 1, 5).Resize(nrow, 1), "Water table depth (cm)", 1, True
    End If
    ws.Columns("A:G").AutoFit
    UpdateGraphs
End Sub

' refresh the Graphs sheet, if the workbook has one: date, observed and
' simulated drain flow for the calibration period, and cumulative flows that
' add only the days with both a measured and a simulated value. The charts
' on the sheet keep their formatting; their data ranges are resized.
Private Sub UpdateGraphs()
    Dim ws As Worksheet, i As Long, j As Long, n As Long, last As Long, data() As Variant, dfmt As String
    Dim co As ChartObject, sr As Series, f As String, parts As Variant, rx As Range, ry As Range, lim As Double
    On Error Resume Next
    Set ws = ThisWorkbook.Worksheets("Graphs")
    On Error GoTo 0
    If ws Is Nothing Or nObs < 1 Then Exit Sub
    n = nObs
    lim = InstrDepth()
    ReDim data(1 To n, 1 To 3)
    Dim wt() As Variant
    ReDim wt(1 To n, 1 To 2)
    j = 1
    For i = 1 To n
        Do While j < sN And sDate(j) < oDate(i)
            j = j + 1
        Loop
        data(i, 1) = CDate(oDate(i))
        If oHasQ(i) Then data(i, 2) = oQ(i)
        If sDate(j) = oDate(i) Then data(i, 3) = sQ(j)
        If oHasW(i) Then wt(i, 1) = oW(i)
        ' simulated depths below the measuring instrument are shown at its depth
        If sDate(j) = oDate(i) Then
            wt(i, 2) = sW(j)
            If lim > 0 And sW(j) > lim Then wt(i, 2) = lim
        End If
    Next i
    dfmt = ws.Range("A2").NumberFormat
    If dfmt = "General" Then dfmt = "m/d/yyyy"
    last = ws.Cells(ws.Rows.Count, 1).End(xlUp).Row
    If last < 2 Then last = 2
    ws.Range(ws.Cells(2, 1), ws.Cells(last, 7)).ClearContents
    ws.Range("A1:G1").Value = Array("Date", "Observed Flow", "Simulated Flow", "Cumulative Observed", "Cumulative Simulated", _
        "Observed WTD (cm)", "Simulated WTD (cm)")
    ws.Cells(2, 1).Resize(n, 3).Value = data
    ws.Cells(2, 6).Resize(n, 2).Value = wt
    ws.Cells(2, 1).Resize(n, 1).NumberFormat = dfmt
    ws.Range("D2").Formula = "=IF(AND(ISNUMBER(B2),ISNUMBER(C2)),B2,0)"
    ws.Range("E2").Formula = "=IF(AND(ISNUMBER(B2),ISNUMBER(C2)),C2,0)"
    If n > 1 Then
        ws.Range("D3").Resize(n - 1, 1).Formula = "=D2+IF(AND(ISNUMBER(B3),ISNUMBER(C3)),B3,0)"
        ws.Range("E3").Resize(n - 1, 1).Formula = "=E2+IF(AND(ISNUMBER(B3),ISNUMBER(C3)),C3,0)"
    End If
    ' every series that plots rows from row 2 down is resized to the new rows
    On Error Resume Next
    For Each co In ws.ChartObjects
        For Each sr In co.Chart.SeriesCollection
            f = sr.Formula
            f = Mid$(f, InStr(f, "(") + 1)
            f = Left$(f, InStrRev(f, ")") - 1)
            parts = Split(f, ",")
            If UBound(parts) >= 2 Then
                Set rx = Nothing: Set ry = Nothing
                Set rx = Application.Range(CStr(parts(1)))
                Set ry = Application.Range(CStr(parts(2)))
                If Not rx Is Nothing And Not ry Is Nothing Then
                    If rx.Row = 2 And ry.Row = 2 And rx.Worksheet.Name = ws.Name And ry.Worksheet.Name = ws.Name Then
                        sr.XValues = rx.Cells(1, 1).Resize(n, 1)
                        sr.Values = ry.Cells(1, 1).Resize(n, 1)
                    End If
                End If
            End If
        Next sr
    Next co
    On Error GoTo 0
End Sub


' cumulative measured and simulated drain flow on a secondary axis of the
' drain flow chart (dashed lines in the colors of the daily series)
Private Sub AddCumulative(ch As Chart, xr As Range, obsR As Range, simR As Range)
    Dim sr As Series
    On Error Resume Next
    ch.SeriesCollection(1).MarkerBackgroundColor = RGB(68, 114, 196)
    ch.SeriesCollection(1).MarkerForegroundColor = RGB(68, 114, 196)
    ch.SeriesCollection(2).Format.Line.ForeColor.RGB = RGB(237, 125, 49)
    Set sr = ch.SeriesCollection.NewSeries
    sr.Name = "Cumulative measured"
    sr.ChartType = xlXYScatterLinesNoMarkers
    sr.XValues = xr
    sr.Values = obsR
    sr.AxisGroup = xlSecondary
    sr.Format.Line.ForeColor.RGB = RGB(31, 56, 100)
    sr.Format.Line.Weight = 2
    sr.Format.Line.DashStyle = 4   ' msoLineDash
    Set sr = ch.SeriesCollection.NewSeries
    sr.Name = "Cumulative simulated"
    sr.ChartType = xlXYScatterLinesNoMarkers
    sr.XValues = xr
    sr.Values = simR
    sr.AxisGroup = xlSecondary
    sr.Format.Line.ForeColor.RGB = RGB(197, 90, 17)
    sr.Format.Line.Weight = 2
    sr.Format.Line.DashStyle = 4   ' msoLineDash
    ' one date axis for both groups
    ch.HasAxis(xlCategory, xlSecondary) = False
    ch.HasAxis(xlValue, xlSecondary) = True
    With ch.Axes(xlValue, xlSecondary)
        .MinimumScale = 0
        .HasTitle = True
        .AxisTitle.Text = "Cumulative drain flow (cm)"
        .HasMajorGridlines = False
    End With
    ch.Axes(xlValue, xlPrimary).MinimumScale = 0
End Sub

Private Sub AddChart(ws As Worksheet, ByVal title As String, xr As Range, obsR As Range, simR As Range, _
        ByVal yTitle As String, ByVal slot As Integer, ByVal reverseY As Boolean)
    Dim co As ChartObject, sr As Series
    Set co = ws.ChartObjects.Add(ws.Range("G2").Left, ws.Range("G2").Top + slot * 300, 760, 280)
    With co.Chart
        .ChartType = xlXYScatterLinesNoMarkers
        Do While .SeriesCollection.Count > 0
            .SeriesCollection(1).Delete
        Loop
        Set sr = .SeriesCollection.NewSeries
        sr.Name = "Measured"
        sr.XValues = xr
        sr.Values = obsR
        sr.ChartType = xlXYScatter
        sr.MarkerSize = 3
        Set sr = .SeriesCollection.NewSeries
        sr.Name = "Simulated"
        sr.XValues = xr
        sr.Values = simR
        sr.ChartType = xlXYScatterLinesNoMarkers
        .HasTitle = True
        .ChartTitle.Text = title
        .Axes(xlValue).HasTitle = True
        .Axes(xlValue).AxisTitle.Text = yTitle
        .Axes(xlCategory).TickLabels.NumberFormat = "yyyy-mm"
        .Axes(xlCategory).MinimumScale = xr.Cells(1, 1).Value
        .Axes(xlCategory).MaximumScale = xr.Cells(xr.Rows.Count, 1).Value
        If reverseY Then .Axes(xlValue).ReversePlotOrder = True
        .HasLegend = True
        .Legend.Position = xlLegendPositionBottom
    End With
End Sub
