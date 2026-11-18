Attribute VB_Name = "modDrain"
'==========================================================================
' DRAINMOD DRAINAGE DESIGN UNDER CURRENT AND FUTURE WEATHER
'   Runs a DRAINMOD project (its crop sequence, with yields calculated) for
'   every combination of weather set, drain depth and drain spacing, and
'   plots DRAINMOD's average overall relative yield against drain spacing:
'   one chart per crop, one line per drain depth and weather set.
'   Drain depths and spacings are entered and reported in feet.
'
'   The user's .prj/.gen are never changed: each run writes
'   <name>_dsg.prj/.gen next to them (and _dsg2, _dsg3, ... for runs side by
'   side) and reads <name>_dsg.YLD. The simulation years of each weather
'   set come from its weather files (or from the Setup sheet).
'==========================================================================
Option Explicit

Private Const NWMAX As Integer = 16            ' most DRAINMOD runs side by side
Private Const NSETMAX As Integer = 4           ' weather sets (rows of the table)
Private Const NCROPMAX As Integer = 10         ' crops in the sequence
Private Const NYRMAX As Integer = 300          ' years per crop in a yield file
Private Const SYNC_TERMINATE As Long = &H100001 ' SYNCHRONIZE Or PROCESS_TERMINATE
Private Const WAIT_TIMEOUT As Long = &H102
Private Const MAXNAME As Integer = 48         ' longest file name DRAINMOD reads for crops
Private Const CM_PER_FT As Double = 30.48     ' depths and spacings are entered in feet; DRAINMOD uses cm

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
Private wTracked(1 To NWMAX) As Boolean ' Excel could follow the DRAINMOD process
Private wCmd(1 To NWMAX) As String      ' command line of the run
Private wSet(1 To NWMAX) As Integer     ' weather set of the slot's run

' the user's project
Private genLines() As String
Private nGen As Long
Private prjText As String

' weather sets
Private nSet As Integer
Private setName(1 To NSETMAX) As String
Private setRain(1 To NSETMAX) As String
Private setTemp(1 To NSETMAX) As String
Private setRainId(1 To NSETMAX) As String
Private setTempId(1 To NSETMAX) As String
Private setY1(1 To NSETMAX) As Long
Private setM1(1 To NSETMAX) As Long
Private setY2(1 To NSETMAX) As Long
Private setM2(1 To NSETMAX) As Long

' crops of the first run (the charts follow these)
Private nCrop As Integer
Private cropName(1 To NCROPMAX) As String

' the last yield file read: per crop the AVG relative yields
' (1 overall, 2 excess water, 3 drought, 4 planting delay) and the yearly ones
Private yN As Integer
Private yName(1 To NCROPMAX) As String
Private yHasAvg(1 To NCROPMAX) As Boolean
Private yAvg(1 To NCROPMAX, 1 To 4) As Double
Private yYears(1 To NCROPMAX) As Integer
Private yYear(1 To NCROPMAX, 1 To NYRMAX) As Long
Private yVal(1 To NCROPMAX, 1 To NYRMAX, 1 To 4) As Double

Private yldFolder As String              ' where DRAINMOD writes <name>_dsg.YLD
Private yldFixed As String               ' the yield file, if DRAINMOD does not name it after the run's project
Private yearlyRow As Long                ' next free row on the Yearly sheet

' a project that was moved: its files still name oldRoot (e.g. C:\DDESIGN\),
' it now sits under newRoot; both "" when the paths match
Private oldRoot As String
Private newRoot As String

'============================================================ buttons ====
Public Sub EnsureButtons()
    Dim ws As Worksheet
    Set ws = ThisWorkbook.Worksheets("Setup")
    AddButton ws, "btnBrowsePrj", "Browse...", "BrowseProject", ws.Range("C3"), 90
    AddButton ws, "btnBrowseExe", "Browse...", "BrowseProgram", ws.Range("C4"), 90
    AddButton ws, "btnFixPaths", "Fix paths...", "FixProjectPaths", ws.Range("C5"), 90
    AddButton ws, "btnRain1", "Rainfall file...", "BrowseRain1", ws.Range("I10"), 105
    AddButton ws, "btnTemp1", "Temperature file...", "BrowseTemp1", ws.Range("J10"), 105
    AddButton ws, "btnRain2", "Rainfall file...", "BrowseRain2", ws.Range("I11"), 105
    AddButton ws, "btnTemp2", "Temperature file...", "BrowseTemp2", ws.Range("J11"), 105
    AddButton ws, "btnRead", "1. Read project and weather files", "ReadInputs", ws.Range("D14"), 210
    AddButton ws, "btnRun", "2. Run design analysis", "RunDesign", ws.Range("D16"), 210
    AddButton ws, "btnCharts", "3. Redraw charts", "DrawCharts", ws.Range("D18"), 210
    EnsureChartOptions
End Sub

' four large buttons on the Charts sheet, used like radio buttons: which
' relative yield the charts show. The chosen one is blue with a filled circle.
' (Excel's own option buttons cannot be made bigger or bold.)
Private Sub EnsureChartOptions()
    Dim ws As Worksheet, caps As Variant, i As Integer, sh As Shape, anyOn As Boolean
    Set ws = ThisWorkbook.Worksheets("Charts")
    caps = Array("Overall", "Excess water", "Drought", "Planting delay")
    For i = 1 To 4
        ' the small option buttons of earlier versions
        On Error Resume Next
        ws.OptionButtons("optYield" & i).Delete
        Set sh = Nothing
        Set sh = ws.Shapes("btnYield" & i)
        On Error GoTo 0
        If sh Is Nothing Then
            Set sh = ws.Shapes.AddShape(msoShapeRoundedRectangle, 12 + (i - 1) * 150, 6, 142, 30)
            sh.Name = "btnYield" & i
            sh.Placement = xlFreeFloating
        End If
        sh.OnAction = "ShowYieldComponent"
        sh.Line.Visible = msoTrue
        sh.Line.ForeColor.RGB = RGB(68, 114, 196)
        sh.Line.Weight = 1.5
        With sh.TextFrame2
            .VerticalAnchor = msoAnchorMiddle
            .MarginLeft = 4: .MarginRight = 4
            .TextRange.ParagraphFormat.Alignment = msoAlignCenter
            .TextRange.Font.Size = 13
            .TextRange.Font.Bold = msoTrue
        End With
        sh.title = caps(i - 1)
        If sh.AlternativeText = "on" Then anyOn = True
    Next i
    If Not anyOn Then ws.Shapes("btnYield1").AlternativeText = "on"
    PaintChartOptions
End Sub

' colour the four buttons by which one is chosen
Private Sub PaintChartOptions()
    Dim ws As Worksheet, i As Integer, sh As Shape, isOn As Boolean
    Set ws = ThisWorkbook.Worksheets("Charts")
    For i = 1 To 4
        Set sh = ws.Shapes("btnYield" & i)
        isOn = (sh.AlternativeText = "on")
        If isOn Then
            sh.Fill.ForeColor.RGB = RGB(68, 114, 196)
            sh.TextFrame2.TextRange.Text = ChrW(&H25C9) & "  " & sh.title
            sh.TextFrame2.TextRange.Font.Fill.ForeColor.RGB = RGB(255, 255, 255)
        Else
            sh.Fill.ForeColor.RGB = RGB(242, 242, 242)
            sh.TextFrame2.TextRange.Text = ChrW(&H25CB) & "  " & sh.title
            sh.TextFrame2.TextRange.Font.Fill.ForeColor.RGB = RGB(64, 64, 64)
        End If
    Next i
End Sub

' 1 overall, 2 excess water, 3 drought, 4 planting delay (the button chosen)
Private Function YieldComponent() As Integer
    Dim i As Integer
    YieldComponent = 1
    On Error Resume Next
    For i = 1 To 4
        If ThisWorkbook.Worksheets("Charts").Shapes("btnYield" & i).AlternativeText = "on" Then YieldComponent = i
    Next i
End Function

