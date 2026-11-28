Attribute VB_Name = "Module2"
'==========================================================================
' ONLINE SOIL DATA (declarations)
'==========================================================================
Private Const SDA_URL As String = "https://sdmdataaccess.sc.egov.usda.gov/Tabular/post.rest"
Private Const NA As Double = -9999#

Private Type SLayer
    top As Double
    bot As Double
    sand As Double
    silt As Double
    clay As Double
    ks As Double          ' cm/hr, NA if missing
    db As Double          ' g/cm3, NA if missing
    nm As String          ' horizon name(s)
    tex As String         ' USDA texture class
End Type

' soil-preparation working arrays (one row per DRAINMOD layer)
Private spNL As Integer
Private spBot() As Double
Private spKs() As Double
Private spT() As Double          ' theta  (layer, point)
Private spH() As Double          ' suction cm, positive (layer, point)
Private spNP() As Integer
Private mqN() As Integer
Private mqT() As Double          ' theta grid (layer, i), i=1 is saturation
Private mqH() As Double          ' suction at theta grid
Private mqK() As Double          ' Millington-Quirk conductivity, cm/hr
Sub FileSaveAs(strshell, strshell0)
Dim StrLine As String
Open strshell For Input As 1
Open strshell0 For Output As 2
While EOF(1) = False
  Line Input #1, StrLine
  Print #2, StrLine
Wend
Close 1, 2
Exit Sub
End Sub

Sub TerminateProcess(strTerminateThis As String)
     '---------------------------------------------------------------------------------------
     '              : Terminates a process. First checking to see if it is running or not.
     '              : Uses WMI (Windows Management Instrumentation) to query all running processes
     '              : then terminates ALL instances of the specified process
     '              : held in the variable strTerminateThis.
     '              :
     '              : ***WARNING: This will terminate a specified running process,use with caution!.
     '              : ***Terminating certain processes can effect the running of Windows and/or
     '              : ***running applications.
     '---------------------------------------------------------------------------------------
    'Dim strTerminateThis As String 'The variable to hold the process to terminate
    Dim objWMIcimv2 As Object
    Dim objProcess As Object
    Dim objList As Object
    Dim intError As Integer
     

     
    Set objWMIcimv2 = GetObject("winmgmts:" _
    & "{impersonationLevel=impersonate}!\\.\root\cimv2") 'Connect to CIMV2 Namespace
     
    Set objList = objWMIcimv2.ExecQuery _
    ("select * from win32_process where name='" & strTerminateThis & "'") 'Find the process to terminate
     
     
    If objList.Count = 0 Then 'If 0 then process isn't running
        'MsgBox strTerminateThis & " is NOT running." & vbCr & vbCr _
        '& "Exiting procedure.", vbCritical, "Unable to continue"
        Set objWMIcimv2 = Nothing
        Set objList = Nothing
        Set objProcess = Nothing
        Exit Sub
    Else
         'Ask if OK to continue
        'Select Case MsgBox("Are you sure you want to terminate this running process?:" _
            & vbCrLf & "" _
            & vbCrLf & "Process name: " & strTerminateThis _
            & vbCrLf & "" _
            & vbCrLf & "Note:" _
            & vbCrLf & "Terminating certain processes can effect the running of Windows" _
            & "and/or running applications. The process will terminate if you OK it, WITHOUT " _
            & "giving you the chance to save any changes in anything that is running in the specified process above." _
            , vbOKCancel Or vbQuestion Or vbSystemModal Or vbDefaultButton1, "WARNING:")
             
        'Case vbOK
             'OK to continue with terminating the process
            For Each objProcess In objList
                 
                intError = objProcess.Terminate 'Terminates a process and all of its threads.
                 'Return value is 0 for success. Any other number is an error.
                If intError <> 0 Then
                    MsgBox "ERROR: Unable to terminate that process.", vbCritical, "Aborting"
                    Exit Sub
                End If
            Next
             'ALL instances of specified process (strTerminateThis) has been terminated
           ' Call MsgBox("ALL instances of process " & strTerminateThis & " has been successfully terminated.", _
            vbInformation, "Process Terminated")
             
            Set objWMIcimv2 = Nothing
            Set objList = Nothing
            Set objProcess = Nothing
            Exit Sub
             
        'Case vbCancel
             'NOT OK to continue with the termination, abort
         '   Set objWMIcimv2 = Nothing
         '   Set objList = Nothing
         '   Set objProcess = Nothing
         '   Exit Sub
        'End Select
    End If
     
End Sub

Sub StopProcess(StringToStop As String)
Dim oServ As Object
Dim cProc As Variant
Dim oProc As Object
Dim errReturnCode As Long:
      
Set oServ = GetObject("winmgmts:")
Set cProc = oServ.ExecQuery("Select * from Win32_Process")

For Each oProc In cProc

    'Rename EXCEL.EXE in the line below with the process that you need to Terminate.
    'NOTE: It is 'case sensitive

    If oProc.Name = StringToStop Then
      'MsgBox "KILL"   ' used to display a message for testing pur
      
errReturnCode = oProc.Terminate()
    End If
Next
Set Myobj = Nothing
End Sub


'==========================================================================
' ONLINE SOIL DATA
'   1. USDA-NRCS Soil Data Access (SSURGO): the dominant soil component at
'      the site latitude/longitude, or a named soil series in the state.
'   2. Horizons -> at most 5 DRAINMOD layers, van Genuchten water retention
'      from Rosetta texture-class averages (as the drainmod_soi QGIS plugin
'      does by default) -> DRAINMOD .soi file.
'   3. DRAINMOD soil preparation (port of the soil utility, checked against
'      its Drummer output): .MIS (water characteristic), .SIN (drained
'      volume, upflux and Green-Ampt tables), .WDV (drained volume by layer).
'   4. Up to 5 lateral-conductivity layers and the impermeable-layer depth
'      for the .gen file (stored on DMData).
'==========================================================================