' a yield button was clicked: choose it and redraw the charts for that component
Public Sub ShowYieldComponent()
    Dim ws As Worksheet, nm As String, i As Integer
    Set ws = ThisWorkbook.Worksheets("Charts")
    On Error Resume Next
    nm = CStr(Application.Caller)
    On Error GoTo 0
    If Left$(nm, 8) = "btnYield" Then
        For i = 1 To 4
            If ws.Shapes("btnYield" & i).Name = nm Then ws.Shapes("btnYield" & i).AlternativeText = "on" Else ws.Shapes("btnYield" & i).AlternativeText = ""
        Next i
    End If
    DrawCharts
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
    b.caption = caption
    b.OnAction = macro
End Sub

'=========================================================== browsing ====
Public Sub BrowseProject()
    Dim f As Variant
    f = Application.GetOpenFilename("DRAINMOD project (*.prj),*.prj", , "Select the DRAINMOD project")
    If f = False Then Exit Sub
    Cfg("PrjFile").Value = f
    Cfg("GenFile").Value = ""
    Cfg("CropList").Value = ""
    Cfg("ProjGeom").Value = ""
    GuessProgram True
End Sub

Private Sub BrowseWeather(ByVal r As Integer, ByVal c As Integer, ByVal filt As String, ByVal ttl As String)
    Dim f As Variant, cur As String, wf As String
    wf = ProjectSubfolder("Weather")
    cur = Trim$(CStr(Cfg("WeatherTable").Cells(r, c).Value))
    If wf <> "" Then
        GoToFolder wf
    ElseIf FileExists(cur) Then
        GoToFolder FolderOf(cur)
    End If
    f = Application.GetOpenFilename(filt, , ttl)
    If f = False Then Exit Sub
    Cfg("WeatherTable").Cells(r, c).Value = f
    Cfg("WeatherTable").Cells(r, 8).Value = ""
End Sub

Public Sub BrowseRain1()
    BrowseWeather 1, 2, "DRAINMOD rainfall (*.rai),*.rai,All files (*.*),*.*", "Rainfall file of weather set 1"
End Sub

Public Sub BrowseTemp1()
    BrowseWeather 1, 3, "DRAINMOD temperature (*.tem),*.tem,All files (*.*),*.*", "Temperature file of weather set 1"
End Sub

Public Sub BrowseRain2()
    BrowseWeather 2, 2, "DRAINMOD rainfall (*.rai),*.rai,All files (*.*),*.*", "Rainfall file of weather set 2"
End Sub

Public Sub BrowseTemp2()
    BrowseWeather 2, 3, "DRAINMOD temperature (*.tem),*.tem,All files (*.*),*.*", "Temperature file of weather set 2"
End Sub

'======================================================= project files ====
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

' a file named on the Setup sheet under the project's old place, moved
Private Sub RemapCell(ByVal nm As String)
    Dim v As String
    v = Trim$(CStr(Cfg(nm).Value))
    If v = "" Or oldRoot = "" Then Exit Sub
    If Not FileExists(v) And FileExists(MapPath(v)) Then Cfg(nm).Value = MapPath(v)
End Sub

' rewrite the paths in the project's own files (.prj, .gen and the input files
' the .prj names: crops, nitrogen, phosphorus, salinity, forest), each saved
' first as <file>.bak; returns the files changed, one per line
Private Function DoFixPaths() As String
    Dim files As New Collection, lines As Variant, i As Long, l As String, v As String, ext As String
    Dim f As Variant, txt As String, Done As String
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
            Done = Done & CStr(f) & vbLf
        End If
    Next f
    DoFixPaths = Done
End Function

' ask, then rewrite the project's files for where it is now
Private Sub OfferFixPaths(ByVal always As Boolean)
    Dim ans As VbMsgBoxResult, Done As String
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
    Done = DoFixPaths()
    LoadProject
    If Done = "" Then
        MsgBox "No file needed changing.", vbInformation
    Else
        MsgBox "Paths rewritten in:" & vbLf & Done, vbInformation
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

' value after "key=" in a section of the project file ("" if absent)
Private Function PrjValue(ByVal section As String, ByVal key As String) As String
    Dim lines As Variant, i As Long, l As String, sec As String
    lines = SplitLines(prjText)
    For i = 0 To UBound(lines)
        l = Trim$(lines(i))
        If Left$(l, 1) = "[" Then
            sec = LCase$(l)
        ElseIf sec = LCase$(section) And LCase$(Left$(l, Len(key) + 1)) = LCase$(key) & "=" Then
            PrjValue = Trim$(Mid$(l, Len(key) + 2))
            Exit Function
        End If
    Next i
End Function

' crop files listed in the [Crops] section of the project, as names
Private Function CropListText() As String
    Dim lines As Variant, i As Long, l As String, sec As String, s As String, f As String
    lines = SplitLines(prjText)
    For i = 0 To UBound(lines)
        l = Trim$(lines(i))
        If Left$(l, 1) = "[" Then
            sec = LCase$(l)
        ElseIf sec = "[crops]" And LCase$(l) Like "crop#*=*" Then
            f = Trim$(Mid$(l, InStr(l, "=") + 1))
            If f <> "" Then
                If s <> "" Then s = s & ", "
                s = s & BaseName(f)
            End If
        End If
    Next i
    CropListText = s
End Function

' station ID and file of climate line n (1 = rainfall, 2 = temperature)
Private Sub ClimateFile(ByVal n As Integer, ByRef id As String, ByRef path As String)
    Dim ln As String, p As Long
    ln = Trim$(genLines(ClimateIdx(genLines) + n))
    p = InStr(ln, " ")
    If p = 0 Then
        id = "": path = ln
    Else
        id = Left$(ln, p - 1): path = Trim$(Mid$(ln, p + 1))
    End If
End Sub

Private Function WorkSuffix(ByVal w As Integer) As String
    If w > 1 Then WorkSuffix = "_dsg" & w Else WorkSuffix = "_dsg"
End Function

' run slot w: <prj name>_dsg<w>.prj next to the project, .gen next to the .gen,
' both with the project's name so DRAINMOD's output is <prj name>_dsg<w>.*
Private Function WorkPrjPath(ByVal w As Integer) As String
    WorkPrjPath = FolderOf(CStr(Cfg("PrjFile").Value)) & BaseName(CStr(Cfg("PrjFile").Value)) & WorkSuffix(w) & ".prj"
End Function

Private Function WorkGenPath(ByVal w As Integer) As String
    WorkGenPath = FolderOf(CStr(Cfg("GenFile").Value)) & BaseName(CStr(Cfg("PrjFile").Value)) & WorkSuffix(w) & ".gen"
End Function

Private Function YldPath(ByVal w As Integer) As String
    If yldFixed <> "" Then
        YldPath = yldFixed
    ElseIf yldFolder <> "" Then
        YldPath = yldFolder & BaseName(WorkPrjPath(w)) & ".YLD"
    End If
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

' write run slot w's project: weather set s, drain depth dFt, drain spacing spFt (feet)
Private Sub WriteDesignProject(ByVal w As Integer, ByVal s As Integer, ByVal dFt As Double, ByVal spFt As Double)
    Dim g() As String, i As Long, k As Long, lines As Variant, l As String, sec As String, parts As Variant
    Dim d As Double, sp As Double
    d = dFt * CM_PER_FT
    sp = spFt * CM_PER_FT
    ReDim g(0 To nGen - 1)
    For i = 0 To nGen - 1
        g(i) = genLines(i)
    Next i
    SetDrainField g, 1, d
    SetDrainField g, 3, sp
    RecomputeDrainGeometry g
    k = ClimateIdx(g)
    g(k + 1) = setRainId(s) & " " & setRain(s)
    g(k + 2) = setTempId(s) & " " & setTemp(s)
    SetClimatePeriod g, setY1(s), setM1(s), setY2(s), setM2(s)
    WriteAllText WorkGenPath(w), Join(g, vbCrLf) & vbCrLf
    lines = SplitLines(prjText)
    For i = 0 To UBound(lines)
        lines(i) = FitPrjLine(MapText(CStr(lines(i))))
        l = LCase$(Trim$(lines(i)))
        If Left$(l, 1) = "[" Then sec = l
        If Right$(l, 4) = ".gen" And InStr(l, "=") > 0 Then
            lines(i) = Left$(lines(i), InStr(lines(i), "=")) & WorkGenPath(w)
        ElseIf sec = "[options]" And Left$(l, 6) = "yield=" Then
            lines(i) = "Yield=1"
        ElseIf sec = "[weather]" And Left$(l, 9) = "rainfall=" Then
            lines(i) = "Rainfall=" & FitName(setRain(s))
        ElseIf sec = "[weather]" And Left$(l, 12) = "temperature=" Then
            lines(i) = "Temperature=" & FitName(setTemp(s))
        ElseIf Left$(l, 11) = "draindepth=" Then
            parts = Split(Mid$(Trim$(lines(i)), 12), ",")
            If UBound(parts) >= 2 Then lines(i) = "DrainDepth=" & RJ(NumStr(d, 2), Len(parts(0))) & "," & RJ(NumStr(d, 2), Len(parts(1))) & "," & parts(2)
        ElseIf Left$(l, 11) = "drainspace=" Then
            parts = Split(Mid$(Trim$(lines(i)), 12), ",")
            If UBound(parts) >= 2 Then lines(i) = "DrainSpace=" & RJ(NumStr(sp, 2), Len(parts(0))) & "," & RJ(NumStr(sp, 2), Len(parts(1))) & "," & parts(2)
        End If
    Next i
    WriteAllText WorkPrjPath(w), Join(lines, vbCrLf) & vbCrLf
End Sub

'===================================================== weather files ====
' year and month of a weather line in one of these layouts:
'   fix7 DRAINMOD's own: station in columns 1-6, year 8-11, month 12-13
'        (so months 10-12 run into the year: "170019 201012 ...")
'   fix  station in columns 1-6, year 7-10, month 11-12 (no spaces between)
'   sym  station year month day ...      ym   year month day ...
'   syj  station year day-of-year ...    yj   year day-of-year ...
Private Function LineYM(ByVal ln As String, ByVal lay As String, ByRef y As Long, ByRef m As Long) As Boolean
    Dim t As Variant, n As Long, doy As Long, ys As String, ms As String
    t = Tokens(ln)
    n = UBound(t)
    Select Case lay
        Case "sym", "syj"
            If n < 2 Then Exit Function
            ys = CStr(t(1)): ms = CStr(t(2))
        Case "ym", "yj"
            If n < 1 Then Exit Function
            ys = CStr(t(0)): ms = CStr(t(1))
        Case "fix7"
            ys = Trim$(Mid$(ln, 8, 4)): ms = Trim$(Mid$(ln, 12, 2))
        Case "fix"
            ys = Trim$(Mid$(ln, 7, 4)): ms = Trim$(Mid$(ln, 11, 2))
    End Select
    If Not (IsIntTok(ys) And IsIntTok(ms)) Then Exit Function
    If Len(ys) <> 4 Or Len(ms) > 3 Then Exit Function
    y = CLng(ys)
    If y < 1800 Or y > 2300 Then Exit Function
    If lay = "syj" Or lay = "yj" Then
        doy = CLng(ms)
        If doy < 1 Or doy > 366 Then Exit Function
        m = Month(DateSerial(y, 1, doy))
    Else
        m = CLng(ms)
        If m < 1 Or m > 12 Then Exit Function
    End If
    LineYM = True
End Function

' a line that starts with a number (headers and notes are skipped)
Private Function IsWeatherLine(ByVal ln As String) As Boolean
    Dim t As Variant
    t = Tokens(ln)
    If UBound(t) < 1 Then Exit Function
    IsWeatherLine = IsIntTok(Replace(CStr(t(0)), "-", ""))
End Function

' first and last year/month of a weather file; returns "" or why it failed.
' Lines without a date (the second line of each month in a .TEM file) are
' skipped. Of the first 400 lines that start with a number, the layout that
' dates the most of them, never going back in time, is used (at least 40%).
Private Function WeatherSpan(ByVal path As String, ByRef y1 As Long, ByRef m1 As Long, ByRef y2 As Long, ByRef m2 As Long) As String
    Dim lines As Variant, i As Long, lay As Variant, found As String, ok As Boolean, prev As Long, cnt As Long
    Dim y As Long, m As Long, nNum As Long, best As Double, frac As Double
    If Not FileExists(path) Then
        WeatherSpan = "The weather file was not found: " & path
        Exit Function
    End If
    lines = SplitLines(ReadAllText(path))
    For Each lay In Array("fix7", "fix", "sym", "ym", "syj", "yj")
        ok = True: prev = 0: cnt = 0: nNum = 0
        For i = 0 To UBound(lines)
            If IsWeatherLine(CStr(lines(i))) Then
                nNum = nNum + 1
                If LineYM(CStr(lines(i)), CStr(lay), y, m) Then
                    If y * 12 + m < prev Then ok = False: Exit For
                    prev = y * 12 + m
                    cnt = cnt + 1
                End If
                If nNum >= 400 Then Exit For
            End If
        Next i
        If ok And nNum > 0 Then
            frac = cnt / nNum
            If frac >= 0.4 And frac > best + 0.000001 Then
                best = frac
                found = CStr(lay)
            End If
        End If
    Next lay
    If found = "" Then
        WeatherSpan = "Could not find the dates in " & path & ". Enter the first and last year of this weather set on the Setup sheet."
        Exit Function
    End If
    For i = 0 To UBound(lines)
        If IsWeatherLine(CStr(lines(i))) Then
            If LineYM(CStr(lines(i)), found, y1, m1) Then Exit For
        End If
    Next i
    For i = UBound(lines) To 0 Step -1
        If IsWeatherLine(CStr(lines(i))) Then
            If LineYM(CStr(lines(i)), found, y2, m2) Then Exit For
        End If
    Next i
End Function

' read the weather table: files, station IDs and the period each set covers
Private Sub LoadSets()
    Dim tbl As Range, r As Long, rf As String, tf As String, msg As String, nm As String
    Dim ry1 As Long, rm1 As Long, ry2 As Long, rm2 As Long, ty1 As Long, tm1 As Long, ty2 As Long, tm2 As Long
    Dim a As Long, b As Long, fy As Variant, ly As Variant, defR As String, defT As String, p As String
    Dim hasF As Boolean, hasL As Boolean, spanOK As Boolean, cover As String, fileY1 As Long, fileM1 As Long, fileY2 As Long, fileM2 As Long
    Set tbl = Cfg("WeatherTable")
    ClimateFile 1, defR, p
    ClimateFile 2, defT, p
    nSet = 0
    For r = 1 To tbl.Rows.Count
        rf = Trim$(CStr(tbl.Cells(r, 2).Value)): tf = Trim$(CStr(tbl.Cells(r, 3).Value))
        ' files named under the project's old place
        If rf <> "" Then
            If Not FileExists(rf) And FileExists(MapPath(rf)) Then rf = MapPath(rf): tbl.Cells(r, 2).Value = rf
        End If
        If tf <> "" Then
            If Not FileExists(tf) And FileExists(MapPath(tf)) Then tf = MapPath(tf): tbl.Cells(r, 3).Value = tf
        End If
        nm = Trim$(CStr(tbl.Cells(r, 1).Value))
        If nm = "" Then nm = "Set " & r
        If rf <> "" Or tf <> "" Then
            If rf = "" Or tf = "" Then Err.Raise vbObjectError + 801, "Weather", "Give both a rainfall and a temperature file for the weather set '" & nm & "'."
            If nSet >= NSETMAX Then Err.Raise vbObjectError + 802, "Weather", "At most " & NSETMAX & " weather sets."
            nSet = nSet + 1
            setName(nSet) = nm: setRain(nSet) = rf: setTemp(nSet) = tf
            If Trim$(CStr(tbl.Cells(r, 4).Value)) = "" Then tbl.Cells(r, 4).Value = defR
            If Trim$(CStr(tbl.Cells(r, 5).Value)) = "" Then tbl.Cells(r, 5).Value = defT
            setRainId(nSet) = Trim$(CStr(tbl.Cells(r, 4).Value))
            setTempId(nSet) = Trim$(CStr(tbl.Cells(r, 5).Value))
            fy = tbl.Cells(r, 6).Value: ly = tbl.Cells(r, 7).Value
            hasF = False: hasL = False
            If IsNumeric(fy) And Not IsEmpty(fy) Then hasF = (Trim$(CStr(fy)) <> "")
            If IsNumeric(ly) And Not IsEmpty(ly) Then hasL = (Trim$(CStr(ly)) <> "")
            ' the years both files cover
            spanOK = False
            msg = WeatherSpan(rf, ry1, rm1, ry2, rm2)
            If msg = "" Then msg = WeatherSpan(tf, ty1, tm1, ty2, tm2)
            If msg = "" Then
                a = ry1 * 12 + rm1: If ty1 * 12 + tm1 > a Then a = ty1 * 12 + tm1
                b = ry2 * 12 + rm2: If ty2 * 12 + tm2 < b Then b = ty2 * 12 + tm2
                setY1(nSet) = (a - 1) \ 12: setM1(nSet) = a - 12 * setY1(nSet)
                setY2(nSet) = (b - 1) \ 12: setM2(nSet) = b - 12 * setY2(nSet)
                spanOK = True
            ElseIf Not (hasF And hasL) Then
                Err.Raise vbObjectError + 803, "Weather", msg
            End If
            ' years typed on the Setup sheet, if they lie inside what the files cover
            cover = ""
            If spanOK Then cover = "Its weather files cover " & setY1(nSet) & "-" & Format$(setM1(nSet), "00") & " to " & _
                setY2(nSet) & "-" & Format$(setM2(nSet), "00") & ". Change the year or clear the cell to use the years in the files."
            fileY1 = setY1(nSet): fileM1 = setM1(nSet): fileY2 = setY2(nSet): fileM2 = setM2(nSet)
            If hasF Then
                If spanOK And (CLng(fy) < fileY1 Or CLng(fy) > fileY2) Then Err.Raise vbObjectError + 806, "Weather", _
                    "The first year entered for '" & nm & "' (" & CLng(fy) & ") is outside its weather files. " & cover
                setY1(nSet) = CLng(fy): setM1(nSet) = 1
                If spanOK And CLng(fy) = fileY1 Then setM1(nSet) = fileM1
            End If
            If hasL Then
                If spanOK And (CLng(ly) > fileY2 Or CLng(ly) < fileY1) Then Err.Raise vbObjectError + 806, "Weather", _
                    "The last year entered for '" & nm & "' (" & CLng(ly) & ") is outside its weather files. " & cover
                setY2(nSet) = CLng(ly): setM2(nSet) = 12
                If spanOK And CLng(ly) = fileY2 Then setM2(nSet) = fileM2
            End If
            If setY2(nSet) * 12 + setM2(nSet) < setY1(nSet) * 12 + setM1(nSet) Then Err.Raise vbObjectError + 804, "Weather", _
                "The rainfall and temperature files of '" & nm & "' have no years in common."
            tbl.Cells(r, 8).Value = setY1(nSet) & "-" & Format$(setM1(nSet), "00") & " to " & setY2(nSet) & "-" & Format$(setM2(nSet), "00")
        End If
    Next r
    If nSet = 0 Then Err.Raise vbObjectError + 805, "Weather", "Select the rainfall and temperature files of at least one weather set on the Setup sheet."
End Sub

Public Sub ReadInputs()
    Dim id As String, path As String, tbl As Range
    On Error GoTo Failed
    LoadProject
    OfferFixPaths False
    Cfg("CropList").Value = CropListText()
    Cfg("ProjGeom").Value = NumStr(DrainField(genLines, 1) / CM_PER_FT, 2) & " / " & NumStr(DrainField(genLines, 3) / CM_PER_FT, 1) & " / " & _
        NumStr(ImpDepth(genLines) / CM_PER_FT, 2) & " ft   (" & NumStr(DrainField(genLines, 1), 1) & " / " & NumStr(DrainField(genLines, 3), 0) & " / " & _
        NumStr(ImpDepth(genLines), 1) & " cm)"
    ' the project's own weather is the default for the first set
    Set tbl = Cfg("WeatherTable")
    If Trim$(CStr(tbl.Cells(1, 2).Value)) = "" And Trim$(CStr(tbl.Cells(1, 3).Value)) = "" Then
        ClimateFile 1, id, path
        tbl.Cells(1, 2).Value = path
        ClimateFile 2, id, path
        tbl.Cells(1, 3).Value = path
    End If
    LoadSets
    If Trim$(CStr(Cfg("DmExe").Value)) = "" Then GuessProgram
    SetStatus "Read " & Cfg("GenFile").Value & " and " & nSet & " weather set(s)."
    Application.StatusBar = False
    Exit Sub
Failed:
    Application.StatusBar = False
    MsgBox "Could not read the inputs: " & Err.Description, vbExclamation
End Sub

'=========================================================== ranges ====
' values from <prefix>Min to <prefix>Max in steps of <prefix>Step (Max included)
Private Sub RangeValues(ByVal prefix As String, ByVal label As String, v() As Double, ByRef n As Integer)
    Dim a As Double, b As Double, st As Double, x As Double
    If Not IsNumeric(Cfg(prefix & "Min").Value) Or IsEmpty(Cfg(prefix & "Min").Value) Then Err.Raise vbObjectError + 810, "Range", "Enter the smallest " & label & " on the Setup sheet."
    a = Cfg(prefix & "Min").Value
    b = a
    If IsNumeric(Cfg(prefix & "Max").Value) And Not IsEmpty(Cfg(prefix & "Max").Value) Then b = Cfg(prefix & "Max").Value
    st = 0
    If IsNumeric(Cfg(prefix & "Step").Value) And Not IsEmpty(Cfg(prefix & "Step").Value) Then st = Cfg(prefix & "Step").Value
    If a <= 0 Then Err.Raise vbObjectError + 811, "Range", "The smallest " & label & " must be above zero."
    If b < a Then Err.Raise vbObjectError + 812, "Range", "The largest " & label & " must not be smaller than the smallest."
    If b > a And st <= 0 Then Err.Raise vbObjectError + 813, "Range", "Enter a step above zero for the " & label & "."
    n = 0
    ReDim v(1 To 101)
    x = a
    Do While x <= b + 0.000001
        n = n + 1
        If n > 100 Then Err.Raise vbObjectError + 814, "Range", "More than 100 values of " & label & "; use a larger step."
        v(n) = x
        If st <= 0 Then Exit Do
        x = a + n * st
    Loop
    If v(n) < b - 0.000001 Then
        n = n + 1
        v(n) = b
    End If
End Sub

'====================================================== yield output ====
' DRAINMOD has finished a file: it exists, nothing else has it open, and its
' size did not change for a moment
Private Function FileDone(ByVal p As String) As Boolean
    Dim f As Integer, n1 As Long
    If Not FileExists(p) Then Exit Function
    On Error GoTo busy
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
busy:
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

' crops named in the [Crops] section of the project (0 if none are listed)
Private Function ExpectedCrops() As Integer
    Dim lines As Variant, i As Long, l As String, sec As String, n As Integer
    lines = SplitLines(prjText)
    For i = 0 To UBound(lines)
        l = Trim$(lines(i))
        If Left$(l, 1) = "[" Then
            sec = LCase$(l)
        ElseIf sec = "[crops]" And LCase$(l) Like "crop#*=?*" Then
            n = n + 1
        End If
    Next i
    ExpectedCrops = n
End Function

' read a finished yield file; if it lists fewer crops than the project has,
' wait for DRAINMOD and read it again once
Private Function ReadYieldDone(ByVal p As String, ByVal s As Integer) As String
    Dim want As Integer, yrs As Long
    If Not WaitFileDone(p) Then
        ReadYieldDone = "DRAINMOD did not finish writing " & p & " within the time limit"
        Exit Function
    End If
    ReadYieldDone = ReadYield(p)
    ' each crop of the sequence needs a year of its own
    want = ExpectedCrops()
    yrs = setY2(s) - setY1(s) + 1
    If yrs < want Then want = yrs
    If ReadYieldDone = "" And want > 0 And yN < want Then
        Sleep 1000
        If WaitFileDone(p) Then ReadYieldDone = ReadYield(p)
        If ReadYieldDone = "" And yN < want Then
            ReadYieldDone = "the yield file lists " & yN & " of the project's " & ExpectedCrops() & " crops (" & p & "). " & YieldExplain(p, s)
        End If
    End If
End Function

' what the yield file and the run's .OUT show, for a run that lost a crop
Private Function YieldExplain(ByVal p As String, ByVal s As Integer) As String
    Dim c As Integer, t As String, o As String, lines As Variant, k As Long, tail As String, cnt As Integer
    t = "Simulated " & setY1(s) & "-" & Format$(setM1(s), "00") & " to " & setY2(s) & "-" & Format$(setM2(s), "00") & _
        " with " & setName(s) & " weather. Years in the yield file:"
    For c = 1 To yN
        If yYears(c) > 0 Then
            t = t & " " & yName(c) & " " & yYear(c, 1) & "-" & yYear(c, yYears(c)) & " (" & yYears(c) & " years);"
        Else
            t = t & " " & yName(c) & " none;"
        End If
    Next c
    If (setY2(s) - setY1(s) + 1) > 2 * yYears(1) + 1 And yN > 0 Then t = t & " DRAINMOD seems to have stopped early: check that the weather files cover the whole period."
    o = FolderOf(p) & BaseName(p) & ".OUT"
    If FileExists(o) Then
        lines = SplitLines(ReadAllText(o))
        For k = UBound(lines) To 0 Step -1
            If Trim$(lines(k)) <> "" Then
                tail = Trim$(lines(k)) & " / " & tail
                cnt = cnt + 1
                If cnt >= 5 Then Exit For
            End If
        Next k
        t = t & " End of " & o & ": " & tail
    End If
    YieldExplain = t
End Function

' read a DRAINMOD .YLD file into yN, yName, yAvg, yYear, yVal; "" or why not
Private Function ReadYield(ByVal path As String) As String
    Dim lines As Variant, i As Long, tl As String, t As Variant, n As Long, c As Integer, k As Integer
    lines = SplitLines(ReadAllText(path))
    yN = 0: c = 0
    For i = 0 To UBound(lines)
        tl = Trim$(lines(i))
        If LCase$(Right$(tl, 4)) = ".cin" Then
            If yN >= NCROPMAX Then
                ReadYield = "more than " & NCROPMAX & " crops in " & path
                Exit Function
            End If
            yN = yN + 1: c = yN
            yName(c) = BaseName(tl): yHasAvg(c) = False: yYears(c) = 0
        ElseIf c > 0 Then
            t = Tokens(tl)
            n = UBound(t)
            If n >= 10 Then
                If UCase$(CStr(t(0))) = "AVG" Then
                    yAvg(c, 1) = Val(t(n)): yAvg(c, 2) = Val(t(n - 4)): yAvg(c, 3) = Val(t(n - 3)): yAvg(c, 4) = Val(t(n - 2))
                    yHasAvg(c) = True
                ElseIf IsIntTok(CStr(t(0))) And Len(CStr(t(0))) = 4 And yYears(c) < NYRMAX Then
                    yYears(c) = yYears(c) + 1: k = yYears(c)
                    yYear(c, k) = CLng(t(0))
                    yVal(c, k, 1) = Val(t(n)): yVal(c, k, 2) = Val(t(n - 4)): yVal(c, k, 3) = Val(t(n - 3)): yVal(c, k, 4) = Val(t(n - 2))
                End If
            End If
        End If
    Next i
    If yN = 0 Then
        ReadYield = "no crop yields in " & path & " (is yield calculation on, with crops in the project?)"
        Exit Function
    End If
    For c = 1 To yN
        If Not yHasAvg(c) Then
            ReadYield = "no average yield for " & yName(c) & " in " & path
            Exit Function
        End If
    Next c
End Function

' seconds since t (a Timer value), across midnight
Private Function SecondsSince(ByVal t As Double) As Double
    SecondsSince = Timer - t
    If SecondsSince < 0 Then SecondsSince = SecondsSince + 86400
End Function

' folders where DRAINMOD may write its output, each once, with a trailing "\"
Private Function OutputFolders() As Collection
    Dim c As New Collection, cand As Variant, f As String, op As String, i As Long, seen As Boolean
    op = MapPath(PrjValue("Path", "Outpath"))
    If op <> "" And Right$(op, 1) <> "\" Then op = op & "\"
    For Each cand In Array(OutputFolder(), op, FolderOf(CStr(Cfg("PrjFile").Value)), FolderOf(CStr(Cfg("GenFile").Value)), FolderOf(CStr(Cfg("DmExe").Value)))
        f = CStr(cand)
        If f <> "" Then
            seen = False
            For i = 1 To c.Count
                If LCase$(c(i)) = LCase$(f) Then seen = True
            Next i
            If Not seen Then c.Add f
        End If
    Next cand
    Set OutputFolders = c
End Function

' find the yield file of the first run: <name>_dsg.YLD in one of the output
' folders, or else any .YLD this run wrote (then DRAINMOD names it some other
' way and runs cannot go side by side). Waits up to 30 s for it to appear.
Private Sub FindYieldFile()
    Dim fol As Collection, i As Long, f As String, nm As String, t As Double, found As String, nFound As Integer
    nm = BaseName(WorkPrjPath(1)) & ".YLD"
    Set fol = OutputFolders()
    yldFolder = "": yldFixed = ""
    t = Timer
    Do
        For i = 1 To fol.Count
            If Dir(fol(i) & nm) <> "" Then
                If FileDateTime(fol(i) & nm) >= wStart(1) - TimeSerial(0, 0, 5) Then
                    yldFolder = fol(i)
                    Exit Sub
                End If
            End If
        Next i
        found = "": nFound = 0
        For i = 1 To fol.Count
            f = Dir(fol(i) & "*.YLD")
            Do While f <> ""
                If FileDateTime(fol(i) & f) >= wStart(1) - TimeSerial(0, 0, 5) Then
                    found = fol(i) & f
                    nFound = nFound + 1
                End If
                f = Dir
            Loop
        Next i
        If nFound = 1 Then
            yldFixed = found
            yldFolder = FolderOf(found)
            Exit Sub
        End If
        If SecondsSince(t) > 30 Then Exit Do
        DoEvents
        Sleep 250
    Loop
    Err.Raise vbObjectError + 820, "DRAINMOD", RunDiagnosis(1, nm, fol)
End Sub

' what the first run did, for the error message
Private Function RunDiagnosis(ByVal w As Integer, ByVal nm As String, fol As Collection) As String
    Dim s As String, i As Long, f As String, n As Integer, outF As String, outT As Date, lst As String
    Dim lines As Variant, k As Long, tail As String, cnt As Integer
    s = "DRAINMOD did not write the yield file " & nm & " for the first run."
    For i = 1 To fol.Count
        f = Dir(fol(i) & "*.*")
        Do While f <> ""
            If FileDateTime(fol(i) & f) >= wStart(w) - TimeSerial(0, 0, 5) Then
                If LCase$(Right$(f, 4)) <> ".gen" And LCase$(Right$(f, 4)) <> ".prj" Then
                    n = n + 1
                    If n <= 12 Then lst = lst & IIf(lst = "", "", ", ") & f
                    If LCase$(Right$(f, 4)) = ".out" Then
                        If outF = "" Or FileDateTime(fol(i) & f) > outT Then outF = fol(i) & f: outT = FileDateTime(fol(i) & f)
                    End If
                End If
            End If
            f = Dir
        Loop
    Next i
    If n = 0 Then
        s = s & vbLf & vbLf & "DRAINMOD wrote no output files at all during the run. Check that the program on the Setup sheet is dmhydro.exe" & _
            " and that " & WorkPrjPath(w) & " opens and runs in DRAINMOD."
    Else
        s = s & vbLf & vbLf & "Files written during the run: " & lst & IIf(n > 12, ", ...", "") & "."
    End If
    If outF <> "" Then
        lines = SplitLines(ReadAllText(outF))
        For k = UBound(lines) To 0 Step -1
            If Trim$(lines(k)) <> "" Then
                tail = Trim$(lines(k)) & vbLf & tail
                cnt = cnt + 1
                If cnt >= 6 Then Exit For
            End If
        Next k
        s = s & vbLf & vbLf & "End of " & outF & ":" & vbLf & tail
    End If
    If Not wTracked(w) Then s = s & vbLf & "(Excel could not follow the DRAINMOD process, so it could not wait for the run to finish.)"
    s = s & vbLf & "Folders searched: "
    For i = 1 To fol.Count
        s = s & IIf(i > 1, "; ", "") & fol(i)
    Next i
    s = s & vbLf & "Command: " & wCmd(w)
    RunDiagnosis = s
End Function


'=================================================== runs and slots ====
' write slot w's project and start DRAINMOD on it
Private Sub LaunchJob(ByVal w As Integer, ByVal s As Integer, ByVal d As Double, ByVal sp As Double)
    Dim op As String
    If wHandle(w) <> 0 Then
        TerminateProcess wHandle(w), 1
        CloseHandle wHandle(w)
        wHandle(w) = 0
    End If
    wFail(w) = ""
    wSet(w) = s
    WriteDesignProject w, s, d, sp
    ' remove the slot's previous yield file, so only this run's can be read
    op = YldPath(w)
    If op <> "" Then
        If Dir(op) <> "" Then
            On Error Resume Next
            Kill op
            On Error GoTo 0
        End If
    End If
    StartRun w, WorkPrjPath(w)
End Sub

' as LaunchJob, but a failure is noted for the slot; Esc (error 18) is passed on
Private Sub SafeLaunch(ByVal w As Integer, ByVal s As Integer, ByVal d As Double, ByVal sp As Double)
    On Error GoTo Failed
    LaunchJob w, s, d, sp
    Exit Sub
Failed:
    If Err.Number = 18 Then Err.Raise 18
    wFail(w) = Err.Description
    If wFail(w) = "" Then wFail(w) = "error " & Err.Number
End Sub

' read slot w's yield file after the run; "" if it worked, else why not
Private Function CollectYield(ByVal w As Integer) As String
    Dim p As String, t As Double
    On Error GoTo Failed
    If wFail(w) <> "" Then
        CollectYield = wFail(w)
        Exit Function
    End If
    p = YldPath(w)
    If Dir(p) = "" Then
        t = Timer
        Do While Dir(p) = "" And SecondsSince(t) < 15
            DoEvents
            Sleep 200
        Loop
        If Dir(p) = "" Then
            CollectYield = "DRAINMOD wrote no yield file (" & p & ")"
            Exit Function
        End If
    End If
    CollectYield = ReadYieldDone(p, wSet(w))
    If CollectYield = "" And yN <> nCrop Then CollectYield = "the yield file has " & yN & " crops instead of " & nCrop
    Exit Function
Failed:
    If Err.Number = 18 Then Err.Raise 18
    CollectYield = Err.Description
    If CollectYield = "" Then CollectYield = "error " & Err.Number
End Function

Private Function ParallelCount() As Integer
    Dim p As Integer
    p = CfgOpt("ParallelRuns", 1)
    If p < 1 Then p = 1
    If p > NWMAX Then p = NWMAX
    ParallelCount = p
End Function

'================================================= results sheets ====
Private Sub StartSheets()
    Dim ws As Worksheet
    For Each ws In ThisWorkbook.Worksheets
        Select Case ws.Name
            Case "Results", "Yearly", "Run Log"
                ws.Cells.Clear
            Case "Charts"
                Do While ws.ChartObjects.Count > 0
                    ws.ChartObjects(1).Delete
                Loop
                ws.Cells.Clear
        End Select
    Next ws
    With ThisWorkbook.Worksheets("Run Log")
        .Range("A1:E1").Value = Array("Run", "Weather set", "Drain depth (ft)", "Drain spacing (ft)", "Note")
        .Rows(1).Font.Bold = True
    End With
    With ThisWorkbook.Worksheets("Yearly")
        .Range("A1:I1").Value = Array("Weather set", "Drain depth (ft)", "Drain spacing (ft)", "Crop", "Year", _
            "Overall relative yield (%)", "Excess water (%)", "Drought (%)", "Planting delay (%)")
        .Rows(1).Font.Bold = True
    End With
    yearlyRow = 2
End Sub

' Results header once the crops are known (4 columns per crop, overall first)
Private Sub ResultsHeader()
    Dim ws As Worksheet, c As Integer, col As Long
    Set ws = ThisWorkbook.Worksheets("Results")
    ws.Range("A1:C1").Value = Array("Weather set", "Drain depth (ft)", "Drain spacing (ft)")
    For c = 1 To nCrop
        col = 4 + (c - 1) * 4
        ws.Cells(1, col).Value = cropName(c) & ": overall (%)"
        ws.Cells(1, col + 1).Value = cropName(c) & ": excess water (%)"
        ws.Cells(1, col + 2).Value = cropName(c) & ": drought (%)"
        ws.Cells(1, col + 3).Value = cropName(c) & ": planting delay (%)"
    Next c
    ws.Rows(1).Font.Bold = True
    ws.Rows(1).WrapText = True
End Sub

' write run j (weather set s, depth d, spacing sp) from the yield file just read
Private Sub RecordJob(ByVal j As Long, ByVal s As Integer, ByVal d As Double, ByVal sp As Double, ByVal note As String)
    Dim wr As Worksheet, wy As Worksheet, c As Integer, k As Integer, col As Long, q As Integer
    Set wr = ThisWorkbook.Worksheets("Results")
    Set wy = ThisWorkbook.Worksheets("Yearly")
    wr.Cells(j + 1, 1).Value = setName(s)
    wr.Cells(j + 1, 2).Value = d
    wr.Cells(j + 1, 3).Value = sp
    If note = "" Then
        For c = 1 To nCrop
            col = 4 + (c - 1) * 4
            For q = 1 To 4
                wr.Cells(j + 1, col + q - 1).Value = yAvg(c, q)
            Next q
            For k = 1 To yYears(c)
                wy.Cells(yearlyRow, 1).Value = setName(s)
                wy.Cells(yearlyRow, 2).Value = d
                wy.Cells(yearlyRow, 3).Value = sp
                wy.Cells(yearlyRow, 4).Value = cropName(c)
                wy.Cells(yearlyRow, 5).Value = yYear(c, k)
                For q = 1 To 4
                    wy.Cells(yearlyRow, 5 + q).Value = yVal(c, k, q)
                Next q
                yearlyRow = yearlyRow + 1
            Next k
        Next c
    End If
    With ThisWorkbook.Worksheets("Run Log")
        .Cells(j + 1, 1).Value = j
        .Cells(j + 1, 2).Value = setName(s)
        .Cells(j + 1, 3).Value = d
        .Cells(j + 1, 4).Value = sp
        If note = "" Then .Cells(j + 1, 5).Value = "ok" Else .Cells(j + 1, 5).Value = "failed: " & note
    End With
End Sub

'=========================================================== the run ====
Public Sub RunDesign()
    Dim depths() As Double, spaces() As Double, nd As Integer, ns As Integer, s As Integer, di As Integer, si As Integer
    Dim nJobs As Long, j As Long, w As Integer, nb As Integer, c As Integer, q As Integer, h As Double
    Dim jSet() As Integer, jDep() As Double, jSp() As Double, baseY() As Double
    Dim msg As String, note As String, parNote As String, errText As String, skipped As String
    Dim started As Boolean, ok As Boolean, nFail As Long, t0 As Date
    On Error GoTo Failed
    Application.EnableCancelKey = xlErrorHandler
    LoadProject
    LoadSets
    RangeValues "Depth", "drain depth (ft)", depths, nd
    RangeValues "Space", "drain spacing (ft)", spaces, ns
    ' drains must be above the impermeable layer
    h = ImpDepth(genLines)
    j = 0
    For di = 1 To nd
        If depths(di) * CM_PER_FT < h Then
            j = j + 1
            depths(j) = depths(di)
        Else
            skipped = skipped & IIf(skipped = "", "", ", ") & depths(di)
        End If
    Next di
    nd = j
    If nd = 0 Then Err.Raise vbObjectError + 830, "Range", "Every drain depth is at or below the impermeable layer (" & NumStr(h / CM_PER_FT, 2) & " ft)."
    ' runs in chart order: weather set, then depth, then spacing
    nJobs = CLng(nSet) * nd * ns
    ReDim jSet(1 To nJobs): ReDim jDep(1 To nJobs): ReDim jSp(1 To nJobs)
    j = 0
    For s = 1 To nSet
        For di = 1 To nd
            For si = 1 To ns
                j = j + 1
                jSet(j) = s: jDep(j) = depths(di): jSp(j) = spaces(si)
            Next si
        Next di
    Next s
    StartSheets
    ThisWorkbook.Worksheets("Setup").Activate
    t0 = Now
    Cfg("RunsDone").Value = 0
    yldFolder = "": yldFixed = ""

    ' ---- the first run alone: finds the yield file and the crops
    SetStatus "Run 1 of " & nJobs & " ..."
    LaunchJob 1, jSet(1), jDep(1), jSp(1)
    WaitRuns 1
    If wFail(1) <> "" Then Err.Raise vbObjectError + 831, "DRAINMOD", wFail(1)
    FindYieldFile
    msg = ReadYieldDone(YldPath(1), jSet(1))
    If msg <> "" Then Err.Raise vbObjectError + 832, "DRAINMOD", msg
    nCrop = yN
    For c = 1 To nCrop
        cropName(c) = yName(c)
    Next c
    ResultsHeader
    RecordJob 1, jSet(1), jDep(1), jSp(1), ""
    ReDim baseY(1 To nCrop, 1 To 4)
    For c = 1 To nCrop
        For q = 1 To 4
            baseY(c, q) = yAvg(c, q)
        Next q
    Next c
    Cfg("RunsDone").Value = 1
    started = True

    ' ---- runs side by side: every slot must reproduce the first run
    nWork = ParallelCount()
    If yldFixed <> "" Then
        If nWork > 1 Then parNote = "Runs side by side are off: DRAINMOD wrote its yield file as " & yldFixed & _
            ", not named after the run's project, so runs side by side would share it."
        nWork = 1
    End If
    If nWork > 1 And nJobs > 1 Then
        SetStatus "Checking " & nWork & " DRAINMOD runs side by side ..."
        For w = 1 To nWork
            SafeLaunch w, jSet(1), jDep(1), jSp(1)
        Next w
        WaitRuns nWork
        ok = True
        For w = 1 To nWork
            msg = CollectYield(w)
            If msg <> "" Then
                ok = False: parNote = "run slot " & w & ": " & msg
            Else
                For c = 1 To nCrop
                    For q = 1 To 4
                        If yAvg(c, q) <> baseY(c, q) Then ok = False: parNote = "run slot " & w & " gave different yields than the single run"
                    Next q
                Next c
            End If
        Next w
        If Not ok Then
            parNote = "Runs side by side are off: they did not reproduce a single run (" & parNote & ")."
            nWork = 1
        End If
    Else
        nWork = 1
    End If

    ' ---- the other runs, in batches of nWork
    j = 1
    Do While j < nJobs
        nb = nWork
        If nb > nJobs - j Then nb = nJobs - j
        For w = 1 To nb
            SafeLaunch w, jSet(j + w), jDep(j + w), jSp(j + w)
        Next w
        SetStatus "Run " & (j + 1) & IIf(nb > 1, "-" & (j + nb), "") & " of " & nJobs & IIf(nWork > 1, " (" & nWork & " side by side)", "") & _
            ";  failed runs " & nFail & ";  elapsed " & Format$(Now - t0, "h:nn:ss") & "  (press Esc to stop)"
        WaitRuns nb
        For w = 1 To nb
            note = CollectYield(w)
            If note <> "" Then nFail = nFail + 1
            RecordJob j + w, jSet(j + w), jDep(j + w), jSp(j + w), note
        Next w
        j = j + nb
        Cfg("RunsDone").Value = j
    Loop
Finish:
    On Error GoTo Failed2
    KillRuns
    DrawCharts
    msg = "Design analysis finished: " & Cfg("RunsDone").Value & " of " & nJobs & " runs"
    If nFail > 0 Then msg = msg & " (" & nFail & " failed; see the Run Log sheet, which is hidden: right-click a sheet tab > Unhide)"
    msg = msg & "."
    If skipped <> "" Then msg = msg & " Drain depths at or below the impermeable layer (" & NumStr(ImpDepth(genLines) / CM_PER_FT, 2) & " ft) were left out: " & skipped & " ft."
    If parNote <> "" Then msg = msg & " " & parNote
    If Not wTracked(1) Then msg = msg & " (Excel could not follow the DRAINMOD runs directly, so it waited for each yield file to be finished.)"
    SetStatus msg
    Application.StatusBar = False
    Application.EnableCancelKey = xlInterrupt
    ThisWorkbook.Worksheets("Charts").Activate
    Exit Sub
Failed:
    If Err.Number = 18 Then
        If MsgBox("Stop the analysis? The runs finished so far are kept and charted.", vbYesNo + vbQuestion) = vbNo Then Resume
        KillRuns
        If started Then Resume Finish
        Application.StatusBar = False
        Application.EnableCancelKey = xlInterrupt
        Exit Sub
    End If
    errText = Err.Description
    KillRuns
    If started Then Resume Partial
    Application.StatusBar = False
    SetStatus "Analysis stopped: " & errText
    Application.StatusBar = False
    Application.EnableCancelKey = xlInterrupt
    MsgBox "Analysis stopped: " & errText, vbExclamation
    Exit Sub
Partial:
    On Error Resume Next
    DrawCharts
    Application.StatusBar = False
    SetStatus "Analysis stopped: " & errText
    Application.StatusBar = False
    Application.EnableCancelKey = xlInterrupt
    MsgBox "Analysis stopped: " & errText & vbLf & vbLf & "The runs finished so far are on the Results sheet and charted.", vbExclamation
    Exit Sub
Failed2:
    KillRuns
    Application.StatusBar = False
    Application.EnableCancelKey = xlInterrupt
    MsgBox "Could not finish: " & Err.Description, vbExclamation
End Sub

'============================================================ charts ====
' one chart per crop on the Charts sheet, from the Results sheet: overall
' relative yield vs drain spacing, a line per drain depth (colour) and
' weather set (solid for the first set, dashed for the others); legend below
Public Sub DrawCharts()
    Dim wr As Worksheet, wc As Worksheet, last As Long, lastCol As Long, nc As Integer, c As Integer
    Dim r As Long, r0 As Long, co As ChartObject, sr As Series, col As Long, title As String
    Dim depList(1 To 200) As Double, nDep As Integer, setList(1 To 50) As String, nSets As Integer
    Dim i As Integer, di As Integer, sIdx As Integer, pal As Variant, clr As Long
    Dim comp As Integer, compTitle As Variant, compAxis As Variant
    Set wr = ThisWorkbook.Worksheets("Results")
    Set wc = ThisWorkbook.Worksheets("Charts")
    EnsureChartOptions
    comp = YieldComponent()
    compTitle = Array("Relative Overall Yield (%)", "Relative Yield, Excess Water (%)", "Relative Yield, Drought (%)", "Relative Yield, Planting Delay (%)")
    compAxis = Array("Overall Yield", "Excess Water Yield", "Drought Yield", "Planting Delay Yield")
    Do While wc.ChartObjects.Count > 0
        wc.ChartObjects(1).Delete
    Loop
    last = wr.Cells(wr.Rows.Count, 1).End(xlUp).Row
    lastCol = wr.Cells(1, wr.Columns.Count).End(xlToLeft).Column
    If last < 2 Or lastCol < 4 Then Exit Sub
    nc = (lastCol - 3) \ 4
    For r = 2 To last
        di = 0
        For i = 1 To nDep
            If depList(i) = wr.Cells(r, 2).Value Then di = i
        Next i
        If di = 0 And nDep < 200 Then nDep = nDep + 1: depList(nDep) = wr.Cells(r, 2).Value
        sIdx = 0
        For i = 1 To nSets
            If setList(i) = CStr(wr.Cells(r, 1).Value) Then sIdx = i
        Next i
        If sIdx = 0 And nSets < 50 Then nSets = nSets + 1: setList(nSets) = CStr(wr.Cells(r, 1).Value)
    Next r
    ' blue, red, green, purple, aqua, orange, ... (Excel's classic series colours)
    pal = Array(RGB(79, 129, 189), RGB(192, 80, 77), RGB(155, 187, 89), RGB(128, 100, 162), _
                RGB(75, 172, 198), RGB(247, 150, 70), RGB(44, 77, 117), RGB(119, 44, 42))
    For c = 1 To nc
        col = 4 + (c - 1) * 4
        title = CStr(wr.Cells(1, col).Value)
        col = col + comp - 1          ' Results: overall, excess water, drought, planting delay
        If InStr(title, ":") > 0 Then title = Left$(title, InStr(title, ":") - 1)
        Set co = wc.ChartObjects.Add(10 + (c - 1) * 615, 46, 600, 380)     ' side by side, one per crop
        With co.Chart
            .ChartType = xlXYScatterLines
            Do While .SeriesCollection.Count > 0
                .SeriesCollection(1).Delete
            Loop
            r = 2
            Do While r <= last
                r0 = r
                Do While r + 1 <= last
                    If wr.Cells(r + 1, 1).Value <> wr.Cells(r0, 1).Value Or wr.Cells(r + 1, 2).Value <> wr.Cells(r0, 2).Value Then Exit Do
                    r = r + 1
                Loop
                di = 1: sIdx = 1
                For i = 1 To nDep
                    If depList(i) = wr.Cells(r0, 2).Value Then di = i
                Next i
                For i = 1 To nSets
                    If setList(i) = CStr(wr.Cells(r0, 1).Value) Then sIdx = i
                Next i
                clr = pal((di - 1) Mod 8)
                Set sr = .SeriesCollection.NewSeries
                ' the legend shows the drain depth (ft), and the weather set when there are several
                If nSets > 1 Then
                    sr.Name = wr.Cells(r0, 2).Value & " (" & wr.Cells(r0, 1).Value & ")"
                Else
                    sr.Name = CStr(wr.Cells(r0, 2).Value)
                End If
                sr.XValues = wr.Range(wr.Cells(r0, 3), wr.Cells(r, 3))
                sr.Values = wr.Range(wr.Cells(r0, col), wr.Cells(r, col))
                sr.Format.Line.Visible = msoTrue
                sr.Format.Line.ForeColor.RGB = clr
                sr.Format.Line.Weight = 2.25
                If sIdx > 1 Then sr.Format.Line.DashStyle = msoLineDash Else sr.Format.Line.DashStyle = msoLineSolid
                If sIdx > 1 Then sr.MarkerStyle = xlMarkerStyleTriangle Else sr.MarkerStyle = xlMarkerStyleCircle
                sr.MarkerSize = 7
                sr.MarkerForegroundColor = clr
                sr.MarkerBackgroundColor = clr
                r = r + 1
            Loop
            .HasTitle = True
            .ChartTitle.Text = compTitle(comp - 1) & " for " & UCase$(Left$(title, 1)) & Mid$(title, 2)
            .Axes(xlCategory).HasTitle = True
            .Axes(xlCategory).AxisTitle.Text = "Spacing (ft)"
            .Axes(xlValue).HasTitle = True
            .Axes(xlValue).AxisTitle.Text = compAxis(comp - 1)
            .HasLegend = True
            .Legend.Position = xlLegendPositionBottom
        End With
        StyleChart co
    Next c