'---------------------------------------------------------------- JSON ----
Private Function JsonEscape(ByVal s As String) As String
    s = Replace(s, "\", "\\")
    s = Replace(s, """", "\""")
    s = Replace(s, vbCr, " ")
    s = Replace(s, vbLf, " ")
    s = Replace(s, vbTab, " ")
    JsonEscape = s
End Function

Private Sub JsonSkipWs(ByRef s As String, ByRef p As Long)
    Do While p <= Len(s)
        Select Case Mid$(s, p, 1)
            Case " ", vbTab, vbCr, vbLf
                p = p + 1
            Case Else
                Exit Do
        End Select
    Loop
End Sub

Private Function JsonString(ByRef s As String, ByRef p As Long) As String
    ' p at the opening quote; returns the string, p after the closing quote
    Dim c As String, out As String
    p = p + 1
    Do While p <= Len(s)
        c = Mid$(s, p, 1)
        If c = """" Then
            p = p + 1
            JsonString = out
            Exit Function
        ElseIf c = "\" Then
            p = p + 1
            c = Mid$(s, p, 1)
            Select Case c
                Case "n": out = out & vbLf
                Case "t": out = out & vbTab
                Case "r": out = out & vbCr
                Case "b", "f": out = out & " "
                Case "u"
                    out = out & ChrW(CLng("&H" & Mid$(s, p + 1, 4)))
                    p = p + 4
                Case Else: out = out & c
            End Select
            p = p + 1
        Else
            out = out & c
            p = p + 1
        End If
    Loop
    JsonString = out
End Function

' Parse an SDA "JSON+COLUMNNAME" reply: {"Table":[[col,...],[v,...],...]}
' Returns a Collection of Scripting.Dictionary (column name -> value text;
' missing values are "").
Private Function ParseSdaRows(ByVal s As String) As Collection
    Dim rows As New Collection, p As Long, c As String, rec As Collection
    Dim header As Collection, d As Object, i As Long, first As Boolean, tok As String, q As Long
    Set ParseSdaRows = rows
    p = InStr(1, s, """Table""")
    If p = 0 Then Exit Function
    p = InStr(p, s, "[")
    If p = 0 Then Exit Function
    p = p + 1
    first = True
    Do
        JsonSkipWs s, p
        If p > Len(s) Then Exit Do
        c = Mid$(s, p, 1)
        If c = "," Then
            p = p + 1
            JsonSkipWs s, p
            c = Mid$(s, p, 1)
        End If
        If c <> "[" Then Exit Do
        p = p + 1
        Set rec = New Collection
        Do
            JsonSkipWs s, p
            If p > Len(s) Then Exit Do
            c = Mid$(s, p, 1)
            If c = "]" Then
                p = p + 1
                Exit Do
            End If
            If c = "," Then
                p = p + 1
                JsonSkipWs s, p
                c = Mid$(s, p, 1)
            End If
            If c = """" Then
                rec.Add JsonString(s, p)
            Else
                q = p
                Do While q <= Len(s)
                    If Mid$(s, q, 1) = "," Or Mid$(s, q, 1) = "]" Then Exit Do
                    q = q + 1
                Loop
                tok = Trim$(Mid$(s, p, q - p))
                If tok = "null" Then tok = ""
                rec.Add tok
                p = q
            End If
        Loop
        If first Then
            Set header = rec
            first = False
        Else
            Set d = CreateObject("Scripting.Dictionary")
            d.CompareMode = 1
            For i = 1 To header.Count
                If i <= rec.Count Then d(header(i)) = rec(i) Else d(header(i)) = ""
            Next i
            rows.Add d
        End If
    Loop
End Function

Private Function SdaQuery(ByVal sql As String) As Collection
    Dim body As String
    body = "{""query"":""" & JsonEscape(sql) & """,""format"":""JSON+COLUMNNAME""}"
    Set SdaQuery = ParseSdaRows(HttpPostJson(SDA_URL, body))
End Function

Private Function NumOrNA(ByVal v As Variant) As Double
    If IsNull(v) Or IsEmpty(v) Then
        NumOrNA = NA
    ElseIf Trim$(CStr(v)) = "" Then
        NumOrNA = NA
    Else
        NumOrNA = Val(CStr(v))
    End If
End Function

Private Function SqlText(ByVal s As String) As String
    SqlText = Replace(s, "'", "''")
End Function

'------------------------------------------------------------ SSURGO -----
Private Function SdaMapunitAt(ByVal lon As Double, ByVal lat As Double) As Object
    Dim r As Collection
    Set r = SdaQuery("SELECT mu.mukey, mu.muname, mu.musym, l.areasymbol FROM mapunit mu " & _
        "INNER JOIN legend l ON l.lkey = mu.lkey WHERE mu.mukey IN (SELECT * FROM " & _
        "SDA_Get_Mukey_from_intersection_with_WktWgs84('point(" & NumStr(lon, 7) & " " & NumStr(lat, 7) & ")'))")
    If r.Count = 0 Then Set SdaMapunitAt = Nothing Else Set SdaMapunitAt = r(1)
End Function

Private Function SdaDefaultComponent(ByVal mukey As String) As Object
    Dim r As Collection, c As Object
    Set r = SdaQuery("SELECT cokey, compname, comppct_r, majcompflag, compkind FROM component " & _
        "WHERE mukey = '" & SqlText(mukey) & "' ORDER BY comppct_r DESC, majcompflag DESC")
    If r.Count = 0 Then Set SdaDefaultComponent = Nothing: Exit Function
    For Each c In r
        If c("majcompflag") = "Yes" And LCase$(c("compkind")) <> "miscellaneous area" Then
            Set SdaDefaultComponent = c
            Exit Function
        End If
    Next c
    Set SdaDefaultComponent = r(1)
End Function

' Representative component for a named soil series: the largest major
' component with that name (and horizon texture data) in the county's soil
' survey area; if the county has none, the largest one in the state.
' scope returns "county" or "state".
Private Function SdaSeriesComponent(ByVal series As String, ByVal county As String, ByVal stateName As String, _
        ByRef scope As String) As Object
    Dim r As Collection, where As String, names As Variant, nm As Variant, k As Integer
    Dim stateAbbr As String, stateLong As String
    stateAbbr = StateAbbrev(stateName)
    stateLong = Replace(stateName, "_", " ")
    county = Replace(Trim$(county), "_", " ")
    names = Array(Trim$(series), Split(Trim$(series) & " ", " ")(0))
    For k = 1 To 2
        If k = 1 And (county = "" Or stateAbbr = "") Then GoTo NextScope
        For Each nm In names
            where = "c.compname = '" & SqlText(CStr(nm)) & "' AND c.majcompflag = 'Yes'"
            If k = 1 Then
                where = where & " AND l.areasymbol LIKE '" & stateAbbr & "%' AND l.areaname LIKE '" & _
                        SqlText(county) & " %" & SqlText(stateLong) & "%'"
            ElseIf stateAbbr <> "" Then
                where = where & " AND l.areasymbol LIKE '" & stateAbbr & "%'"
            End If
            Set r = SdaQuery("SELECT TOP 1 c.cokey, c.compname, c.comppct_r, mu.mukey, mu.muname, l.areasymbol, l.areaname " & _
                "FROM component c INNER JOIN mapunit mu ON mu.mukey = c.mukey INNER JOIN legend l ON l.lkey = mu.lkey " & _
                "WHERE " & where & " AND EXISTS (SELECT 1 FROM chorizon ch WHERE ch.cokey = c.cokey " & _
                "AND ch.sandtotal_r IS NOT NULL) ORDER BY c.comppct_r DESC, c.cokey")
            If r.Count > 0 Then
                Set SdaSeriesComponent = r(1)
                If k = 1 Then scope = "county" Else scope = "state"
                Exit Function
            End If
        Next nm
NextScope:
    Next k
    Set SdaSeriesComponent = Nothing
End Function

Private Function SdaHorizons(ByVal cokey As String) As Collection
    Set SdaHorizons = SdaQuery("SELECT ch.chkey, ch.hzname, ch.hzdept_r, ch.hzdepb_r, " & _
        "ch.sandtotal_r, ch.silttotal_r, ch.claytotal_r, ch.dbthirdbar_r, ch.ksat_r, " & _
        "(SELECT TOP 1 tg.texdesc FROM chtexturegrp tg WHERE tg.chkey = ch.chkey AND tg.rvindicator = 'Yes') AS texdesc " & _
        "FROM chorizon ch WHERE ch.cokey = '" & SqlText(cokey) & "' ORDER BY ch.hzdept_r")
End Function

Private Function StateAbbrev(ByVal st As String) As String
    Select Case st
        Case "Illinois": StateAbbrev = "IL"
        Case "Indiana": StateAbbrev = "IN"
        Case "Iowa": StateAbbrev = "IA"
        Case "Michigan": StateAbbrev = "MI"
        Case "Minnesota": StateAbbrev = "MN"
        Case "Missouri": StateAbbrev = "MO"
        Case "North_Dakota": StateAbbrev = "ND"
        Case "Ohio": StateAbbrev = "OH"
        Case "South_Dakota": StateAbbrev = "SD"
        Case "Wisconsin": StateAbbrev = "WI"
        Case Else: StateAbbrev = ""
    End Select
End Function

'------------------------------------------------------------ texture ----
Private Function TextureClass(ByVal sand As Double, ByVal silt As Double, ByVal clay As Double) As String
    Dim t As Double
    t = sand + silt + clay
    If t <= 0 Then TextureClass = "loam": Exit Function
    sand = 100 * sand / t: silt = 100 * silt / t: clay = 100 * clay / t
    If silt + 1.5 * clay < 15 Then
        TextureClass = "sand"
    ElseIf silt + 2 * clay < 30 Then
        TextureClass = "loamy sand"
    ElseIf (clay >= 7 And clay < 20 And sand > 52) Or (clay < 7 And silt < 50) Then
        TextureClass = "sandy loam"
    ElseIf clay >= 7 And clay < 27 And silt >= 28 And silt < 50 And sand <= 52 Then
        TextureClass = "loam"
    ElseIf silt >= 80 And clay < 12 Then
        TextureClass = "silt"
    ElseIf (silt >= 50 And clay >= 12 And clay < 27) Or (silt >= 50 And silt < 80 And clay < 12) Then
        TextureClass = "silt loam"
    ElseIf clay >= 20 And clay < 35 And silt < 28 And sand > 45 Then
        TextureClass = "sandy clay loam"
    ElseIf clay >= 27 And clay < 40 And sand > 20 And sand <= 45 Then
        TextureClass = "clay loam"
    ElseIf clay >= 27 And clay < 40 And sand <= 20 Then
        TextureClass = "silty clay loam"
    ElseIf clay >= 35 And sand > 45 Then
        TextureClass = "sandy clay"
    ElseIf clay >= 40 And silt >= 40 Then
        TextureClass = "silty clay"
    Else
        TextureClass = "clay"
    End If
End Function

Private Function HasAnyWord(ByVal t As String, ByVal words As Variant) As Boolean
    Dim w As Variant
    t = LCase$(t)
    For Each w In words
        If InStr(t, w) > 0 Then HasAnyWord = True: Exit Function
    Next w
End Function

Private Function NonMineralWords() As Variant
    NonMineralWords = Array("bedrock", "muck", "peat", "material", "water", "cemented", "indurated", _
        "fragmental", "duripan", "petrocalcic", "ortstein", "variable", "unknown")
End Function

Private Function BedrockWords() As Variant
    BedrockWords = Array("bedrock", "cemented", "indurated", "duripan", "petrocalcic", "ortstein")
End Function

Private Function ClassFromTexdesc(ByVal texdesc As String) As String
    Dim t As String, cls As Variant
    ClassFromTexdesc = ""
    If Trim$(texdesc) = "" Then Exit Function
    t = LCase$(texdesc)
    If HasAnyWord(t, NonMineralWords()) Then Exit Function
    t = Trim$(Split(t, ",")(0))
    t = Replace(t, "very fine ", ""): t = Replace(t, "fine ", "")
    t = Replace(t, "very coarse ", ""): t = Replace(t, "coarse ", "")
    For Each cls In Array("sandy clay loam", "silty clay loam", "loamy sand", "sandy loam", "silt loam", _
            "sandy clay", "silty clay", "clay loam", "sand", "loam", "silt", "clay")
        If Right$(t, Len(cls)) = cls Then ClassFromTexdesc = cls: Exit Function
    Next cls
End Function

Private Sub ClassCentroid(ByVal cls As String, ByRef s As Double, ByRef si As Double, ByRef c As Double)
    Select Case cls
        Case "sand": s = 92: si = 5: c = 3
        Case "loamy sand": s = 82: si = 12: c = 6
        Case "sandy loam": s = 65: si = 25: c = 10
        Case "loam": s = 40: si = 40: c = 20
        Case "silt loam": s = 20: si = 65: c = 15
        Case "silt": s = 7: si = 88: c = 5
        Case "sandy clay loam": s = 60: si = 13: c = 27
        Case "clay loam": s = 32: si = 34: c = 34
        Case "silty clay loam": s = 10: si = 56: c = 34
        Case "sandy clay": s = 52: si = 6: c = 42
        Case "silty clay": s = 6: si = 47: c = 47
        Case Else: s = 22: si = 20: c = 58
    End Select
End Sub

' Rosetta class-average van Genuchten parameters (Schaap et al. 2001)
Private Sub RosettaClass(ByVal cls As String, ByRef tr As Double, ByRef ts As Double, ByRef alpha As Double, ByRef nvg As Double, ByRef ksCmHr As Double)
    Dim la As Double, ln As Double, lk As Double
    Select Case cls
        Case "clay": tr = 0.098: ts = 0.459: la = -1.825: ln = 0.098: lk = 1.169
        Case "clay loam": tr = 0.079: ts = 0.442: la = -1.801: ln = 0.151: lk = 0.913
        Case "loam": tr = 0.061: ts = 0.399: la = -1.954: ln = 0.168: lk = 1.081
        Case "loamy sand": tr = 0.049: ts = 0.39: la = -1.459: ln = 0.242: lk = 2.022
        Case "sand": tr = 0.053: ts = 0.375: la = -1.453: ln = 0.502: lk = 2.808
        Case "sandy clay": tr = 0.117: ts = 0.385: la = -1.476: ln = 0.082: lk = 1.055
        Case "sandy clay loam": tr = 0.063: ts = 0.384: la = -1.676: ln = 0.124: lk = 1.12
        Case "sandy loam": tr = 0.039: ts = 0.387: la = -1.574: ln = 0.161: lk = 1.583
        Case "silt": tr = 0.05: ts = 0.489: la = -2.182: ln = 0.225: lk = 1.641
        Case "silt loam": tr = 0.065: ts = 0.439: la = -2.296: ln = 0.221: lk = 1.261
        Case "silty clay": tr = 0.111: ts = 0.481: la = -1.79: ln = 0.121: lk = 0.983
        Case Else: tr = 0.09: ts = 0.482: la = -2.076: ln = 0.182: lk = 1.065   ' silty clay loam
    End Select
    alpha = 10 ^ la: nvg = 10 ^ ln: ksCmHr = 10 ^ lk / 24#
End Sub

Private Function VGTheta(ByVal tr As Double, ByVal ts As Double, ByVal alpha As Double, ByVal nvg As Double, ByVal h As Double) As Double
    If h <= 0 Then VGTheta = ts: Exit Function
    VGTheta = tr + (ts - tr) * (1 + (alpha * h) ^ nvg) ^ (-(1 - 1 / nvg))
End Function

'------------------------------------------------- horizons -> layers ----
Private Function MakeLayer(ByVal top As Double, ByVal bot As Double, ByVal s As Double, ByVal si As Double, ByVal c As Double, _
        ByVal ks As Double, ByVal db As Double, ByVal nm As String) As SLayer
    Dim L As SLayer
    L.top = top: L.bot = bot: L.sand = s: L.silt = si: L.clay = c
    L.ks = ks: L.db = db: L.nm = nm
    L.tex = TextureClass(s, si, c)
    MakeLayer = L
End Function

Private Function IsBedrockHz(ByVal hzname As String, ByVal texdesc As String) As Boolean
    Dim nm As String
    nm = UCase$(Trim$(hzname))
    Do While Len(nm) > 0 And Left$(nm, 1) Like "#"
        nm = Mid$(nm, 2)
    Loop
    IsBedrockHz = (Left$(nm, 1) = "R") Or (Left$(nm, 2) = "CR") Or HasAnyWord(texdesc, BedrockWords())
End Function

' SSURGO horizons -> cleaned layers (sorted, gaps closed, bedrock removed,
' horizons without texture folded into the nearest mineral layer)
Private Function CleanHorizons(ByVal hz As Collection, ByRef L() As SLayer, ByRef warn As String) As Integer
    Dim n As Long, i As Long, j As Long, idx() As Long, tmp As Long, h As Object
    Dim tops() As Double, nL As Integer, s As Double, si As Double, c As Double, cls As String
    Dim pend() As Long, nP As Integer, best As Integer, bd As Double, dd As Double, ok() As Boolean
    Dim kept As Long, ks As Double, tmpL As SLayer

    n = hz.Count
    If n = 0 Then Err.Raise vbObjectError + 560, "Soil", "This soil component has no horizon data in SSURGO."
    ReDim idx(1 To n): ReDim tops(1 To n): ReDim ok(1 To n)
    For i = 1 To n
        idx(i) = i
        tops(i) = NumOrNA(hz(i)("hzdept_r"))
        ok(i) = tops(i) <> NA And NumOrNA(hz(i)("hzdepb_r")) <> NA
        If ok(i) Then ok(i) = NumOrNA(hz(i)("hzdepb_r")) > tops(i)
    Next i
    For i = 2 To n
        tmp = idx(i): j = i - 1
        Do While j >= 1
            If tops(idx(j)) <= tops(tmp) Then Exit Do
            idx(j + 1) = idx(j): j = j - 1
        Loop
        idx(j + 1) = tmp
    Next i

    ReDim L(1 To n): ReDim pend(1 To n)
    nL = 0: nP = 0: kept = 0
    For i = 1 To n
        Set h = hz(idx(i))
        If ok(idx(i)) Then
            If IsBedrockHz(h("hzname"), h("texdesc")) And kept > 0 Then
                warn = warn & "Profile truncated at " & h("hzdept_r") & " cm: horizon " & h("hzname") & " (" & h("texdesc") & ") is bedrock/cemented. "
                Exit For
            End If
            kept = kept + 1
            s = NumOrNA(h("sandtotal_r")): si = NumOrNA(h("silttotal_r")): c = NumOrNA(h("claytotal_r"))
            ks = NumOrNA(h("ksat_r"))
            If ks <> NA Then ks = ks * 0.36            ' micrometres/s -> cm/hr
            If s = NA Or c = NA Or (IIf(s = NA, 0, s) + IIf(si = NA, 0, si) + IIf(c = NA, 0, c)) <= 0 Then
                cls = ClassFromTexdesc(h("texdesc"))
                If cls <> "" Then
                    ClassCentroid cls, s, si, c
                Else
                    nP = nP + 1: pend(nP) = idx(i)
                    GoTo NextHz
                End If
            ElseIf si = NA Then
                si = 100 - s - c
                If si < 0 Then si = 0
            End If
            nL = nL + 1
            L(nL) = MakeLayer(tops(idx(i)), NumOrNA(h("hzdepb_r")), s, si, c, ks, NumOrNA(h("dbthirdbar_r")), h("hzname"))
        End If
NextHz:
    Next i
    If nL = 0 Then Err.Raise vbObjectError + 561, "Soil", "No horizon in this soil has mineral texture data (organic or bedrock soil?)."

    ' fold horizons without texture data into the nearest layer
    For j = 1 To nP
        Set h = hz(pend(j))
        best = 1: bd = 1E+30
        For i = 1 To nL
            dd = Abs(L(i).top - NumOrNA(h("hzdepb_r")))
            If Abs(L(i).bot - NumOrNA(h("hzdept_r"))) < dd Then dd = Abs(L(i).bot - NumOrNA(h("hzdept_r")))
            If dd < bd Then bd = dd: best = i
        Next i
        warn = warn & "Horizon " & h("hzname") & " has no mineral texture data; given the properties of the adjacent horizon. "
        If NumOrNA(h("hzdept_r")) < L(best).top Then L(best).top = NumOrNA(h("hzdept_r"))
        If NumOrNA(h("hzdepb_r")) > L(best).bot Then L(best).bot = NumOrNA(h("hzdepb_r"))
        L(best).nm = L(best).nm & "+" & h("hzname")
    Next j

    ' sort by top, start at the surface, close gaps
    For i = 2 To nL
        tmpL = L(i): j = i - 1
        Do While j >= 1
            If L(j).top <= tmpL.top Then Exit Do
            L(j + 1) = L(j): j = j - 1
        Loop
        L(j + 1) = tmpL
    Next i
    L(1).top = 0
    For i = 2 To nL
        If Abs(L(i).top - L(i - 1).bot) > 0.000001 Then L(i).top = L(i - 1).bot
    Next i
    j = 0
    For i = 1 To nL
        If L(i).bot - L(i).top > 0 Then j = j + 1: L(j) = L(i)
    Next i
    CleanHorizons = j
End Function

Private Function WAvg(ByVal x As Double, ByVal y As Double, ByVal ta As Double, ByVal tb As Double) As Double
    If x = NA And y = NA Then
        WAvg = NA
    ElseIf x = NA Then
        WAvg = y
    ElseIf y = NA Then
        WAvg = x
    Else
        WAvg = (x * ta + y * tb) / (ta + tb)
    End If
End Function

Private Function MergeLayers(a As SLayer, b As SLayer) As SLayer
    Dim ta As Double, tb As Double
    ta = a.bot - a.top: tb = b.bot - b.top
    MergeLayers = MakeLayer(a.top, b.bot, WAvg(a.sand, b.sand, ta, tb), WAvg(a.silt, b.silt, ta, tb), _
        WAvg(a.clay, b.clay, ta, tb), WAvg(a.ks, b.ks, ta, tb), WAvg(a.db, b.db, ta, tb), a.nm & "+" & b.nm)
End Function

Private Function Dissim(a As SLayer, b As SLayer) As Double
    Dim dtex As Double, dk As Double
    dtex = Sqr((a.sand - b.sand) ^ 2 + (a.silt - b.silt) ^ 2 + (a.clay - b.clay) ^ 2) / 10#
    dk = 0
    If a.ks > 0 And b.ks > 0 Then dk = Abs(Log(a.ks / b.ks) / Log(10#)) / 0.5
    Dissim = dtex + dk
    If a.tex <> b.tex Then Dissim = Dissim + 0.5
End Function

Private Function MergeScore(a As SLayer, b As SLayer) As Double
    Dim thin As Double
    thin = a.bot - a.top
    If b.bot - b.top < thin Then thin = b.bot - b.top
    MergeScore = Dissim(a, b) * Sqr(thin / 30#)
End Function

Private Sub MergeAt(L() As SLayer, ByRef n As Integer, ByVal k As Integer)
    Dim i As Integer
    L(k) = MergeLayers(L(k), L(k + 1))
    For i = k + 1 To n - 1
        L(i) = L(i + 1)
    Next i
    n = n - 1
End Sub

' merge adjacent layers until there are at most maxL
Private Sub ReduceLayers(L() As SLayer, ByRef n As Integer, ByVal maxL As Integer, ByVal mergeSimilar As Boolean)
    Dim i As Integer, best As Integer, bv As Double, v As Double, changed As Boolean
    If mergeSimilar Then
        changed = True
        Do While changed And n > 1
            changed = False
            best = 1: bv = 1E+30
            For i = 1 To n - 1
                v = Dissim(L(i), L(i + 1))
                If v < bv Then bv = v: best = i
            Next i
            If bv < 0.6 Then
                MergeAt L, n, best
                changed = True
            End If
        Loop
    End If
    Do While n > maxL
        best = 1: bv = 1E+30
        For i = 1 To n - 1
            v = MergeScore(L(i), L(i + 1))
            If v < bv Then bv = v: best = i
        Next i
        MergeAt L, n, best
    Loop
End Sub

'-------------------------------------------------- soil preparation -----
Private Function SpLayerAt(ByVal y As Double) As Integer
    Dim i As Integer
    For i = 1 To spNL
        If y < spBot(i) Then SpLayerAt = i: Exit Function
    Next i
    SpLayerAt = spNL
End Function

' theta at suction h from the layer's water characteristic (linear in h)
Private Function SpTheta(ByVal l As Integer, ByVal h As Double) As Double
    Dim i As Integer
    If h <= spH(l, 1) Then SpTheta = spT(l, 1): Exit Function
    For i = 2 To spNP(l)
        If h <= spH(l, i) Then
            SpTheta = spT(l, i - 1) + (spT(l, i) - spT(l, i - 1)) * (h - spH(l, i - 1)) / (spH(l, i) - spH(l, i - 1))
            Exit Function
        End If
    Next i
    SpTheta = spT(l, spNP(l))
End Function

Private Function SpSuction(ByVal l As Integer, ByVal t As Double) As Double
    Dim i As Integer
    For i = 2 To spNP(l)
        If t <= spT(l, i - 1) And t >= spT(l, i) Then
            If spT(l, i - 1) = spT(l, i) Then
                SpSuction = spH(l, i - 1)
            Else
                SpSuction = spH(l, i - 1) + (spH(l, i) - spH(l, i - 1)) * (spT(l, i - 1) - t) / (spT(l, i - 1) - spT(l, i))
            End If
            Exit Function
        End If
    Next i
    SpSuction = spH(l, spNP(l))
End Function

' Millington-Quirk conductivity table: theta from saturation down in 0.01
' steps, suction at the midpoint of each class, matched to Ks at saturation,
' with the (theta/theta_s)^(4/3) pore-interaction term.
Private Sub SpBuildMQ(ByVal l As Integer)
    Dim ts As Double, tmin As Double, m As Integer, k As Integer, t As Double, i As Integer, j As Integer
    Dim Hm() As Double, S() As Double
    ts = spT(l, 1): tmin = spT(l, spNP(l))
    m = 0
    Do
        t = Round(ts - 0.01 * m, 6)
        If t < tmin - 0.000000001 Then Exit Do
        m = m + 1
        mqT(l, m) = t
        mqH(l, m) = SpSuction(l, t)
        If m >= UBound(mqT, 2) Then Exit Do
    Loop
    If m < 2 Then Err.Raise vbObjectError + 570, "SoilPrep", "Layer " & l & " water characteristic spans less than 0.01 cm3/cm3."
    mqN(l) = m
    ReDim Hm(1 To m): ReDim S(1 To m)
    For j = 2 To m
        Hm(j) = (mqH(l, j - 1) + mqH(l, j)) / 2
    Next j
    For i = 1 To m
        S(i) = 0
        For j = i + 1 To m
            S(i) = S(i) + (2 * (j - i) - 1) / (Hm(j) ^ 2)
        Next j
    Next i
    For i = 1 To m
        mqK(l, i) = spKs(l) * S(i) / S(1) * (mqT(l, i) / ts) ^ (4# / 3#)
    Next i
    mqK(l, m) = mqK(l, m - 1)
End Sub

Private Function SpSav(ByVal l As Integer) As Double
    Dim i As Integer, s As Double
    For i = 2 To mqN(l)
        s = s + (mqK(l, i - 1) + mqK(l, i)) / 2 / spKs(l) * (mqH(l, i) - mqH(l, i - 1))
    Next i
    SpSav = s
End Function

Private Function SpThetaMQ(ByVal l As Integer, ByVal h As Double) As Double
    Dim i As Integer
    If h <= 0 Then SpThetaMQ = mqT(l, 1): Exit Function
    For i = 2 To mqN(l)
        If h <= mqH(l, i) Then
            SpThetaMQ = mqT(l, i - 1) + (mqT(l, i) - mqT(l, i - 1)) * (h - mqH(l, i - 1)) / (mqH(l, i) - mqH(l, i - 1))
            Exit Function
        End If
    Next i
    SpThetaMQ = mqT(l, mqN(l))
End Function

Private Function SpLogK(ByVal l As Integer, ByVal h As Double) As Double
    Dim i As Integer, f As Double
    If h <= 0 Then SpLogK = mqK(l, 1): Exit Function
    For i = 2 To mqN(l)
        If h <= mqH(l, i) Then
            f = (h - mqH(l, i - 1)) / (mqH(l, i) - mqH(l, i - 1))
            SpLogK = Exp(Log(mqK(l, i - 1)) + f * (Log(mqK(l, i)) - Log(mqK(l, i - 1))))
            Exit Function
        End If
    Next i
    SpLogK = mqK(l, mqN(l))
End Function

' distance (cm) below the root zone at which a water table supplies upward
' flux q (cm/hr) with suction hroot at the bottom of the root zone
Private Function SpUpDistance(ByVal q As Double, ByVal root As Double, ByVal hroot As Double) As Double
    Dim z As Double, h As Double, li As Integer, bot As Double, np As Integer, j As Integer, i As Integer
    Dim ph(1 To 260) As Double, pk(1 To 260) As Double, dzs As Double, fr As Double, crossed As Boolean
    z = root: h = hroot: li = SpLayerAt(z)
    Do
        If li < spNL Then bot = spBot(li) Else bot = 1E+30
        np = 1: ph(1) = h: pk(1) = SpLogK(li, h)
        For i = mqN(li) To 1 Step -1
            If mqH(li, i) < h Then np = np + 1: ph(np) = mqH(li, i): pk(np) = mqK(li, i)
        Next i
        crossed = False
        For j = 1 To np - 1
            dzs = (ph(j) - ph(j + 1)) * (1 / (1 + q / pk(j)) + 1 / (1 + q / pk(j + 1))) / 2
            If dzs > 0 And z + dzs >= bot Then
                fr = (bot - z) / dzs
                h = ph(j) - fr * (ph(j) - ph(j + 1))
                z = bot: li = li + 1: crossed = True
                Exit For
            End If
            z = z + dzs
        Next j
        If Not crossed Then SpUpDistance = z - root: Exit Function
    Loop
End Function

' drained volume (cm) for a water table at depth d: hydrostatic profile,
' 1-cm slices, single-precision sum as in the DRAINMOD utility
Private Function SpVolume(ByVal d As Double) As Single
    Dim v As Single, y As Double, y2 As Double, l As Integer, ts As Double
    y = 0
    Do While y < d - 0.000000001
        y2 = y + 1: If y2 > d Then y2 = d
        l = SpLayerAt((y + y2) / 2)
        ts = spT(l, 1)
        v = v + CSng((y2 - y) * ((ts - SpTheta(l, d - y)) + (ts - SpTheta(l, d - y2))) / 2)
        y = y2
    Loop
    SpVolume = v
End Function

Private Function SpVolumeHomog(ByVal l As Integer, ByVal d As Long) As Single
    Dim v As Single, k As Long, ts As Double
    ts = spT(l, 1)
    For k = 0 To d - 1
        v = v + CSng(((ts - SpTheta(l, k)) + (ts - SpTheta(l, k + 1))) / 2)
    Next k
    SpVolumeHomog = v
End Function

' Write the .SIN, .MIS and .WDV files from the soil-prep arrays
Private Sub SpWriteFiles(ByVal soilName As String, ByVal maxRoot As Double, ByVal avgRoot As Double, _
        ByVal sinPath As String, ByVal misPath As String, ByVal wdvPath As String)
    Dim volD As Variant, gaD As Variant, upQ As Variant, f As Integer, l As Integer, i As Integer, k As Long
    Dim upDist(0 To 20) As Double, d As Double, q As Double, top As Double, th As Double, w As Double
    Dim ksw As Double, a As Double, m As Double, ln As String, v As Single

    volD = Array(0, 3, 6, 9, 12, 15, 20, 25, 30, 35, 40, 45, 60, 75, 90, 120, 150, 200, 500, 1000)
    gaD = Array(0, 10, 20, 40, 60, 80, 100, 150, 200, 1000)
    upQ = Array(0.5, 0.2, 0.15, 0.1, 0.075, 0.05, 0.025, 0.02, 0.015, 0.012, 0.01, 0.0075, 0.005, 0.004, _
        0.003, 0.002, 0.001, 0.0008, 0.0006, 0.0004, 0.0002)
    For l = 1 To spNL
        SpBuildMQ l
    Next l
    For i = 0 To 20
        upDist(i) = SpUpDistance(CDbl(upQ(i)), avgRoot, 1000#)
    Next i

    ' ---- .SIN
    f = FreeFile
    Open sinPath For Output As #f
    Print #f, Left$(soilName & Space$(80), 80)
    Print #f, RJ(spNP(1), 2) & RJ(UBound(volD) + 1, 2)
    For i = 1 To spNP(1)
        Print #f, FmtF(spT(1, i), 10, 3) & RJ(-CLng(spH(1, i)), 9) & "."
    Next i
    For k = 0 To UBound(volD)
        d = volD(k)
        v = SpVolume(d)
        If v > 100 Then v = 100
        If d >= 1000 Then
            q = 0
        ElseIf d <= upDist(0) Then
            q = upQ(0)
        Else
            q = 0
            For i = 1 To 20
                If d <= upDist(i) Then
                    q = Exp(Log(upQ(i - 1)) + (d - upDist(i - 1)) / (upDist(i) - upDist(i - 1)) * (Log(upQ(i)) - Log(upQ(i - 1))))
                    Exit For
                End If
            Next i
        End If
        Print #f, FmtF(d, 10, 1) & FmtF(CDbl(v), 10, 3) & FmtF(q, 10, 4)
    Next k
    Print #f, CStr(UBound(gaD) + 1)
    ' Green-Ampt: thickness-weighted over the layers in the top maxRoot cm
    w = 0: ksw = 0: top = 0
    For l = 1 To spNL
        th = spBot(l): If th > maxRoot Then th = maxRoot
        th = th - top: top = spBot(l)
        If th > 0 Then w = w + th: ksw = ksw + th * Round(spKs(l), 3)
    Next l
    If w <= 0 Then w = 1: ksw = Round(spKs(1), 3)
    For k = 0 To UBound(gaD)
        d = gaD(k): a = 0: top = 0
        For l = 1 To spNL
            th = spBot(l): If th > maxRoot Then th = maxRoot
            th = th - top: top = spBot(l)
            If th > 0 Then
                If d <= 100 Then m = mqT(l, 1) - SpThetaMQ(l, d) Else m = mqT(l, 1) - mqT(l, mqN(l))
                a = a + th * Round(spKs(l), 3) * SpSav(l) * m
            End If
        Next l
        Print #f, FmtF(d, 10, 1) & FmtF(a / w, 10, 2) & FmtF(ksw / w, 10, 2)
    Next k
    Print #f, ""
    Close #f

    ' ---- .MIS
    f = FreeFile
    Open misPath For Output As #f
    Print #f, RJ(spNL, 4)
    top = 0
    For l = 1 To spNL
        Print #f, " *TOP AND BOTTOM OF LAYER"
        Print #f, FmtF(top, 8, 1) & FmtF(spBot(l), 8, 1)
        Print #f, " *NUMBER OF POINTS IN LAYER"
        Print #f, RJ(spNP(l), 4)
        For i = 1 To spNP(l)
            Print #f, FmtF(spT(l, i), 7, 3) & FmtF(spH(l, i), 12, 1)
        Next i
        top = spBot(l)
    Next l
    Close #f

    ' ---- .WDV
    f = FreeFile
    Open wdvPath For Output As #f
    Print #f, "*NLAYER"
    Print #f, RJ(spNL, 3)
    top = 0
    For l = 1 To spNL
        Print #f, "*TLAYER BLAYER NPTS"
        Print #f, FmtF(top, 7, 1) & FmtF(spBot(l), 8, 1) & RJ(201, 5)
        Print #f, "*WATER TABLE  VOLUME DRAINED"
        For k = 0 To 200
            Print #f, RJ(k, 8) & FmtF(CDbl(SpVolumeHomog(l, k)), 14, 5)
        Next k
        top = spBot(l)
    Next l
    Close #f
End Sub

'------------------------------------------------- main entry: soil ------
' Finds the soil (SSURGO), writes .soi/.SIN/.MIS/.WDV under
' <DRAINMOD folder>\online\soils\, and stores on DMData: file paths, the
' impermeable-layer depth and five lateral-conductivity layers.  Results are
' reused while the site, soil choice and root depths are unchanged.
Public Function BuildOnlineSoil() As String
    Dim lat As Double, lon As Double, mode As String, series As String, stateName As String, key As String
    Dim mu As Object, comp As Object, hz As Collection, warn As String, cokey As String, compname As String
    Dim L() As SLayer, nAll As Integer, A() As SLayer, nLa As Integer, B() As SLayer, nLb As Integer
    Dim i As Integer, j As Integer, cls As String, tr As Double, ts As Double, al As Double, nv As Double, ksc As Double
    Dim suct As Variant, folder As String, baseP As String, soiP As String, sinP As String, misP As String, wdvP As String
    Dim f As Integer, maxRoot As Double, avgRoot As Double, rootLayer As Integer, soilName As String, tex As String
    Dim h As Object, desc As String, area As String, pct As String, dmd As Worksheet, kb() As Double, kk() As Double
    Dim nk As Integer, thick As Double, impDepth As Double, prevB As Double, county As String, scope As String

    Set dmd = Sheets("DMData")
    GetSite lat, lon
    mode = LCase$(Trim$(CStr(Sheets("New Flow File").Range("SoilSource").Value)))
    stateName = Sheets("New Flow File").Range("StateName")
    series = ""
    On Error Resume Next
    series = Trim$(CStr(Sheets("New Flow File").OLEObjects("ComboBox2").Object.Text))
    On Error GoTo 0
    If series = "" Then series = Trim$(CStr(Sheets("New Flow File").Range("SoilFileName").Value))
    maxRoot = Val(Sheets("New Flow File").Range("MaxRoot").Value): If maxRoot <= 0 Then maxRoot = 45
    avgRoot = Val(Sheets("New Flow File").Range("AvgRoot").Value): If avgRoot <= 0 Then avgRoot = 30
    If avgRoot > maxRoot Then avgRoot = maxRoot
    If Left$(mode, 4) = "soil" Then
        mode = "list"
        county = Trim$(CStr(Sheets("DMData").Range("CountyName").Value))
        key = "list|" & stateName & "|" & county & "|" & series
    Else
        mode = "point"
        key = "point|" & NumStr(lat, 5) & "|" & NumStr(lon, 5)
    End If
    key = key & "|" & maxRoot & "|" & avgRoot

    If Not RefreshRequested() And dmd.Range("SoilKey").Value = key Then
        If FileHasData(CStr(dmd.Range("SinPath").Value)) And FileHasData(CStr(dmd.Range("MisPath").Value)) Then
            BuildOnlineSoil = CStr(dmd.Range("OnlineSoilName").Value)
            Exit Function
        End If
    End If

    Application.StatusBar = "Querying USDA Soil Data Access (SSURGO) ..."
    DoEvents
    If mode = "point" Then
        Set mu = SdaMapunitAt(lon, lat)
        If mu Is Nothing Then Err.Raise vbObjectError + 562, "Soil", "No SSURGO map unit at " & NumStr(lat, 5) & ", " & NumStr(lon, 5) & "."
        Set comp = SdaDefaultComponent(mu("mukey"))
        If comp Is Nothing Then Err.Raise vbObjectError + 563, "Soil", "SSURGO map unit " & mu("mukey") & " has no components."
        area = mu("areasymbol") & ", map unit " & mu("mukey") & " " & mu("muname")
    Else
        If series = "" Then Err.Raise vbObjectError + 565, "Soil", "Pick a soil in the soil dropdown on the New Flow File sheet."
        Set comp = SdaSeriesComponent(series, county, stateName, scope)
        If comp Is Nothing Then Err.Raise vbObjectError + 564, "Soil", "No SSURGO soil named '" & series & "' was found in " & _
            Replace(county, "_", " ") & " County or elsewhere in " & Replace(stateName, "_", " ") & "."
        area = comp("areaname") & " (" & comp("areasymbol") & "), map unit " & comp("mukey") & " " & comp("muname")
        If scope = "state" Then warn = warn & series & " is not mapped in " & Replace(county, "_", " ") & _
            " County; used the largest " & series & " component in " & Replace(stateName, "_", " ") & ". "
    End If
    cokey = comp("cokey"): compname = comp("compname"): pct = comp("comppct_r")
    Set hz = SdaHorizons(cokey)

    nAll = CleanHorizons(hz, L, warn)
    ' layers for the water characteristic (.soi): merge near-identical, max 5
    ReDim A(1 To nAll): ReDim B(1 To nAll)
    For i = 1 To nAll
        A(i) = L(i): B(i) = L(i)
    Next i
    nLa = nAll: nLb = nAll
    ReduceLayers A, nLa, 5, True
    ' layers for lateral conductivity (.gen): keep up to 5 horizons
    ReduceLayers B, nLb, 5, False

    ' soil name: component + first mineral surface texture
    soilName = compname
    For Each h In hz
        tex = Trim$(h("texdesc"))
        If tex <> "" And Not HasAnyWord(tex, NonMineralWords()) Then soilName = compname & " " & LCase$(tex): Exit For
    Next h

    ' van Genuchten (Rosetta class averages) -> .soi values
    suct = Array(0, 20, 40, 60, 80, 100, 150, 200, 250, 330, 500, 1000, 2500, 5000, 10000, 15000)
    spNL = nLa
    ReDim spBot(1 To nLa): ReDim spKs(1 To nLa): ReDim spNP(1 To nLa)
    ReDim spT(1 To nLa, 1 To 16): ReDim spH(1 To nLa, 1 To 16)
    ReDim mqN(1 To nLa): ReDim mqT(1 To nLa, 1 To 200): ReDim mqH(1 To nLa, 1 To 200): ReDim mqK(1 To nLa, 1 To 200)
    For i = 1 To nLa
        RosettaClass A(i).tex, tr, ts, al, nv, ksc
        If A(i).ks = NA Then
            A(i).ks = ksc
            warn = warn & "Layer " & i & " has no SSURGO Ksat; used the texture-class value. "
        End If
        If A(i).ks < 0.0001 Then A(i).ks = 0.0001
        spBot(i) = Round(A(i).bot, 1)
        spKs(i) = Round(A(i).ks, 4)
        spNP(i) = 16
        For j = 1 To 16
            spT(i, j) = Round(VGTheta(tr, ts, al, nv, CDbl(suct(j - 1))), 3)
            spH(i, j) = suct(j - 1)
        Next j
    Next i
    rootLayer = SpLayerAt(avgRoot - 0.000001)

    folder = OnlineFolder() & "soils\" & SafeName(compname & "_" & cokey) & "\"
    EnsureFolder folder
    baseP = folder & SafeName(compname)
    soiP = baseP & ".soi": sinP = baseP & ".SIN": misP = baseP & ".MIS": wdvP = baseP & ".WDV"

    f = FreeFile
    Open soiP For Output As #f
    Print #f, Left$(soilName, 80)
    Print #f, RJ(spNL, 2) & RJ(0, 2)
    For i = 1 To spNL
        Print #f, RJ(16, 2) & FmtF(spKs(i), 9, 4) & FmtF(spBot(i), 9, 1)
        For j = 1 To 16
            Print #f, FmtF(spT(i, j), 5, 3) & FmtF(spH(i, j), 8, 1)
        Next j
    Next i
    Print #f, RJ(CLng(maxRoot), 4) & RJ(CLng(avgRoot), 4) & RJ(rootLayer, 4)
    Close #f

    Application.StatusBar = "DRAINMOD soil preparation for " & soilName & " ..."
    DoEvents
    SpWriteFiles soilName, maxRoot, avgRoot, sinP, misP, wdvP

    ' lateral conductivity layers for the .gen file: exactly 5 (split the
    ' thickest layers if SSURGO has fewer than 5 horizons)
    impDepth = Round(B(nLb).bot, 0)
    ReDim kb(1 To 5): ReDim kk(1 To 5)
    nk = nLb
    For i = 1 To nLb
        kb(i) = B(i).bot
        If B(i).ks = NA Then
            RosettaClass B(i).tex, tr, ts, al, nv, ksc
            kk(i) = ksc
        Else
            kk(i) = B(i).ks
        End If
    Next i
    Do While nk < 5
        j = 1: thick = 0
        For i = 1 To nk
            If i = 1 Then prevB = 0 Else prevB = kb(i - 1)
            If kb(i) - prevB > thick Then
                thick = kb(i) - prevB
                j = i
            End If
        Next i
        For i = nk To j Step -1
            kb(i + 1) = kb(i): kk(i + 1) = kk(i)
        Next i
        If j = 1 Then prevB = 0 Else prevB = kb(j - 1)
        kb(j) = prevB + thick / 2
        nk = nk + 1
    Loop
    For i = 1 To 5
        dmd.Range("KLayers").Cells(i, 1).Value = Round(kb(i), 0)
        If kk(i) >= 99.5 Then
            dmd.Range("KLayers").Cells(i, 2).Value = Round(kk(i), 0)
        ElseIf kk(i) >= 9.995 Then
            dmd.Range("KLayers").Cells(i, 2).Value = Round(kk(i), 1)
        Else
            dmd.Range("KLayers").Cells(i, 2).Value = Round(kk(i), 2)
        End If
    Next i
    dmd.Range("KLayers").Cells(5, 1).Value = impDepth

    desc = soilName & " (" & pct & "% of " & area & "); " & spNL & " layer(s) to " & spBot(spNL) & " cm"
    If warn <> "" Then desc = desc & ". Notes: " & warn
    dmd.Range("OnlineImpDepth").Value = impDepth
    dmd.Range("OnlineSoilName").Value = compname
    dmd.Range("SoiPath").Value = soiP
    dmd.Range("SinPath").Value = sinP
    dmd.Range("MisPath").Value = misP
    dmd.Range("WdvPath").Value = wdvP
    dmd.Range("SoilKey").Value = key
    If mode = "point" Then Sheets("New Flow File").Range("SoilFileName").Value = compname
    Sheets("New Flow File").Range("SoilFound").Value = desc
    Application.StatusBar = False
    BuildOnlineSoil = compname
End Function

' Called when the site, soil source, soil name or root depths change on the
' New Flow File sheet: fetches the soil, shows it, sets the profile depth.
Public Sub RefreshSoilPreview()
    Dim impDepth As Variant
    On Error GoTo Failed
    Application.EnableEvents = False
    Application.Cursor = xlWait
    EnsureSoilFiles
    impDepth = Sheets("DMData").Range("OnlineImpDepth").Value
    Sheets("New Flow File").Range("MSDepth").Value = impDepth
    If Sheets("New Flow File").Range("ChkSoilDepth").Value <> 1 Then Sheets("New Flow File").Range("SSDepth").Value = impDepth
Done:
    Application.Cursor = xlDefault
    Application.EnableEvents = True
    Application.StatusBar = False
    Exit Sub
Failed:
    Sheets("New Flow File").Range("SoilFound").Value = "Soil lookup failed: " & Err.Description
    Resume Done
End Sub


'-------------------------------------------- Illinois soil database ----
' Illinois soils picked from the dropdown come from the county database folder
' (<DRAINMOD folder>\Illinois\Soils\<representative soil>.dsi/.sin/.mis/.wdv).
' An Illinois soil that is not in the dropdown (Locate from Map sets "Soil
' source" to "SSURGO at site"), and all other states, use SSURGO.
Public Function IllinoisSoilDb() As Boolean
    IllinoisSoilDb = (Trim$(CStr(Sheets("New Flow File").Range("StateName").Value)) = "Illinois") _
        And (LCase$(Left$(Trim$(CStr(Sheets("New Flow File").Range("SoilSource").Value)), 4)) <> "ssur")
End Function

' Points DMData at the database files for the representative soil in
' SoilFileName, checks they exist, and returns the impermeable-layer depth.
Public Function SetIllinoisDbSoil() As Double
    Dim folder As String, soil As String, f As Integer, impDepth As Variant, nLay As Variant, ext As Variant
    Dim dmd As Worksheet
    Set dmd = Sheets("DMData")
    If Not EnsureDrainmodFolder() Then Err.Raise vbObjectError + 579, "Soil", "No DRAINMOD folder was chosen."
    folder = IllinoisSoilFolder()
    If folder = "" Then Err.Raise vbObjectError + 579, "Soil", "No Illinois soil database folder was chosen. " & _
        "Choose it, or set 'Soil source' on the New Flow File sheet to SSURGO at site."
    soil = Trim$(CStr(Sheets("New Flow File").Range("SoilFileName").Value))
    If soil = "" Then Err.Raise vbObjectError + 580, "Soil", "Pick a soil in the soil dropdown on the New Flow File sheet."
    For Each ext In Array(".dsi", ".sin", ".mis", ".wdv")
        If Not FileHasData(folder & soil & ext) Then
            Err.Raise vbObjectError + 581, "Soil", "Illinois soil database file not found: " & folder & soil & ext & _
                vbLf & "Pick another soil, or set 'Soil source' on the New Flow File sheet to SSURGO at site."
        End If
    Next ext
    f = FreeFile
    Open folder & soil & ".dsi" For Input As #f
    Input #f, impDepth, nLay
    Close #f
    dmd.Range("SoiPath").Value = ""
    dmd.Range("SinPath").Value = folder & soil & ".sin"
    dmd.Range("MisPath").Value = folder & soil & ".mis"
    dmd.Range("WdvPath").Value = folder & soil & ".wdv"
    dmd.Range("OnlineImpDepth").Value = impDepth
    dmd.Range("OnlineSoilName").Value = soil
    dmd.Range("SoilKey").Value = "illinois-db|" & soil
    Sheets("New Flow File").Range("SoilFound").Value = soil & " from the Illinois soil database (" & folder & "); profile depth " & impDepth & " cm"
    SetIllinoisDbSoil = CDbl(impDepth)
End Function

' Makes sure the soil files for the current inputs exist (database or SSURGO)
Public Sub EnsureSoilFiles()
    If IllinoisSoilDb() Then
        SetIllinoisDbSoil
    Else
        BuildOnlineSoil
    End If
End Sub

' Dominant SSURGO component at a point (for Locate from Map)
Public Function PointSoilName(ByVal lat As Double, ByVal lon As Double, ByRef muname As String) As String
    Dim mu As Object, comp As Object
    Set mu = SdaMapunitAt(lon, lat)
    If mu Is Nothing Then Exit Function
    Set comp = SdaDefaultComponent(mu("mukey"))
    If comp Is Nothing Then Exit Function
    muname = mu("muname")
    PointSoilName = comp("compname")
End Function