End Sub

' the look of the yield charts: yield axis up to 100 by 10 (lower end automatic), spacing axis from 0,
' light gray gridlines both ways, bold dark-gray text, blue border
Private Sub StyleChart(co As ChartObject)
    Dim ax As Variant, grey As Long
    grey = RGB(89, 89, 89)
    On Error Resume Next            ' formatting only: never stop the run for it
    With co.Chart
        With .ChartTitle.Format.TextFrame2.TextRange.Font
            .Size = 16: .Bold = msoTrue: .Fill.ForeColor.RGB = grey
        End With
        With .Axes(xlValue)
            .MinimumScaleIsAuto = True: .MaximumScale = 100: .MajorUnit = 10
        End With
        .Axes(xlCategory).MinimumScale = 0
        For Each ax In Array(xlValue, xlCategory)
            With .Axes(ax)
                .HasMajorGridlines = True
                .MajorGridlines.Format.Line.ForeColor.RGB = RGB(217, 217, 217)
                .MajorGridlines.Format.Line.Weight = 0.75
                .Format.Line.ForeColor.RGB = RGB(191, 191, 191)
                .TickLabels.Font.Bold = True
                .TickLabels.Font.Size = 11
                .TickLabels.Font.Color = grey
                With .AxisTitle.Format.TextFrame2.TextRange.Font
                    .Size = 12: .Bold = msoTrue: .Fill.ForeColor.RGB = grey
                End With
            End With
        Next ax
        With .Legend.Font
            .Bold = True: .Size = 11: .Color = grey
        End With
        .PlotArea.Format.Line.Visible = msoFalse
        With .ChartArea.Format.Line
            .Visible = msoTrue: .ForeColor.RGB = RGB(68, 114, 196): .Weight = 2
        End With
    End With
End Sub

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

Private Function ImpDepth(g() As String) As Double
    ImpDepth = Val(Tokens(g(SoilsIdx(g) + 1))(0))
End Function

Private Function EffRadius(g() As String) As Double
    EffRadius = Val(Tokens(g(SoilsIdx(g) + 1))(1))
End Function

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
    wCmd(w) = cmd
    pid = Shell(cmd, vbHide)
    wHandle(w) = OpenProcess(SYNC_TERMINATE, 0, CLng(pid))
    wTracked(w) = (wHandle(w) <> 0)
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

Public Sub BrowseProgram()
    Dim f As Variant, cur As String
    cur = Trim$(CStr(Cfg("DmExe").Value))
    If FileExists(cur) Then GoToFolder FolderOf(cur) Else GoToFolder ProjectSubfolder("Program")
    f = Application.GetOpenFilename("DRAINMOD program (dmhydro.exe),*.exe", , "Select dmhydro.exe")
    If f = False Then Exit Sub
    Cfg("DmExe").Value = f
End Sub

' dmhydro.exe of the project: in its Program folder, else next to the .prj.
' With force, it replaces the program on the Setup sheet when the project has one.
Private Sub GuessProgram(Optional ByVal force As Boolean = False)
    Dim cur As String, f As String, cand As Variant
    cur = Trim$(CStr(Cfg("DmExe").Value))
    If Not force And FileExists(cur) Then Exit Sub
    For Each cand In Array(ProjectSubfolder("Program"), FolderOf(CStr(Cfg("PrjFile").Value)))
        f = CStr(cand)
        If f <> "" Then
            If FileExists(f & "dmhydro.exe") Then
                Cfg("DmExe").Value = f & "dmhydro.exe"
                Exit Sub
            End If
        End If
    Next cand
End Sub

' a folder of the project with this name (e.g. Program, Weather): next to the
' .prj, or in the folder above it (DDESIGN\Weather for DDESIGN\inputs\x.prj), or
' the one above that; with a trailing "\", or "" if there is none
Private Function ProjectSubfolder(ByVal nm As String) As String
    Dim b As String, p As Long, lvl As Integer
    b = Trim$(CStr(Cfg("PrjFile").Value))
    If b = "" Then Exit Function
    b = FolderOf(b)
    For lvl = 0 To 2
        If FolderExists(b & nm) Then
            ProjectSubfolder = b & nm & "\"
            Exit Function
        End If
        p = InStrRev(Left$(b, Len(b) - 1), "\")
        If p = 0 Then Exit Function
        b = Left$(b, p)
    Next lvl
End Function

Private Function FolderExists(ByVal p As String) As Boolean
    On Error Resume Next
    If p <> "" Then FolderExists = ((GetAttr(p) And vbDirectory) = vbDirectory)
End Function

' make p the folder a file dialog opens in (network folders included)
Private Sub GoToFolder(ByVal p As String)
    If p = "" Then Exit Sub
    On Error Resume Next
    CreateObject("WScript.Shell").CurrentDirectory = p
    If Mid$(p, 2, 1) = ":" Then ChDrive Left$(p, 1)
    ChDir p
End Sub

' ---- dates in the daily output
' a token that is a whole number (digits only)
Private Function IsIntTok(ByVal s As String) As Boolean
    IsIntTok = (Len(s) > 0 And Not (s Like "*[!0-9]*"))
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
