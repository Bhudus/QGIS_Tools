Attribute VB_Name = "Module4"
Public Const DAYMET_URL As String = "https://daymet.ornl.gov/single-pixel/api/data"
Public Const NEX_THREDDS As String = "https://ds.nccs.nasa.gov/thredds"
Public Const NEX_ROOT As String = "AMES/NEX/GDDP-CMIP6"
Public Const SIM_BASE_Y1 As Integer = 1996
Public Const SIM_BASE_Y2 As Integer = 2025
Public Const NEX_HIST_END As Integer = 2014       ' last year of the NEX-GDDP-CMIP6 historical runs
Public Const TIGER_COUNTIES As String = "https://tigerweb.geo.census.gov/arcgis/rest/services/TIGERweb/State_County/MapServer"
Private countyCache As Object          ' state FIPS -> Dictionary(normalized county name -> "lat|lon")
Public gLocating As Boolean            ' True while Locate from Map sets the state/county/soil
Private gUseCurl As Boolean             ' True once curl.exe worked where Windows' HTTP components failed
Private Const HTTP_UA As String = "DRAINMOD-County-Simulations-Excel/2.0"
Private Const DM_APP As String = "DRAINMOD County Simulations"   ' registry key for saved settings
Private gNoParallel As Boolean          ' True if downloading several files at once did not work here
Private Const NEX_PARALLEL As Long = 3  ' NEX-GDDP downloads at a time (NASA's server throttles busy clients)
#If VBA7 Then
Private Declare PtrSafe Sub SleepMs Lib "kernel32" Alias "Sleep" (ByVal dwMilliseconds As Long)
#Else
Private Declare Sub SleepMs Lib "kernel32" Alias "Sleep" (ByVal dwMilliseconds As Long)
#End If
Private Const NET_HINT As String = "Check that the website opens in your web browser. If it does, a VPN, firewall or antivirus program may be blocking secure connections from Excel: try again off the VPN, or ask IT to allow the site."

Function Bilinear(Y As Single, X As Single, xvalues As Range, yvalues As Range, zvalues As Range)
With WorksheetFunction


   X1 = .Index(xvalues, .Match(X, xvalues, 1))
   X2 = .Index(xvalues, .Match(X, xvalues, 1) + 1)
   Y1 = .Index(yvalues, .Match(Y, yvalues, 1))
   Y2 = .Index(yvalues, .Match(Y, yvalues, 1) + 1)
   
   Q11 = .Index(zvalues, .Match(Y1, yvalues, 0), .Match(X1, xvalues, 0))
   Q12 = .Index(zvalues, .Match(Y2, yvalues, 0), .Match(X1, xvalues, 0))
   Q21 = .Index(zvalues, .Match(Y1, yvalues, 0), .Match(X2, xvalues, 0))
   Q22 = .Index(zvalues, .Match(Y2, yvalues, 0), .Match(X2, xvalues, 0))
   
   Z = 1 / ((X2 - X1) * (Y2 - Y1)) * (Q11 * (X2 - X) * (Y2 - Y) + Q21 * (X - X1) * (Y2 - Y) + Q12 * (X2 - X) * (Y - Y1) + Q22 * (X - X1) * (Y - Y1))
  End With
  
  Bilinear = Z
   
End Function

Public Function InterpolateXY(ByRef X As Single, ByRef Y As Single, ByRef XRange As Range, ByRef YRange As Range, ByRef ValueTable As Range) As Double
    Dim iCol As Long
    Dim iRow As Long
    Dim C1 As Double
    Dim C2 As Double
    Dim C3 As Double
    Dim C4 As Double
    Dim F1 As Double
    Dim F2 As Double
    Dim C5 As Double
    Dim C6 As Double
    
    'search X
    iCol = 1
    Do While X >= XRange(1, iCol).Value And (iCol < XRange.Columns.Count)
        iCol = iCol + 1
    Loop
    
    'search Y
    iRow = 1
    Do While Y >= YRange(iRow, 1).Value And (iRow < YRange.Rows.Count)
        iRow = iRow + 1
    Loop
    
    'if X or Y fall outside the table, the function result is an error value
    If (X < XRange(1, 1).Value) Or (X > XRange(1, iCol).Value) Or (Y < YRange(1, 1).Value) Or (Y > YRange(iRow, 1).Value) Then
        InterpolateXY = (1 / 0)
        Exit Function
    End If
    
    '4 data table points 'around' X and Y
    C1 = ValueTable.Cells(iRow - 1, iCol - 1).Value
    C2 = ValueTable.Cells(iRow - 1, iCol).Value
    C3 = ValueTable.Cells(iRow, iCol - 1).Value
    C4 = ValueTable.Cells(iRow, iCol).Value

    'X and Y fractions
    F1 = (X - XRange.Cells(1, iCol - 1).Value) / (XRange.Cells(1, iCol).Value - XRange.Cells(1, iCol - 1).Value)
    F2 = (Y - YRange.Cells(iRow - 1, 1).Value) / (YRange.Cells(iRow, 1).Value - YRange.Cells(iRow - 1, 1).Value)

    '2 intermediate results after interpolation on X
    C5 = C1 + F1 * (C2 - C1)
    C6 = C3 + F1 * (C4 - C3)
    
    'final result after interpolation on Y
    InterpolateXY = C5 + F2 * (C6 - C5)
    
End Function


'==========================================================================
' ONLINE WEATHER DATA
'   Observed weather : Daymet single-pixel API (ORNL DAAC), 1980 onward
'   Simulated weather: NASA NEX-GDDP-CMIP6 through the NCCS THREDDS NetCDF
'                      Subset Service (one grid cell, one request per
'                      variable-year)
' The DRAINMOD .RAI/.TEM files are written with the same rules as the
' DRAINMOD_Weather_Files and drainmod_climate QGIS plugins (and DRAINMOD's
' weather utility, metric input): rain days spread over hours 17-20 in
' hundredths of an inch, temperatures in whole deg F.
' Files are cached in <DRAINMOD folder>\online\wx\ and reused unless
' "Re-download online data" is set to Yes on the New Flow File sheet.
'==========================================================================


'------------------------------------------------------ DRAINMOD program ----
' Location of the DRAINMOD hydrology program (DMhydro.exe). The user picks the
' file; the choice is remembered for this Windows user (SaveSetting).
' forceAsk = True always shows the file dialog (opening at the last choice);
' otherwise the dialog appears only when no valid location is saved.
' Returns "" if the user cancels and no valid location is saved.
Public Function DMHydroPath(Optional ByVal forceAsk As Boolean = False) As String
    Dim p As String, fd As Object, startAt As String, pick As String
    On Error Resume Next
    p = GetSetting(DM_APP, "Paths", "DMhydro", "")
    If p <> "" Then
        If Dir$(p) = "" Then p = ""
    End If
    On Error GoTo 0
    If p <> "" And Not forceAsk Then
        DMHydroPath = p
        Exit Function
    End If
    startAt = p
    If startAt = "" Then startAt = Trim$(CStr(Sheets("New Flow File").Range("CurrentPath").Value)) & "\"
    Set fd = Application.FileDialog(3)            ' msoFileDialogFilePicker
    Do
        With fd
            .Title = "Select the DRAINMOD hydrology program (DMhydro.exe)"
            .AllowMultiSelect = False
            .Filters.Clear
            .Filters.Add "Programs", "*.exe"
            .InitialFileName = startAt
            If .Show <> -1 Then Exit Do               ' cancelled: keep any saved location
            pick = .SelectedItems(1)
        End With
        If LCase$(Mid$(pick, InStrRev(pick, "\") + 1)) = "dmhydro.exe" Then Exit Do
        If MsgBox("The selected file is not named DMhydro.exe:" & vbLf & pick & vbLf & vbLf & _
                  "Use it as the DRAINMOD hydrology program anyway?", vbYesNo + vbQuestion, "DRAINMOD") = vbYes Then Exit Do
        startAt = pick
        pick = ""
    Loop
    If pick <> "" Then
        p = pick
        SaveSetting DM_APP, "Paths", "DMhydro", p
    End If
    DMHydroPath = p
End Function

'-------------------------------------------------------------- HTTP ----
' Downloads try Windows' HTTP components first (MSXML ServerXMLHTTP and XMLHTTP,
' then WinHttpRequest with TLS 1.3 allowed). If none of them gets an answer from
' the server (for example a secure channel / TLS / SSL handshake error), the
' request is retried with the curl.exe that ships with Windows 10 and 11.
Private Function HttpRequest(ByVal verb As String, ByVal url As String, ByVal body As String, ByVal contentType As String) As String
    Dim tries As Integer, lastErr As String, progId As Variant, answered As Boolean
    Dim stat As Long, txt As String, errMsg As String, errs As String
    For tries = 1 To 3
        answered = False
        stat = 0
        errs = ""
        If Not gUseCurl Then
            For Each progId In Array("MSXML2.ServerXMLHTTP.6.0", "MSXML2.XMLHTTP.6.0", "WinHttp.WinHttpRequest.5.1")
                errMsg = ""
                answered = TryXmlHttp(CStr(progId), verb, url, body, contentType, stat, txt, errMsg)
                If answered Then Exit For
                errs = errs & vbLf & "  " & errMsg
            Next progId
        End If
        If Not answered Then
            errMsg = ""
            answered = TryCurl(verb, url, body, contentType, stat, txt, errMsg)
            If answered Then
                gUseCurl = True          ' use curl for the rest of this session
            Else
                errs = errs & vbLf & "  " & errMsg
                gUseCurl = False
            End If
        End If
        If answered Then
            If stat = 200 Then
                HttpRequest = txt
                Exit Function
            End If
            lastErr = "HTTP " & stat & ": " & Left$(txt, 400)
            If stat = 404 Or stat = 400 Then GoTo GiveUp
        Else
            lastErr = "Could not connect to the server:" & errs & vbLf & vbLf & NET_HINT
        End If
        If stat = 429 Or stat = 503 Then
            Application.Wait Now + TimeSerial(0, 0, 15 * tries)     ' server busy: wait longer
        Else
            Application.Wait Now + TimeSerial(0, 0, 2 * tries)
        End If
    Next tries
GiveUp:
    Err.Raise vbObjectError + 513, "HttpRequest", lastErr & vbLf & "URL: " & url
End Function

' One request through an MSXML / WinHTTP object. Returns True if the server
' answered (any HTTP status), False if no answer came back (error in errMsg).
Private Function TryXmlHttp(ByVal progId As String, ByVal verb As String, ByVal url As String, ByVal body As String, _
        ByVal contentType As String, ByRef stat As Long, ByRef txt As String, ByRef errMsg As String) As Boolean
    Dim http As Object
    On Error GoTo Failed
    Set http = CreateObject(progId)
    If progId <> "MSXML2.XMLHTTP.6.0" Then http.setTimeouts 30000, 30000, 180000, 180000
    If progId = "WinHttp.WinHttpRequest.5.1" Then
        On Error Resume Next
        http.Option(9) = &H2A80          ' TLS 1.0 to 1.3 (TLS 1.3 needs Windows 11)
        If Err.Number <> 0 Then
            Err.Clear
            http.Option(9) = &HA80       ' TLS 1.0 to 1.2
        End If
        On Error GoTo Failed
    End If
    http.Open verb, url, False
    http.setRequestHeader "User-Agent", HTTP_UA
    If contentType <> "" Then http.setRequestHeader "Content-Type", contentType
    If verb = "POST" Then
        http.send body
    Else
        http.send
    End If
    stat = http.Status
    txt = http.responseText
    TryXmlHttp = True
    Exit Function
Failed:
    errMsg = progId & ": " & Trim$(Replace(Replace(Err.Description, vbCr, " "), vbLf, " "))
    TryXmlHttp = False
End Function

' One request through Windows' curl.exe (Windows 10 version 1803 and later).
' --ssl-no-revoke skips only the certificate revocation check, which often
' fails behind firewalls; the certificate itself is still verified.
Private Function TryCurl(ByVal verb As String, ByVal url As String, ByVal body As String, ByVal contentType As String, _
        ByRef stat As Long, ByRef txt As String, ByRef errMsg As String) As Boolean
    Dim exe As String, tmp As String, fOut As String, fHdr As String, fErr As String, fBody As String
    Dim cmd As String, rc As Long, f As Integer, hdr As String, lns() As String, i As Long
    On Error GoTo Failed
    exe = Environ$("SystemRoot") & "\System32\curl.exe"
    If Dir$(exe) = "" Then
        errMsg = "curl.exe: not found (needs Windows 10 version 1803 or later)"
        Exit Function
    End If
    tmp = Environ$("TEMP") & "\drainmod_http_" & Format$(Now, "hhnnss") & Format$((Timer * 1000) Mod 1000, "000")
    fOut = tmp & ".out": fHdr = tmp & ".hdr": fErr = tmp & ".err": fBody = tmp & ".body"
    cmd = """" & exe & """ -sS -L --ssl-no-revoke --connect-timeout 30 --max-time 300" & _
          " -A """ & HTTP_UA & """ -D """ & fHdr & """ -o """ & fOut & """ --stderr """ & fErr & """"
    If verb = "POST" Then
        f = FreeFile
        Open fBody For Binary Access Write As #f
        Put #f, , body
        Close #f
        cmd = cmd & " -X POST --data-binary ""@" & fBody & """"
        If contentType <> "" Then cmd = cmd & " -H ""Content-Type: " & contentType & """"
    End If
    cmd = cmd & " """ & url & """"
    rc = CreateObject("WScript.Shell").Run(cmd, 0, True)
    hdr = ReadTextFile(fHdr, "us-ascii")
    If rc <> 0 Or hdr = "" Then
        errMsg = Trim$(Replace(Replace(ReadTextFile(fErr, "us-ascii"), vbCr, " "), vbLf, " "))
        If errMsg = "" Then errMsg = "exit code " & rc
        errMsg = "curl: " & errMsg
    Else
        lns = Split(Replace(hdr, vbCr, ""), vbLf)
        For i = 0 To UBound(lns)
            If Left$(lns(i), 5) = "HTTP/" Then stat = Val(Split(lns(i), " ")(1))   ' last response wins
        Next i
        txt = ReadTextFile(fOut, "utf-8")
        TryCurl = True
    End If
Cleanup:
    On Error Resume Next
    Kill fOut
    Kill fHdr
    Kill fErr
    Kill fBody
    Exit Function
Failed:
    errMsg = "curl: " & Err.Description
    TryCurl = False
    Resume Cleanup
End Function

' Downloads several URLs, up to `slots` at a time. Returns the response texts
' in the same order; an entry is "" where that download failed. With slots = 1
' (or when parallel downloads do not work here) the URLs are fetched one at a
' time with HttpGetText.
Private Function FetchMany(urls() As String, ByVal label As String, ByVal slots As Long) As String()
    Dim res() As String, n As Long, i As Long, got As Long
    n = UBound(urls)
    ReDim res(1 To n)
    If slots > 1 And Not gNoParallel Then
        If gUseCurl Then
            FetchManyCurl urls, res, label, slots
        Else
            FetchManyXml urls, res, label, slots
        End If
        For i = 1 To n
            If res(i) <> "" Then got = got + 1
        Next i
        If got = 0 And n >= 3 Then gNoParallel = True     ' parallel did nothing here: one at a time from now on
    Else
        For i = 1 To n
            StatusMsg label & " (" & i & " of " & n & ")"
            On Error Resume Next
            res(i) = HttpGetText(urls(i))
            If Err.Number <> 0 Then res(i) = ""
            Err.Clear
            On Error GoTo 0
        Next i
    End If
    FetchMany = res
End Function

Private Function SecondsSince(ByVal t0 As Double) As Double
    SecondsSince = Timer - t0
    If SecondsSince < 0 Then SecondsSince = SecondsSince + 86400#     ' past midnight
End Function

' Parallel downloads with asynchronous MSXML ServerXMLHTTP requests. If the
' server answers "busy" (HTTP 429 or 503), only one request is sent at a time
' for the rest of the batch.
Private Sub FetchManyXml(urls() As String, res() As String, ByVal label As String, ByVal slots As Long)
    Const MAX_SECONDS As Double = 240
    Dim http() As Object, job() As Long, t0() As Double
    Dim n As Long, nextJob As Long, done As Long, s As Long, st As Long, rs As Long
    Dim running As Long, maxRunning As Long, wasInteractive As Boolean
    ReDim http(1 To slots): ReDim job(1 To slots): ReDim t0(1 To slots)
    n = UBound(urls)
    nextJob = 1
    maxRunning = slots
    wasInteractive = Application.Interactive
    Application.Interactive = False          ' no clicks while downloads run (the screen still updates)
    Do
        running = 0
        For s = 1 To slots
            If job(s) <> 0 Then
                rs = 0
                On Error Resume Next
                rs = http(s).readyState
                On Error GoTo 0
                If rs = 4 Or SecondsSince(t0(s)) > MAX_SECONDS Then
                    st = 0
                    On Error Resume Next
                    If rs = 4 Then
                        st = http(s).Status
                        If st = 200 Then res(job(s)) = http(s).responseText
                    Else
                        http(s).abort
                    End If
                    On Error GoTo 0
                    If st = 429 Or st = 503 Then maxRunning = 1
                    Set http(s) = Nothing
                    job(s) = 0
                    done = done + 1
                    StatusMsg label & " (" & done & " of " & n & ")"
                Else
                    running = running + 1
                End If
            End If
        Next s
        For s = 1 To slots
            If job(s) = 0 And nextJob <= n And running < maxRunning Then
                Err.Clear
                On Error Resume Next
                Set http(s) = CreateObject("MSXML2.ServerXMLHTTP.6.0")
                http(s).setTimeouts 30000, 30000, 180000, 180000
                http(s).Open "GET", urls(nextJob), True
                http(s).setRequestHeader "User-Agent", HTTP_UA
                http(s).send
                If Err.Number = 0 Then
                    job(s) = nextJob
                    t0(s) = Timer
                    running = running + 1
                Else
                    Set http(s) = Nothing
                    done = done + 1           ' left empty: retried later
                End If
                On Error GoTo 0
                nextJob = nextJob + 1
            End If
        Next s
        If running = 0 And nextJob > n Then Exit Do
        DoEvents
        SleepMs 50
    Loop
    Application.Interactive = wasInteractive
End Sub

' Parallel downloads with Windows' curl.exe (used once curl has been needed
' for this session). Needs curl 7.66 or later for -Z (Windows 10 21H2, 11).
' curl itself waits and retries when the server answers "busy".
Private Sub FetchManyCurl(urls() As String, res() As String, ByVal label As String, ByVal slots As Long)
    Const BATCH As Long = 24
    Dim exe As String, tmp As String, cfg As String, cmd As String, f As Integer
    Dim n As Long, i As Long, j As Long, jEnd As Long, outFile() As String
    exe = Environ$("SystemRoot") & "\System32\curl.exe"
    If Dir$(exe) = "" Then Exit Sub
    n = UBound(urls)
    ReDim outFile(1 To n)
    tmp = Environ$("TEMP") & "\drainmod_batch_" & Format$(Now, "hhnnss")
    For i = 1 To n Step BATCH
        jEnd = i + BATCH - 1
        If jEnd > n Then jEnd = n
        cfg = tmp & "_" & i & ".cfg"
        f = FreeFile
        Open cfg For Output As #f
        For j = i To jEnd
            outFile(j) = tmp & "_" & j & ".csv"
            Print #f, "url = """ & urls(j) & """"
            Print #f, "output = """ & Replace(outFile(j), "\", "\\") & """"
        Next j
        Close #f
        StatusMsg label & " (" & (i - 1) & " of " & n & ")"
        cmd = """" & exe & """ -sS -f -L --ssl-no-revoke -Z --parallel-max " & slots & _
              " --retry 3 --retry-delay 20 --connect-timeout 30 --max-time 300" & _
              " -A """ & HTTP_UA & """ -K """ & cfg & """"
        On Error Resume Next
        CreateObject("WScript.Shell").Run cmd, 0, True
        On Error GoTo 0
        For j = i To jEnd
            res(j) = ReadTextFile(outFile(j), "utf-8")
            On Error Resume Next
            Kill outFile(j)
            On Error GoTo 0
        Next j
        On Error Resume Next
        Kill cfg
        On Error GoTo 0
    Next i
End Sub

Private Sub SaveTextFile(ByVal path As String, ByVal txt As String)
    Dim f As Integer
    On Error Resume Next
    f = FreeFile
    Open path For Output As #f
    Print #f, txt;
    Close #f
End Sub

Private Function ReadTextFile(ByVal path As String, ByVal charset As String) As String
    Dim st As Object
    On Error GoTo Done
    If Dir$(path) = "" Then Exit Function
    If FileLen(path) = 0 Then Exit Function
    Set st = CreateObject("ADODB.Stream")
    st.Type = 2
    st.charset = charset
    st.Open
    st.LoadFromFile path
    ReadTextFile = st.ReadText
    st.Close
Done:
End Function

Public Function HttpGetText(ByVal url As String) As String
    HttpGetText = HttpRequest("GET", url, "", "")
End Function

Public Function HttpPostJson(ByVal url As String, ByVal body As String) As String
    HttpPostJson = HttpRequest("POST", url, body, "application/json")
End Function

Public Function UrlEncode(ByVal s As String) As String
    Dim i As Long, c As String, out As String
    For i = 1 To Len(s)
        c = Mid$(s, i, 1)
        Select Case AscW(c)
            Case 48 To 57, 65 To 90, 97 To 122, 45, 46, 95, 126
                out = out & c
            Case Else
                out = out & "%" & Right$("0" & Hex$(AscW(c) And 255), 2)
        End Select
    Next i
    UrlEncode = out
End Function

'------------------------------------------------------ small helpers ----
' Number to text with a period as the decimal separator, whatever the
' Windows regional settings are.
Public Function NumStr(ByVal x As Double, ByVal decimals As Integer) As String
    Dim fmt As String
    If decimals > 0 Then fmt = "0." & String$(decimals, "0") Else fmt = "0"
    NumStr = Replace(Format$(x, fmt), ",", ".")
    If Left$(NumStr, 1) = "-" And Val(NumStr) = 0 Then NumStr = Mid$(NumStr, 2)
End Function

' Fortran-style fixed field: Fw.d  (right justified, period decimal)
Public Function FmtF(ByVal x As Double, ByVal w As Integer, ByVal d As Integer) As String
    FmtF = NumStr(x, d)
    If Len(FmtF) < w Then FmtF = Space$(w - Len(FmtF)) & FmtF
End Function

' Fortran-style Iw
Public Function RJ(ByVal v As Variant, ByVal w As Integer) As String
    RJ = CStr(v)
    If Len(RJ) < w Then RJ = Space$(w - Len(RJ)) & RJ
End Function

' Fortran NINT: round half away from zero
Public Function FNint(ByVal x As Double) As Long
    If x >= 0 Then FNint = Int(x + 0.5) Else FNint = -Int(-x + 0.5)
End Function

Public Function IsLeapYear(ByVal y As Long) As Boolean
    IsLeapYear = ((y Mod 4 = 0) And (y Mod 100 <> 0)) Or (y Mod 400 = 0)
End Function

Public Sub EnsureFolder(ByVal path As String)
    Dim parts() As String, i As Integer, cur As String
    If Right$(path, 1) = "\" Then path = Left$(path, Len(path) - 1)
    parts = Split(path, "\")
    If Left$(path, 2) = "\\" And UBound(parts) >= 3 Then
        cur = "\\" & parts(2) & "\" & parts(3)
        i = 4
    Else
        cur = parts(0)
        i = 1
    End If
    Do While i <= UBound(parts)
        cur = cur & "\" & parts(i)
        If Dir(cur, vbDirectory) = "" Then MkDir cur
        i = i + 1
    Loop
End Sub

Public Function OnlineFolder() As String
    Dim p As String
    p = Sheets("New Flow File").Range("CurrentPath")
    If Right$(p, 1) <> "\" Then p = p & "\"
    OnlineFolder = p & "online\"
    EnsureFolder OnlineFolder
End Function

Public Function SafeName(ByVal s As String) As String
    Dim i As Long, c As String
    For i = 1 To Len(s)
        c = Mid$(s, i, 1)
        If c Like "[A-Za-z0-9_.-]" Then SafeName = SafeName & c Else SafeName = SafeName & "_"
    Next i
    If SafeName = "" Then SafeName = "site"
End Function

Public Function FileHasData(ByVal f As String) As Boolean
    FileHasData = False
    If Trim$(f) = "" Then Exit Function
    If Dir(f) <> "" Then
        If FileLen(f) > 0 Then FileHasData = True
    End If
End Function

Public Function RefreshRequested() As Boolean
    Dim v As String
    On Error Resume Next
    v = LCase$(Trim$(CStr(Sheets("New Flow File").Range("RefreshOnline").Value)))
    On Error GoTo 0
    RefreshRequested = (v = "yes" Or v = "y" Or v = "1" Or v = "true")
End Function

' Site latitude / longitude from the New Flow File sheet (validated)
Public Sub GetSite(ByRef lat As Double, ByRef lon As Double)
    Dim a As Variant, b As Variant
    a = Sheets("New Flow File").Range("SiteLat").Value
    b = Sheets("New Flow File").Range("SiteLon").Value
    If IsEmpty(a) Or IsEmpty(b) Then
        If SetCountyCentroid(False) Then
            a = Sheets("New Flow File").Range("SiteLat").Value
            b = Sheets("New Flow File").Range("SiteLon").Value
        End If
    End If
    If Not IsNumeric(a) Or Not IsNumeric(b) Or IsEmpty(a) Or IsEmpty(b) Then
        Err.Raise vbObjectError + 520, "GetSite", "Enter the site latitude and longitude (decimal degrees) on the New Flow File sheet."
    End If
    lat = CDbl(a): lon = CDbl(b)
    If lon > 0 And lon <= 180 Then
        Err.Raise vbObjectError + 521, "GetSite", "Longitude must be negative (west) for North American sites."
    End If
    If lat < 14 Or lat > 72 Or lon < -180 Or lon > -52 Then
        Err.Raise vbObjectError + 522, "GetSite", "The site (" & lat & ", " & lon & ") is outside North America."
    End If
End Sub

' DRAINMOD station ID: lat and |lon| truncated to 0.1 deg, e.g. 40.24,-88.03 -> 402880
Public Function StationID(ByVal lat As Double, ByVal lon As Double) As String
    Dim a As Long, b As Long
    a = Int(Abs(lat) * 10 + 0.000000001) Mod 1000
    b = Int(Abs(lon) * 10 + 0.000000001) Mod 1000
    StationID = Format$(a, "000") & Format$(b, "000")
End Function

' latitude in DDMM form, as DRAINMOD's climate line expects (e.g. 3959)
Public Function LatDDMM(ByVal lat As Double) As String
    Dim dg As Long, mn As Long
    dg = Int(Abs(lat))
    mn = Int((Abs(lat) - dg) * 60 + 0.000001)
    If mn > 59 Then mn = 59
    LatDDMM = CStr(dg * 100 + mn)
End Function

Private Sub StatusMsg(ByVal s As String)
    Application.StatusBar = s
    DoEvents
End Sub

'---------------------------------------------- DRAINMOD file writers ----
' yr(), jd(): year, day of year; pr(): mm/day; tx(), tn(): deg C; n: days
Public Sub WriteRaiTem(ByVal raiPath As String, ByVal temPath As String, ByVal sid As String, _
        yr() As Long, jd() As Long, pr() As Double, tx() As Double, tn() As Double, ByVal n As Long)
    Dim f As Integer, i As Long, d As Date, m As Integer, dd As Integer
    Dim curKey As Long, key As Long, cnt As Integer, ln As String
    Dim total As Long, baseV As Long, p As Double, h As Integer
    Dim hiF() As Long, loF() As Long, k As Integer, nd As Integer, line2 As String
    Dim curY As Long, curM As Integer

    ' ---- rainfall (.RAI)
    f = FreeFile
    Open raiPath For Output As #f
    curKey = -1: cnt = 0: ln = ""
    For i = 1 To n
        p = Round(pr(i), 2)
        If p > 0 Then
            d = DateSerial(yr(i), 1, 1) + jd(i) - 1
            m = Month(d): dd = Day(d)
            key = yr(i) * 100 + m
            If key <> curKey Then
                If cnt > 0 Then Print #f, ln
                curKey = key: cnt = 0
            End If
            If cnt = 0 Then ln = RJ(sid, 6) & " " & RJ(yr(i), 4) & RJ(m, 2) & " "
            total = FNint(p / 25.4 * 100#)
            baseV = total \ 4
            For h = 17 To 19
                ln = ln & RJ(dd, 2) & RJ(h, 2) & RJ(baseV, 4)
            Next h
            ln = ln & RJ(dd, 2) & "20" & RJ(total - 3 * baseV, 4)
            cnt = cnt + 1
            If cnt = 3 Then
                Print #f, ln
                cnt = 0
            End If
        End If
    Next i
    If cnt > 0 Then Print #f, ln
    Close #f

    ' ---- temperature (.TEM): two lines per month
    f = FreeFile
    Open temPath For Output As #f
    ReDim hiF(1 To 31): ReDim loF(1 To 31)
    nd = 0: curY = -1: curM = -1
    For i = 1 To n
        d = DateSerial(yr(i), 1, 1) + jd(i) - 1
        m = Month(d)
        If yr(i) <> curY Or m <> curM Then
            If nd > 0 Then GoSub FlushTem
            curY = yr(i): curM = m: nd = 0
        End If
        nd = nd + 1
        hiF(nd) = CToF(tx(i)): loF(nd) = CToF(tn(i))
    Next i
    If nd > 0 Then GoSub FlushTem
    Close #f
    Exit Sub

FlushTem:
    ln = RJ(sid, 6) & " " & RJ(curY, 4) & RJ(curM, 2) & "     "
    line2 = ""
    For k = 1 To nd
        If k <= 14 Then
            ln = ln & RJ(hiF(k), 3) & RJ(loF(k), 3)
        Else
            line2 = line2 & RJ(hiF(k), 3) & RJ(loF(k), 3)
        End If
    Next k
    Print #f, ln
    Print #f, line2
    Return
End Sub

' deg C (rounded to 0.1) -> whole deg F with DRAINMOD's single-precision 1.8
Public Function CToF(ByVal c As Double) As Long
    CToF = FNint(CDbl(CSng(Round(c, 1))) * CDbl(CSng(1.8)) + 32#)
End Function

' Thornthwaite heat index from a DRAINMOD .TEM file (long-term monthly means)
Public Function HeatIndexFromTem(ByVal temPath As String) As Long
    Dim f As Integer, ln As String, ln2 As String, m As Integer, s As String
    Dim sumT(1 To 12) As Double, cnt(1 To 12) As Double, k As Long, hi As Double, lo As Double
    Dim idx As Double, tm As Double
    f = FreeFile
    Open temPath For Input As #f
    Do While Not EOF(f)
        Line Input #f, ln
        If Len(ln) >= 13 Then
            m = Val(Mid$(ln, 12, 2))
            ln2 = ""
            If Not EOF(f) Then Line Input #f, ln2
            s = Mid$(ln, 19) & ln2
            If m >= 1 And m <= 12 Then
                For k = 1 To Len(s) - 5 Step 6
                    hi = Val(Mid$(s, k, 3)): lo = Val(Mid$(s, k + 3, 3))
                    sumT(m) = sumT(m) + ((hi + lo) / 2 - 32) / 1.8
                    cnt(m) = cnt(m) + 1
                Next k
            End If
        End If
    Loop
    Close #f
    idx = 0
    For m = 1 To 12
        If cnt(m) > 0 Then
            tm = sumT(m) / cnt(m)
            If tm > 0 Then idx = idx + (tm / 5) ^ 1.514
        End If
    Next m
    HeatIndexFromTem = CLng(idx)
End Function

'------------------------------------------------------------- Daymet ----
Public Sub DaymetDaily(ByVal lat As Double, ByVal lon As Double, ByVal y0 As Long, ByVal y1 As Long, _
        yr() As Long, jd() As Long, pr() As Double, tx() As Double, tn() As Double, ByRef n As Long, ByRef note As String)
    Dim url As String, txt As String, lines() As String, i As Long, hdr As Long
    Dim cols() As String, ci As Integer, cY As Integer, cJ As Integer, cP As Integer, cX As Integer, cN As Integer
    Dim v() As String, nmax As Long, filled As Long, lastY As Long

    url = DAYMET_URL & "?lat=" & NumStr(lat, 6) & "&lon=" & NumStr(lon, 6) & _
          "&vars=prcp,tmax,tmin&start=" & y0 & "-01-01&end=" & y1 & "-12-31&format=csv"
    StatusMsg "Downloading Daymet " & y0 & "-" & y1 & " ..."
    txt = HttpGetText(url)
    txt = Replace(txt, vbCr, "")
    lines = Split(txt, vbLf)
    hdr = -1
    For i = 0 To UBound(lines)
        If LCase$(Left$(Trim$(lines(i)), 5)) = "year," Then hdr = i: Exit For
    Next i
    If hdr < 0 Then Err.Raise vbObjectError + 530, "Daymet", "Unexpected Daymet response:" & vbLf & Left$(txt, 400)
    cols = Split(LCase$(lines(hdr)), ",")
    cY = -1: cJ = -1: cP = -1: cX = -1: cN = -1
    For ci = 0 To UBound(cols)
        Select Case Split(Trim$(cols(ci)), " ")(0)
            Case "year": cY = ci
            Case "yday": cJ = ci
            Case "prcp": cP = ci
            Case "tmax": cX = ci
            Case "tmin": cN = ci
        End Select
    Next ci
    If cY < 0 Or cJ < 0 Or cP < 0 Or cX < 0 Or cN < 0 Then
        Err.Raise vbObjectError + 531, "Daymet", "Daymet response is missing a column: " & lines(hdr)
    End If
    nmax = (y1 - y0 + 1) * 366 + 10
    ReDim yr(1 To nmax): ReDim jd(1 To nmax): ReDim pr(1 To nmax): ReDim tx(1 To nmax): ReDim tn(1 To nmax)
    n = 0: filled = 0
    For i = hdr + 1 To UBound(lines)
        If InStr(lines(i), ",") > 0 Then
            v = Split(lines(i), ",")
            If UBound(v) >= 4 Then
                n = n + 1
                yr(n) = CLng(Val(v(cY))): jd(n) = CLng(Val(v(cJ)))
                pr(n) = Val(v(cP)): tx(n) = Val(v(cX)): tn(n) = Val(v(cN))
                ' Daymet drops Dec 31 in leap years: add it back as day 366
                ' with zero rain and Dec 30 temperatures
                If IsLeapYear(yr(n)) And jd(n) = 365 Then
                    n = n + 1
                    yr(n) = yr(n - 1): jd(n) = 366: pr(n) = 0
                    tx(n) = tx(n - 1): tn(n) = tn(n - 1)
                    filled = filled + 1
                End If
            End If
        End If
    Next i
    If n = 0 Then Err.Raise vbObjectError + 532, "Daymet", "Daymet returned no data for this point and period."
    lastY = yr(n)
    note = "Daymet V4, point " & NumStr(lat, 5) & ", " & NumStr(lon, 5) & "; " & yr(1) & "-" & lastY
    If filled > 0 Then note = note & "; " & filled & " leap-year Dec 31 day(s) filled (0 rain, Dec 30 temperatures)"
    If lastY < y1 Then note = note & "; NOTE: Daymet data end in " & lastY & " (requested " & y1 & ")"
End Sub

'------------------------------------------------------ NEX-GDDP-CMIP6 ----
Private Function XmlDoc(ByVal txt As String) As Object
    Dim doc As Object
    Set doc = CreateObject("MSXML2.DOMDocument.6.0")
    doc.async = False
    doc.validateOnParse = False
    doc.setProperty "SelectionNamespaces", "xmlns:t='http://www.unidata.ucar.edu/namespaces/thredds/InvCatalog/v1.0' xmlns:xlink='http://www.w3.org/1999/xlink'"
    If Not doc.LoadXML(txt) Then Err.Raise vbObjectError + 540, "THREDDS", "Could not read THREDDS catalog: " & Left$(txt, 300)
    Set XmlDoc = doc
End Function

Private Function NexCatalog(ByVal path As String) As Object
    Set NexCatalog = XmlDoc(HttpGetText(NEX_THREDDS & "/catalog/" & path & "/catalog.xml"))
End Function

Private Function NexMember(ByVal model As String, ByVal scen As String) As String
    Dim doc As Object, nd As Object, t As String, best As String
    Set doc = NexCatalog(NEX_ROOT & "/" & model & "/" & scen)
    For Each nd In doc.SelectNodes("//t:catalogRef")
        t = nd.getAttribute("xlink:title")
        If t = "r1i1p1f1" Then NexMember = t: Exit Function
        If best = "" Or t < best Then best = t
    Next nd
    If best = "" Then Err.Raise vbObjectError + 541, "NEX-GDDP", "No ensemble member found for " & model & " " & scen & "."
    NexMember = best
End Function

' year -> urlPath (highest file version) for one variable
Private Function NexYearFiles(ByVal model As String, ByVal scen As String, ByVal member As String, ByVal var As String) As Object
    Dim doc As Object, nd As Object, nm As String, up As String, y As Long, ver As Double
    Dim d As Object, vers As Object, p As Long, q As Long
    Set d = CreateObject("Scripting.Dictionary")
    Set vers = CreateObject("Scripting.Dictionary")
    Set doc = NexCatalog(NEX_ROOT & "/" & model & "/" & scen & "/" & member & "/" & var)
    For Each nd In doc.SelectNodes("//t:dataset[@urlPath]")
        up = nd.getAttribute("urlPath")
        nm = Mid$(up, InStrRev(up, "/") + 1)
        If LCase$(Right$(nm, 3)) = ".nc" Then
            nm = Left$(nm, Len(nm) - 3)
            ver = 0
            p = InStrRev(nm, "_v")
            If p > 0 And p > Len(nm) - 8 Then
                ver = Val(Mid$(nm, p + 2))
                nm = Left$(nm, p - 1)
            End If
            q = InStrRev(nm, "_")
            y = Val(Mid$(nm, q + 1))
            If y > 1900 And y < 2200 Then
                If Not d.Exists(y) Then
                    d.Add y, up: vers.Add y, ver
                ElseIf ver > vers(y) Then
                    d(y) = up: vers(y) = ver
                End If
            End If
        End If
    Next nd
    Set NexYearFiles = d
End Function

' local cache file name for one variable-year download
Private Function NexCacheName(ByVal urlPath As String) As String
    NexCacheName = SafeName(Mid$(urlPath, InStrRev(urlPath, "/") + 1)) & ".csv"
End Function

' NCSS grid-as-point URL for one variable-year file (CSV, whole calendar year)
Private Function NexYearUrl(ByVal urlPath As String, ByVal var As String, ByVal lat As Double, ByVal lon360 As Double, ByVal y As Long) As String
    NexYearUrl = NEX_THREDDS & "/ncss/grid/" & urlPath & "?var=" & var & "&latitude=" & NumStr(lat, 5) & "&longitude=" & NumStr(lon360, 5) & _
                 "&time_start=" & y & "-01-01T00:00:00Z&time_end=" & y & "-12-31T23:59:59Z&accept=csv"
End Function

' One NCSS grid-as-point request; returns rows "y-m-d" -> value in dict
Private Function NexFetchYear(ByVal urlPath As String, ByVal var As String, ByVal lat As Double, ByVal lon360 As Double, _
        ByVal y As Long, ByVal vals As Object, ByRef units As String, ByRef cellLat As Double, ByRef cellLon As Double) As Long
    Dim variants(1 To 3) As String, k As Integer, url As String, txt As String, nrows As Long
    Dim baseUrl As String
    baseUrl = NEX_THREDDS & "/ncss/grid/" & urlPath & "?var=" & var & "&latitude=" & NumStr(lat, 5) & "&longitude=" & NumStr(lon360, 5)
    variants(1) = "&time_start=" & y & "-01-01T00:00:00Z&time_end=" & y & "-12-31T23:59:59Z"
    variants(2) = "&temporal=all"
    variants(3) = "&time_start=" & y & "-01-01T00:00:00Z&time_end=" & y & "-12-30T23:59:59Z"
    For k = 1 To 3
        If k = 1 Then url = NexYearUrl(urlPath, var, lat, lon360, y) Else url = baseUrl & variants(k) & "&accept=csv"
        txt = ""
        On Error Resume Next
        txt = HttpGetText(url)
        On Error GoTo 0
        If txt <> "" Then
            nrows = ParseNcssCsv(txt, var, y, vals, units, cellLat, cellLon)
            If nrows >= 300 Then
                NexFetchYear = nrows
                Exit Function
            End If
        End If
    Next k
    Err.Raise vbObjectError + 542, "NEX-GDDP", "NASA's NEX-GDDP-CMIP6 server did not return " & var & " data for " & y & _
        " after several tries; it may be busy or down. The years already downloaded are saved, so running " & _
        "the simulation again later continues from where this run stopped." & vbLf & vbLf & "Last request: " & url
End Function

Private Function ParseNcssCsv(ByVal txt As String, ByVal var As String, ByVal y As Long, ByVal vals As Object, _
        ByRef units As String, ByRef cellLat As Double, ByRef cellLon As Double) As Long
    Dim lines() As String, hdr() As String, i As Long, vi As Integer, ti As Integer, la As Integer, lo As Integer
    Dim nm As String, parts() As String, ds As String, p As Long, n As Long, key As String
    Dim yy As Long, mm As Long, dd As Long, v As Double
    txt = Replace(txt, vbCr, "")
    lines = Split(txt, vbLf)
    If UBound(lines) < 1 Then Exit Function
    If Left$(LTrim$(lines(0)), 1) = "<" Then Exit Function
    hdr = Split(lines(0), ",")
    vi = UBound(hdr): ti = 0: la = -1: lo = -1
    For i = 0 To UBound(hdr)
        nm = LCase$(Trim$(Split(hdr(i), "[")(0)))
        nm = Replace(nm, """", "")
        If nm = LCase$(var) Then vi = i
        If nm = "time" Or nm = "date" Then ti = i
        If Left$(nm, 3) = "lat" Then la = i
        If Left$(nm, 3) = "lon" Then lo = i
    Next i
    p = InStr(hdr(vi), "unit=""")
    If p > 0 Then units = Mid$(hdr(vi), p + 6, InStr(p + 6, hdr(vi), """") - p - 6)
    For i = 1 To UBound(lines)
        If Len(Trim$(lines(i))) > 0 Then
            parts = Split(Replace(lines(i), """", ""), ",")
            If UBound(parts) >= vi Then
                ds = Trim$(parts(ti))
                ' ISO date yyyy-mm-dd (model calendar: may be Feb 30)
                yy = Val(Left$(ds, 4)): mm = Val(Mid$(ds, 6, 2)): dd = Val(Mid$(ds, 9, 2))
                If yy = y And mm >= 1 And mm <= 12 And dd >= 1 And dd <= 31 Then
                    v = Val(parts(vi))
                    If Abs(v) < 1E+19 Then
                        key = yy & "-" & mm & "-" & dd
                        vals(key) = v
                        n = n + 1
                    End If
                    If la >= 0 And lo >= 0 And cellLat = 0 Then
                        cellLat = Val(parts(la)): cellLon = Val(parts(lo))
                    End If
                End If
            End If
        End If
    Next i
    ParseNcssCsv = n
End Function

' Download one NEX-GDDP model/scenario at a point and return a real-calendar
' daily series (pr mm/day, tmax/tmin deg C).  noleap calendars get Feb 29
' inserted (0 rain, interpolated temperature), 360_day calendars are spread
' over the real year, all_leap Feb 29 rain is added to Feb 28.
Public Sub NexDaily(ByVal model As String, ByVal scen As String, ByVal lat As Double, ByVal lon As Double, _
        ByVal y0 As Long, ByVal y1 As Long, yr() As Long, jd() As Long, pr() As Double, tx() As Double, tn() As Double, _
        ByRef n As Long, ByRef note As String)
    Dim member As String, vars As Variant, vi As Integer, var As String, files As Object
    Dim y As Long, data(0 To 2) As Object, units(0 To 2) As String, cellLat As Double, cellLon As Double
    Dim lon360 As Double, req As Long, nreq As Long, missingYears As String
    Dim jobVar() As Integer, jobYear() As Long, jobPath() As String, urls() As String, res() As String
    Dim nj As Long, nrows As Long, got() As Boolean, todo() As Long, nTodo As Long, kk As Long
    Dim tryNo As Integer, pauseS As Long, cacheDir As String, txt As String
    Dim is360 As Boolean, k As Variant, parts() As String, m As Long, d As Long
    Dim nmax As Long, i As Long, ndays As Long, idx As Long, real As Long, dt As Date
    Dim vals() As Double, have() As Boolean, cal As String, filledT As Long, filledP As Long, swapped As Long
    Dim scaleP As Double, offT(1 To 2) As Double, j As Long, a As Long, b As Long, w As Double, tmp As Double

    lon360 = lon: If lon360 < 0 Then lon360 = lon360 + 360
    vars = Array("pr", "tasmax", "tasmin")
    StatusMsg "NEX-GDDP-CMIP6: reading catalog for " & model & " " & scen & " ..."
    member = NexMember(model, scen)
    nreq = 3 * (y1 - y0 + 1)
    ReDim jobVar(1 To nreq): ReDim jobYear(1 To nreq): ReDim jobPath(1 To nreq): ReDim urls(1 To nreq)
    For vi = 0 To 2
        var = vars(vi)
        Set data(vi) = CreateObject("Scripting.Dictionary")
        StatusMsg "NEX-GDDP-CMIP6: reading catalog for " & model & " " & scen & " " & var & " ..."
        Set files = NexYearFiles(model, scen, member, var)
        For y = y0 To y1
            If files.Exists(y) Then
                nj = nj + 1
                jobVar(nj) = vi: jobYear(nj) = y: jobPath(nj) = files(y)
                urls(nj) = NexYearUrl(files(y), var, lat, lon360, y)
            Else
                If InStr(missingYears, CStr(y)) = 0 Then missingYears = missingYears & " " & y
            End If
        Next y
    Next vi
    If nj > 0 Then
        ' Year files saved by an earlier, interrupted run are read from the local
        ' cache. The rest are downloaded a few at a time; any that fail are retried
        ' after a pause (NASA's server may be busy). Each downloaded year is saved,
        ' so if NASA still fails, running again continues where this run stopped.
        cacheDir = OnlineFolder() & "wx\nex_cache\" & SafeName(model & "_" & scen & "_" & NumStr(lat, 3) & "_" & NumStr(lon, 3)) & "\"
        EnsureFolder cacheDir
        ReDim got(1 To nj): ReDim todo(1 To nj)
        For req = 1 To nj
            vi = jobVar(req)
            txt = ReadTextFile(cacheDir & NexCacheName(jobPath(req)), "us-ascii")
            If txt <> "" Then
                If ParseNcssCsv(txt, vars(vi), jobYear(req), data(vi), units(vi), cellLat, cellLon) >= 360 Then got(req) = True
            End If
        Next req
        For tryNo = 1 To 4
            nTodo = 0
            For req = 1 To nj
                If Not got(req) Then
                    nTodo = nTodo + 1
                    todo(nTodo) = req
                End If
            Next req
            If nTodo = 0 Then Exit For
            If tryNo > 1 Then
                pauseS = Choose(tryNo - 1, 20, 60, 120)
                StatusMsg "NASA's server did not return " & nTodo & " file(s). Waiting " & pauseS & _
                          " seconds before trying again (try " & tryNo & " of 4) ..."
                Application.Wait Now + TimeSerial(0, 0, pauseS)
            End If
            ReDim urls(1 To nTodo)
            For kk = 1 To nTodo
                req = todo(kk)
                urls(kk) = NexYearUrl(jobPath(req), vars(jobVar(req)), lat, lon360, jobYear(req))
            Next kk
            res = FetchMany(urls, "NEX-GDDP-CMIP6 " & model & " " & scen & ": downloading", IIf(tryNo = 1, NEX_PARALLEL, 1))
            For kk = 1 To nTodo
                req = todo(kk): vi = jobVar(req)
                If res(kk) <> "" Then
                    If ParseNcssCsv(res(kk), vars(vi), jobYear(req), data(vi), units(vi), cellLat, cellLon) >= 360 Then
                        got(req) = True
                        SaveTextFile cacheDir & NexCacheName(jobPath(req)), res(kk)
                    End If
                End If
            Next kk
        Next tryNo
        ' last try, one at a time with the other date forms (raises an error if a year is still missing)
        For req = 1 To nj
            If Not got(req) Then
                vi = jobVar(req): var = vars(vi)
                StatusMsg "NEX-GDDP-CMIP6 " & model & " " & scen & ": last try for " & var & " " & jobYear(req) & " ..."
                NexFetchYear jobPath(req), var, lat, lon360, jobYear(req), data(vi), units(vi), cellLat, cellLon
            End If
        Next req
        ' all years are in: the per-year cache is no longer needed
        On Error Resume Next
        Kill cacheDir & "*.csv"
        RmDir cacheDir
        On Error GoTo 0
    End If
    If data(0).Count = 0 Then Err.Raise vbObjectError + 543, "NEX-GDDP", model & " " & scen & " has no data for " & y0 & "-" & y1 & "."

    ' unit conversion factors
    scaleP = 1
    If InStr(LCase$(units(0)), "s-1") > 0 Or InStr(units(0), "/s") > 0 Then scaleP = 86400
    For vi = 1 To 2
        offT(vi) = 0
        If UCase$(Trim$(units(vi))) = "K" Or LCase$(units(vi)) = "kelvin" Then offT(vi) = -273.15
    Next vi

    ' calendar
    is360 = False
    For Each k In data(0).Keys
        parts = Split(k, "-")
        If Val(parts(2)) > Day(DateSerial(Val(parts(0)), Val(parts(1)) + 1, 0)) Then is360 = True: Exit For
    Next k
    cal = "standard/noleap"
    If is360 Then cal = "360_day"

    nmax = (y1 - y0 + 1) * 366
    ReDim yr(1 To nmax): ReDim jd(1 To nmax): ReDim pr(1 To nmax): ReDim tx(1 To nmax): ReDim tn(1 To nmax)
    ReDim vals(0 To 2, 1 To nmax): ReDim have(0 To 2, 1 To nmax)
    n = 0
    For y = y0 To y1
        ndays = 365: If IsLeapYear(y) Then ndays = 366
        For i = 1 To ndays
            yr(n + i) = y: jd(n + i) = i
        Next i
        For vi = 0 To 2
            If is360 Then
                For m = 1 To 12
                    For d = 1 To 30
                        If data(vi).Exists(y & "-" & m & "-" & d) Then
                            idx = (m - 1) * 30 + (d - 1)
                            real = CLng(Round(idx * (ndays - 1) / 359#)) + 1
                            vals(vi, n + real) = data(vi)(y & "-" & m & "-" & d): have(vi, n + real) = True
                        End If
                    Next d
                Next m
            Else
                For i = 1 To ndays
                    dt = DateSerial(y, 1, 1) + i - 1
                    If data(vi).Exists(y & "-" & Month(dt) & "-" & Day(dt)) Then
                        vals(vi, n + i) = data(vi)(y & "-" & Month(dt) & "-" & Day(dt)): have(vi, n + i) = True
                    End If
                Next i
                ' all_leap model: Feb 29 in a non-leap year -> rain onto Feb 28
                If Not IsLeapYear(y) And vi = 0 And data(0).Exists(y & "-2-29") Then
                    vals(0, n + 59) = vals(0, n + 59) + data(0)(y & "-2-29")
                End If
            End If
        Next vi
        n = n + ndays
    Next y

    ' fill gaps: rain 0, temperatures interpolated
    For i = 1 To n
        If have(0, i) Then
            pr(i) = vals(0, i) * scaleP
            If pr(i) < 0 Then pr(i) = 0
        Else
            pr(i) = 0: filledP = filledP + 1
        End If
    Next i
    For vi = 1 To 2
        For i = 1 To n
            If have(vi, i) Then
                tmp = vals(vi, i) + offT(vi)
            Else
                a = i - 1
                Do While a >= 1
                    If have(vi, a) Then Exit Do
                    a = a - 1
                Loop
                b = i + 1
                Do While b <= n
                    If have(vi, b) Then Exit Do
                    b = b + 1
                Loop
                If a >= 1 And b <= n Then
                    w = (i - a) / (b - a)
                    tmp = (vals(vi, a) * (1 - w) + vals(vi, b) * w) + offT(vi)
                ElseIf a >= 1 Then
                    tmp = vals(vi, a) + offT(vi)
                ElseIf b <= n Then
                    tmp = vals(vi, b) + offT(vi)
                Else
                    Err.Raise vbObjectError + 544, "NEX-GDDP", "Temperature series is empty."
                End If
                filledT = filledT + 1
            End If
            If vi = 1 Then tx(i) = tmp Else tn(i) = tmp
        Next i
    Next vi
    For i = 1 To n
        If tn(i) > tx(i) Then tmp = tn(i): tn(i) = tx(i): tx(i) = tmp: swapped = swapped + 1
    Next i

    note = "NASA NEX-GDDP-CMIP6 " & model & " " & scen & " " & member & ", grid cell " & _
           NumStr(cellLat, 3) & ", " & NumStr(IIf(cellLon > 180, cellLon - 360, cellLon), 3) & "; " & y0 & "-" & y1 & _
           "; model calendar " & cal & "; " & filledP & " rain day(s) set to 0 and " & filledT & _
           " temperature value(s) interpolated where the model calendar has no date"
    If swapped > 0 Then note = note & "; " & swapped & " day(s) with Tmin > Tmax swapped"
    If missingYears <> "" Then note = note & "; no files for:" & missingYears
End Sub

'------------------------------------------------ your own weather files ----
' New Flow File!WeatherSource = "My own files" means the run uses .RAI/.TEM
' files the user already has (MyRaiFile / MyTemFile) instead of downloading.
Public Function UseOwnWeatherFiles() As Boolean
    Dim v As String
    On Error Resume Next
    v = LCase$(Trim$(CStr(Sheets("New Flow File").Range("WeatherSource").Value)))
    On Error GoTo 0
    UseOwnWeatherFiles = (Left$(v, 6) = "my own")
End Function

' Confirms (or asks for) the .RAI and .TEM files to use for this run and saves
' the choice in New Flow File!MyRaiFile / MyTemFile. Returns False if cancelled.
Private Function PickOwnWeatherFiles(ByVal periodLabel As String, ByRef rai As String, ByRef tem As String) As Boolean
    Dim ans As VbMsgBoxResult
    rai = Trim$(CStr(Sheets("New Flow File").Range("MyRaiFile").Value))
    tem = Trim$(CStr(Sheets("New Flow File").Range("MyTemFile").Value))
    If FileHasData(rai) And FileHasData(tem) Then
        ans = MsgBox("Use these weather files for the " & periodLabel & " run?" & vbLf & vbLf & _
                     "Rainfall:  " & rai & vbLf & "Temperature:  " & tem & vbLf & vbLf & _
                     "Yes = use them     No = choose other files     Cancel = stop", _
                     vbYesNoCancel + vbQuestion, "Your weather files")
        If ans = vbCancel Then Exit Function
        If ans = vbYes Then
            PickOwnWeatherFiles = True
            Exit Function
        End If
    End If
    rai = PickFile("Select the DRAINMOD rainfall file (.RAI) for " & periodLabel, "DRAINMOD rainfall files", "*.rai", rai)
    If rai = "" Then Exit Function
    If Not FileHasData(tem) And InStrRev(rai, ".") > 0 Then tem = Left$(rai, InStrRev(rai, ".")) & "TEM"   ' suggest the matching name
    tem = PickFile("Select the DRAINMOD temperature file (.TEM) for " & periodLabel, "DRAINMOD temperature files", "*.tem", tem)
    If tem = "" Then Exit Function
    Sheets("New Flow File").Range("MyRaiFile").Value = rai
    Sheets("New Flow File").Range("MyTemFile").Value = tem
    PickOwnWeatherFiles = True
End Function

Private Function PickFile(ByVal title As String, ByVal filterName As String, ByVal pattern As String, ByVal startAt As String) As String
    With Application.FileDialog(3)                ' msoFileDialogFilePicker
        .Title = title
        .AllowMultiSelect = False
        .Filters.Clear
        .Filters.Add filterName, pattern
        .Filters.Add "All files", "*.*"
        If startAt <> "" Then .InitialFileName = startAt
        If .Show = -1 Then PickFile = .SelectedItems(1)
    End With
End Function

'------------------------------------------------- main entry: weather ----
' Builds (or reuses) the .RAI/.TEM files for the site and the selected time
' period, and stores their paths in DMData!RaiPath / TemPath.
'Model years before 2015 come from the historical run, later years from the
'scenario; a period that spans 2014/2015 is joined from the two.
Private Sub NexDailySpan(ByVal model As String, ByVal scen As String, ByVal lat As Double, ByVal lon As Double, _
        ByVal y0 As Long, ByVal y1 As Long, yr() As Long, jd() As Long, pr() As Double, tx() As Double, tn() As Double, _
        ByRef n As Long, ByRef note As String)
    Dim yr1() As Long, jd1() As Long, pr1() As Double, tx1() As Double, tn1() As Double, n1 As Long, note1 As String
    Dim yr2() As Long, jd2() As Long, pr2() As Double, tx2() As Double, tn2() As Double, n2 As Long, note2 As String
    Dim i As Long
    If y1 <= NEX_HIST_END Then
        NexDaily model, "historical", lat, lon, y0, y1, yr, jd, pr, tx, tn, n, note
        Exit Sub
    End If
    If y0 > NEX_HIST_END Then
        NexDaily model, scen, lat, lon, y0, y1, yr, jd, pr, tx, tn, n, note
        Exit Sub
    End If
    NexDaily model, "historical", lat, lon, y0, NEX_HIST_END, yr1, jd1, pr1, tx1, tn1, n1, note1
    NexDaily model, scen, lat, lon, NEX_HIST_END + 1, y1, yr2, jd2, pr2, tx2, tn2, n2, note2
    n = n1 + n2
    ReDim yr(1 To n): ReDim jd(1 To n): ReDim pr(1 To n): ReDim tx(1 To n): ReDim tn(1 To n)
    For i = 1 To n1
        yr(i) = yr1(i): jd(i) = jd1(i): pr(i) = pr1(i): tx(i) = tx1(i): tn(i) = tn1(i)
    Next i
    For i = 1 To n2
        yr(n1 + i) = yr2(i): jd(n1 + i) = jd2(i): pr(n1 + i) = pr2(i): tx(n1 + i) = tx2(i): tn(n1 + i) = tn2(i)
    Next i
    note = note1 & vbCrLf & "              " & note2
End Sub

Public Function EnsureWeatherFiles() As Boolean
    Dim lat As Double, lon As Double, clm As Variant, src As String, y0 As Long, y1 As Long
    Dim model As String, scen As String, tag As String, folder As String, sid As String, shortTag As String
    Dim rai As String, tem As String, yr() As Long, jd() As Long, pr() As Double, tx() As Double, tn() As Double
    Dim n As Long, note As String, f As Integer, est As String, srcLabel As String, periodLabel As String

    GetSite lat, lon
    clm = Worksheets("Main").Range("ClmCheck").Value
    model = Trim$(CStr(Sheets("New Flow File").Range("GCMName").Value))
    scen = LCase$(Trim$(CStr(Sheets("New Flow File").Range("SSPName").Value)))
    If model = "" Then model = "GFDL-ESM4"
    If scen = "" Then scen = "ssp245"
    Select Case clm
        Case 3
            src = "Daymet"
            y0 = Val(Sheets("New Flow File").Range("ObsYear1").Value)
            y1 = Val(Sheets("New Flow File").Range("ObsYear2").Value)
            If y0 < 1980 Then y0 = 1980
            If y1 < y0 Then Err.Raise vbObjectError + 550, "Weather", "Observed weather: last year must not be before first year."
            tag = "Daymet_" & y0 & "-" & y1
            shortTag = "obs" & y0 & "-" & y1
        Case 0
            ' simulated baseline: model historical run to 2014, then the selected scenario
            src = "NEX": y0 = SIM_BASE_Y1: y1 = SIM_BASE_Y2
            tag = model & "_historical+" & scen & "_" & y0 & "-" & y1
            shortTag = model & "_hist-" & scen & "_" & y0
        Case 2
            src = "NEX": y0 = 2070: y1 = 2099
            tag = model & "_" & scen & "_" & y0 & "-" & y1
            shortTag = model & "_" & scen & "_" & y0
        Case Else
            src = "NEX": y0 = 2040: y1 = 2069
            tag = model & "_" & scen & "_" & y0 & "-" & y1
            shortTag = model & "_" & scen & "_" & y0
    End Select

    If UseOwnWeatherFiles() Then
        ' use .RAI/.TEM files the user already has instead of downloading
        If src = "Daymet" Then
            periodLabel = y0 & "-" & y1 & " observed"
        Else
            periodLabel = y0 & "-" & y1 & " " & model & " " & scen
        End If
        If Not PickOwnWeatherFiles(periodLabel, rai, tem) Then
            Err.Raise vbObjectError + 551, "Weather", "No weather files were chosen. Choose your .RAI and .TEM files, " & _
                "or set 'Weather files' on the New Flow File sheet to Download."
        End If
        Sheets("DMData").Range("RaiPath") = rai
        Sheets("DMData").Range("TemPath") = tem
        Sheets("New Flow File").Range("RainfallFileName") = Mid$(rai, InStrRev(rai, "\") + 1) & "  (your file)"
        Sheets("New Flow File").Range("TemperatureFileName") = Mid$(tem, InStrRev(tem, "\") + 1) & "  (your file)"
        StatusMsg "Using your weather files: " & rai & " and " & tem
        EnsureWeatherFiles = True
        Exit Function
    End If

    sid = StationID(lat, lon)
    folder = OnlineFolder() & "wx\" & SafeName(shortTag & "_" & NumStr(lat, 3) & "_" & NumStr(lon, 3)) & "\"
    EnsureFolder folder
    rai = folder & sid & ".RAI"
    tem = folder & sid & ".TEM"

    If RefreshRequested() Or Not FileHasData(rai) Or Not FileHasData(tem) Then
        Application.DisplayStatusBar = True
        If src = "Daymet" Then
            est = "usually less than a minute (one download from Daymet)"
            srcLabel = "Daymet observed weather"
        Else
            est = "a minute or two (" & 3 * (y1 - y0 + 1) & " downloads from NASA NEX-GDDP-CMIP6, three at a time)"
            srcLabel = "NEX-GDDP-CMIP6 " & model & " " & scen
            If y0 <= NEX_HIST_END Then srcLabel = "NEX-GDDP-CMIP6 " & model & " historical to " & NEX_HIST_END & ", then " & scen
        End If
        MsgBox "The routine is creating the weather file for " & y0 & "-" & y1 & " (" & srcLabel & ") at " & _
            NumStr(lat, 3) & ", " & NumStr(lon, 3) & "." & vbLf & vbLf & _
            "This takes " & est & ". Excel may look unresponsive while it works; progress is shown in the " & _
            "status bar at the bottom of the Excel window." & vbLf & vbLf & _
            "The file is saved, so this period and site will not need to be downloaded again." & vbLf & vbLf & _
            "Click OK to start.", vbInformation, "Creating weather file"
        StatusMsg "Creating weather file for " & y0 & "-" & y1 & " (" & srcLabel & ") ..."
        If src = "Daymet" Then
            DaymetDaily lat, lon, y0, y1, yr, jd, pr, tx, tn, n, note
        Else
            NexDailySpan model, scen, lat, lon, y0, y1, yr, jd, pr, tx, tn, n, note
        End If
        StatusMsg "Writing DRAINMOD weather files ..."
        WriteRaiTem rai, tem, sid, yr, jd, pr, tx, tn, n
        f = FreeFile
        Open folder & "README.txt" For Output As #f
        Print #f, "DRAINMOD weather files written by County_Simulations.xlsm"
        Print #f, "Created:      " & Format$(Now, "yyyy-mm-dd hh:nn")
        Print #f, "Station ID:   " & sid
        Print #f, "Site:         lat " & NumStr(lat, 5) & ", lon " & NumStr(lon, 5)
        Print #f, "Source:       " & note
        Print #f, sid & ".RAI   hourly rainfall, hundredths of an inch (rain days spread over hours 17-20)"
        Print #f, sid & ".TEM   daily Tmax/Tmin, deg F"
        Close #f
        StatusMsg "Weather file created for " & y0 & "-" & y1 & " (" & srcLabel & "). Running DRAINMOD ..."
    End If

    Sheets("DMData").Range("RaiPath") = rai
    Sheets("DMData").Range("TemPath") = tem
    Sheets("New Flow File").Range("RainfallFileName") = sid & ".RAI  (" & tag & ")"
    Sheets("New Flow File").Range("TemperatureFileName") = sid & ".TEM  (" & tag & ")"
    EnsureWeatherFiles = True
End Function

' Labels the Main sheet time-period buttons (observed years come from the
' New Flow File sheet; simulated baseline is the NEX-GDDP historical run)
Public Sub SetTimePeriodCaptions()
    Dim y0 As Variant, y1 As Variant
    On Error Resume Next
    y0 = Sheets("New Flow File").Range("ObsYear1").Value
    y1 = Sheets("New Flow File").Range("ObsYear2").Value
    If Not IsNumeric(y0) Or IsEmpty(y0) Then y0 = 1996
    If Not IsNumeric(y1) Or IsEmpty(y1) Then y1 = 2025
    With Worksheets("Main")
        .OptionButton4.Caption = y0 & " - " & y1 & " (Observed)"
        .OptionButton2.Caption = SIM_BASE_Y1 & " to " & SIM_BASE_Y2
        .OptionButton3.Caption = "2040 to 2069"
        .OptionButton1.Caption = "2070 to 2099"
    End With
    On Error GoTo 0
End Sub

'------------------------------------------------- county centroids -----
' County centroid from the US Census TIGERweb counties layer (CENTLAT/
' CENTLON; internal point if no centroid). All counties of a state are read
' in one request and kept for the Excel session.
Private Function StateFips(ByVal st As String) As String
    Select Case st
        Case "Illinois": StateFips = "17"
        Case "Indiana": StateFips = "18"
        Case "Iowa": StateFips = "19"
        Case "Michigan": StateFips = "26"
        Case "Minnesota": StateFips = "27"
        Case "Missouri": StateFips = "29"
        Case "North_Dakota", "North Dakota": StateFips = "38"
        Case "Ohio": StateFips = "39"
        Case "South_Dakota", "South Dakota": StateFips = "46"
        Case "Wisconsin": StateFips = "55"
        Case Else: StateFips = ""
    End Select
End Function

Private Function NormCounty(ByVal s As String) As String
    Dim i As Long, c As String, out As String
    s = LCase$(Replace(s, "_", " "))
    If Right$(s, 7) = " county" Then s = Left$(s, Len(s) - 7)
    If Right$(s, 7) = " parish" Then s = Left$(s, Len(s) - 7)
    If Left$(s, 6) = "saint " Then s = "st " & Mid$(s, 7)
    For i = 1 To Len(s)
        c = Mid$(s, i, 1)
        If c Like "[a-z]" Then out = out & c
    Next i
    NormCounty = out
End Function

' value text after "key": in a JSON object fragment ("" if absent / null)
Private Function JsonFieldText(ByVal obj As String, ByVal key As String) As String
    Dim p As Long, q As Long, c As String
    p = InStr(1, obj, """" & key & """", vbTextCompare)
    If p = 0 Then Exit Function
    p = InStr(p + Len(key) + 2, obj, ":")
    If p = 0 Then Exit Function
    p = p + 1
    Do While Mid$(obj, p, 1) = " "
        p = p + 1
    Loop
    c = Mid$(obj, p, 1)
    If c = """" Then
        q = InStr(p + 1, obj, """")
        JsonFieldText = Mid$(obj, p + 1, q - p - 1)
    Else
        q = p
        Do While q <= Len(obj) And InStr(",}] ", Mid$(obj, q, 1)) = 0
            q = q + 1
        Loop
        JsonFieldText = Mid$(obj, p, q - p)
        If JsonFieldText = "null" Then JsonFieldText = ""
    End If
End Function

Private Function TigerCountyLayerId() As String
    Dim txt As String, p As Long, q As Long, seg As String
    txt = HttpGetText(TIGER_COUNTIES & "?f=json")
    p = InStr(txt, """layers""")
    Do While p > 0
        p = InStr(p + 1, txt, "{")
        If p = 0 Then Exit Do
        q = InStr(p, txt, "}")
        seg = Mid$(txt, p, q - p + 1)
        If JsonFieldText(seg, "name") = "Counties" Then
            TigerCountyLayerId = JsonFieldText(seg, "id")
            Exit Function
        End If
        If InStr(seg, "]") > 0 Then Exit Do
    Loop
    Err.Raise vbObjectError + 590, "County centroid", "TIGERweb has no 'Counties' layer."
End Function

Private Function StateCounties(ByVal fips As String) As Object
    Dim txt As String, url As String, p As Long, q As Long, seg As String, d As Object
    Dim nm As String, la As String, lo As String
    If countyCache Is Nothing Then Set countyCache = CreateObject("Scripting.Dictionary")
    If countyCache.Exists(fips) Then Set StateCounties = countyCache(fips): Exit Function
    url = TIGER_COUNTIES & "/" & TigerCountyLayerId() & "/query?where=" & UrlEncode("STATE='" & fips & "'") & _
          "&outFields=" & UrlEncode("NAME,BASENAME,CENTLAT,CENTLON,INTPTLAT,INTPTLON") & "&returnGeometry=false&f=json"
    txt = HttpGetText(url)
    Set d = CreateObject("Scripting.Dictionary")
    p = InStr(txt, """attributes""")
    Do While p > 0
        q = InStr(p, txt, "}")
        seg = Mid$(txt, p, q - p + 1)
        nm = JsonFieldText(seg, "NAME")
        la = JsonFieldText(seg, "CENTLAT"): lo = JsonFieldText(seg, "CENTLON")
        If la = "" Or lo = "" Then la = JsonFieldText(seg, "INTPTLAT"): lo = JsonFieldText(seg, "INTPTLON")
        If nm <> "" And la <> "" And lo <> "" Then d(NormCounty(nm)) = Val(la) & "|" & Val(lo)
        p = InStr(q, txt, """attributes""")
    Loop
    If d.Count = 0 Then Err.Raise vbObjectError + 591, "County centroid", "TIGERweb returned no counties for state " & fips & "."
    countyCache.Add fips, d
    Set StateCounties = d
End Function

' Sets the site latitude/longitude on New Flow File to the centroid of the
' selected county. Returns False (cells unchanged) if it cannot be found.
Public Function SetCountyCentroid(Optional ByVal showErrors As Boolean = False) As Boolean
    Dim st As String, county As String, fips As String, d As Object, k As String, v() As String
    Dim ev As Boolean
    On Error GoTo Failed
    st = Trim$(CStr(Sheets("New Flow File").Range("StateName").Value))
    county = Trim$(CStr(Sheets("DMData").Range("CountyName").Value))
    fips = StateFips(st)
    If fips = "" Or county = "" Then Exit Function
    Application.StatusBar = "Looking up the centroid of " & Replace(county, "_", " ") & " County (US Census TIGERweb) ..."
    DoEvents
    Set d = StateCounties(fips)
    k = NormCounty(county)
    If Not d.Exists(k) Then
        If d.Exists(k & "city") Then k = k & "city" Else Err.Raise vbObjectError + 592, "County centroid", _
            "County '" & county & "' was not found in the Census county list for " & st & "."
    End If
    v = Split(d(k), "|")
    ev = Application.EnableEvents
    Application.EnableEvents = False
    Sheets("New Flow File").Range("SiteLat").Value = Round(Val(v(0)), 4)
    Sheets("New Flow File").Range("SiteLon").Value = Round(Val(v(1)), 4)
    Application.EnableEvents = ev
    Application.StatusBar = False
    SetCountyCentroid = True
    Exit Function
Failed:
    Application.EnableEvents = True
    Application.StatusBar = False
    If showErrors Then MsgBox "Could not look up the county centroid: " & Err.Description & vbLf & vbLf & _
        "Enter the site latitude and longitude on the New Flow File sheet.", vbExclamation
End Function
Private Sub WriteLocateMap(ByVal path As String, ByVal token As String, ByVal lat0 As Double, ByVal lon0 As Double, ByVal z0 As Integer)
    Dim f As Integer
    f = FreeFile
    Open path For Output As #f
    Print #f, "<!DOCTYPE html>"
    Print #f, "<html><head><meta charset=""utf-8""><title>DRAINMOD - Locate from map</title>"
    Print #f, "<meta name=""viewport"" content=""width=device-width, initial-scale=1"">"
    Print #f, "<link rel=""stylesheet"" href=""https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"">"
    Print #f, "<script src=""https://unpkg.com/leaflet@1.9.4/dist/leaflet.js""></script>"
    Print #f, "<style>"
    Print #f, "html,body{height:100%;margin:0;font-family:Segoe UI,Arial,sans-serif}"
    Print #f, "#map{position:absolute;top:0;bottom:0;left:0;right:0}"
    Print #f, "#panel{position:absolute;top:10px;left:54px;z-index:1000;background:#fff;padding:10px 12px;border-radius:6px;box-shadow:0 1px 6px rgba(0,0,0,.35);max-width:370px;font-size:13px}"
    Print #f, "#panel b{font-size:15px}"
    Print #f, "#coords{font-family:Consolas,monospace;margin:8px 0}"
    Print #f, "button{font-size:13px;padding:5px 10px;cursor:pointer}"
    Print #f, "#use{background:#1F4E79;color:#fff;border:0;border-radius:4px}"
    Print #f, "#use:disabled{background:#9aa5b1;cursor:default}"
    Print #f, "#msg{color:#0a6b0a;margin-top:6px;font-weight:600}"
    Print #f, "#find{width:220px;padding:4px}"
    Print #f, ".note{margin-top:6px;color:#555}"
    Print #f, "</style></head><body>"
    Print #f, "<div id=""map""></div>"
    Print #f, "<div id=""panel""><b>DRAINMOD: locate the site</b>"
    Print #f, "<div class=""note"">Zoom in and click the field. Then click <i>Use this point</i> and return to Excel.</div>"
    Print #f, "<div style=""margin-top:8px""><input id=""find"" placeholder=""Find a place, e.g. Champaign, IL""> <button id=""go"">Find</button></div>"
    Print #f, "<div id=""coords"">No point selected</div>"
    Print #f, "<button id=""use"" disabled>Use this point</button>"
    Print #f, "<div id=""msg""></div>"
    Print #f, "<div class=""note"">Layers (top right): streets or aerial imagery; soil map units (SSURGO) appear when zoomed in.</div></div>"
    Print #f, "<script>"
    Print #f, "var TOKEN='" & token & "', LAT0=" & NumStr(lat0, 5) & ", LON0=" & NumStr(lon0, 5) & ", Z0=" & z0 & ";"
    Print #f, "var map=L.map('map').setView([LAT0,LON0],Z0);"
    Print #f, "var esri='https://server.arcgisonline.com/ArcGIS/rest/services/';"
    Print #f, "var streets=L.tileLayer(esri+'World_Street_Map/MapServer/tile/{z}/{y}/{x}',{maxZoom:19,attribution:'Tiles &copy; Esri, HERE, Garmin, USGS, NGA, EPA, USDA, NPS'}).addTo(map);"
    Print #f, "var imgBase=L.tileLayer(esri+'World_Imagery/MapServer/tile/{z}/{y}/{x}',{maxZoom:19,attribution:'Imagery &copy; Esri, Maxar, Earthstar Geographics'});"
    Print #f, "var imgLabels=L.tileLayer(esri+'Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',{maxZoom:19});"
    Print #f, "var img=L.layerGroup([imgBase,imgLabels]);"
    Print #f, "var soils=L.tileLayer.wms('https://SDMDataAccess.sc.egov.usda.gov/Spatial/SDM.wms',{layers:'mapunitpoly',format:'image/png',transparent:true,minZoom:12,attribution:'USDA-NRCS SSURGO'}).addTo(map);"
    Print #f, "L.control.layers({'Streets':streets,'Aerial imagery':img},{'Soil map units':soils}).addTo(map);"
    Print #f, "var mk=null,pt=null;"
    Print #f, "map.on('click',function(e){pt=e.latlng;if(mk){mk.setLatLng(pt);}else{mk=L.marker(pt).addTo(map);}"
    Print #f, "document.getElementById('coords').textContent='Latitude '+pt.lat.toFixed(5)+', Longitude '+pt.lng.toFixed(5);"
    Print #f, "document.getElementById('use').disabled=false;document.getElementById('msg').textContent='';});"
    Print #f, "function copyText(t){if(navigator.clipboard&&window.isSecureContext){return navigator.clipboard.writeText(t);}"
    Print #f, "return new Promise(function(res,rej){var a=document.createElement('textarea');a.value=t;document.body.appendChild(a);a.select();"
    Print #f, "try{if(document.execCommand('copy')){res();}else{rej();}}catch(x){rej(x);}document.body.removeChild(a);});}"
    Print #f, "document.getElementById('use').onclick=function(){if(!pt){return;}"
    Print #f, "var t='DRAINMOD_POINT '+TOKEN+' '+pt.lat.toFixed(6)+','+pt.lng.toFixed(6);"
    Print #f, "copyText(t).then(function(){document.getElementById('msg').textContent='Location copied. Return to Excel and click OK.';},"
    Print #f, "function(){document.getElementById('msg').textContent='Could not copy automatically. In Excel click OK, then type: '+pt.lat.toFixed(6)+', '+pt.lng.toFixed(6);});};"
    Print #f, "function findPlace(){var q=document.getElementById('find').value;if(!q){return;}"
    Print #f, "fetch('https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates?f=json&maxLocations=1&countryCode=USA&SingleLine='+encodeURIComponent(q))"
    Print #f, ".then(function(r){return r.json();}).then(function(j){if(j.candidates&&j.candidates.length){var c=j.candidates[0].location;map.setView([c.y,c.x],13);}else{alert('Place not found');}})"
    Print #f, ".catch(function(){alert('Place search is not available');});}"
    Print #f, "document.getElementById('go').onclick=findPlace;"
    Print #f, "document.getElementById('find').onkeydown=function(e){if(e.key==='Enter'){findPlace();}};"
    Print #f, "</script></body></html>"
    Close #f
End Sub

'-------------------------------------------------- Locate from Map -----
' Button "Locate from Map" (New Flow File): opens a map in the web browser;
' the user zooms, clicks the field and presses "Use this point", which puts
' the location on the clipboard. The state, county, latitude/longitude and
' soil are then set from that point.
Public Sub LocateFromMap()
    Dim lat0 As Double, lon0 As Double, z0 As Integer, token As String, path As String
    Dim txt As String, lat As Double, lon As Double, ans As VbMsgBoxResult, a As Variant, b As Variant

    On Error GoTo Failed
    a = Sheets("New Flow File").Range("SiteLat").Value
    b = Sheets("New Flow File").Range("SiteLon").Value
    If IsNumeric(a) And IsNumeric(b) And Not IsEmpty(a) And Not IsEmpty(b) Then
        lat0 = a: lon0 = b: z0 = 11
    Else
        lat0 = 42: lon0 = -91: z0 = 5
    End If
    Randomize
    token = CStr(Int(Rnd * 900000) + 100000)
    path = OnlineFolder() & "map\"
    EnsureFolder path
    path = path & "locate.html"
    WriteLocateMap path, token, lat0, lon0, z0
    ThisWorkbook.FollowHyperlink path

    Do
        ans = MsgBox("A map has opened in your web browser." & vbLf & vbLf & _
            "1. Zoom in and click the field (use 'Find a place' or the aerial imagery layer if helpful)." & vbLf & _
            "2. Click 'Use this point' on the map." & vbLf & _
            "3. Come back here and click OK." & vbLf & vbLf & _
            "The state, county, latitude/longitude and soil will be set from that point.", _
            vbOKCancel + vbInformation, "Locate from Map")
        If ans = vbCancel Then Exit Sub
        txt = ClipboardText()
        If ParseMapPoint(txt, token, lat, lon) Then Exit Do
        txt = InputBox("No location from the map was found on the clipboard." & vbLf & vbLf & _
            "Click 'Use this point' on the map and press OK again, or type the location here as" & vbLf & _
            "latitude, longitude   (for example 40.1123, -88.2410):", "Locate from Map")
        If Trim$(txt) <> "" Then
            If ParseMapPoint("DRAINMOD_POINT " & token & " " & txt, token, lat, lon) Then Exit Do
            MsgBox "Could not read '" & txt & "' as latitude, longitude.", vbExclamation, "Locate from Map"
        End If
    Loop

    ApplyMapLocation lat, lon
    Exit Sub
Failed:
    gLocating = False
    Application.EnableEvents = True
    Application.StatusBar = False
    Application.Cursor = xlDefault
    MsgBox "Locate from Map failed: " & Err.Description, vbExclamation, "Locate from Map"
End Sub

Private Function ClipboardText() As String
    Dim d As Object
    On Error Resume Next
    Set d = GetObject("New:{1C3B4210-F441-11CE-B9EA-00AA006B1A69}")
    d.GetFromClipboard
    ClipboardText = d.GetText
    If ClipboardText = "" Then ClipboardText = CreateObject("htmlfile").parentWindow.clipboardData.GetData("text")
    On Error GoTo 0
End Function

' "DRAINMOD_POINT <token> lat,lon" -> lat, lon (token must match this map)
Private Function ParseMapPoint(ByVal txt As String, ByVal token As String, ByRef lat As Double, ByRef lon As Double) As Boolean
    Dim p As Long, s As String, parts() As String
    p = InStr(txt, "DRAINMOD_POINT " & token & " ")
    If p = 0 Then Exit Function
    s = Trim$(Mid$(txt, p + Len("DRAINMOD_POINT " & token & " ")))
    s = Replace(s, ";", ",")
    If InStr(s, ",") = 0 Then s = Replace(Application.WorksheetFunction.Trim(s), " ", ",")
    parts = Split(s, ",")
    If UBound(parts) < 1 Then Exit Function
    If Not IsNumeric(Val(parts(0))) Or Trim$(parts(0)) = "" Or Trim$(parts(1)) = "" Then Exit Function
    lat = Val(Trim$(parts(0))): lon = Val(Trim$(parts(1)))
    If lon > 0 And lat < 0 Then
        Dim t As Double
        t = lat: lat = lon: lon = t
    End If
    ParseMapPoint = (lat > 14 And lat < 72 And lon > -180 And lon < -52)
End Function

Private Function FipsToState(ByVal fips As String) As String
    Select Case fips
        Case "17": FipsToState = "Illinois"
        Case "18": FipsToState = "Indiana"
        Case "19": FipsToState = "Iowa"
        Case "26": FipsToState = "Michigan"
        Case "27": FipsToState = "Minnesota"
        Case "29": FipsToState = "Missouri"
        Case "38": FipsToState = "North_Dakota"
        Case "39": FipsToState = "Ohio"
        Case "46": FipsToState = "South_Dakota"
        Case "55": FipsToState = "Wisconsin"
        Case Else: FipsToState = "Other"
    End Select
End Function

Private Function StateOptionIndex(ByVal st As String) As Integer
    Select Case st
        Case "Illinois": StateOptionIndex = 1
        Case "Indiana": StateOptionIndex = 2
        Case "Iowa": StateOptionIndex = 3
        Case "Michigan": StateOptionIndex = 4
        Case "Minnesota": StateOptionIndex = 5
        Case "Missouri": StateOptionIndex = 6
        Case "North_Dakota": StateOptionIndex = 7
        Case "Ohio": StateOptionIndex = 8
        Case "South_Dakota": StateOptionIndex = 9
        Case "Wisconsin": StateOptionIndex = 10
        Case Else: StateOptionIndex = 11
    End Select
End Function

' Census county containing a point: returns county name, sets state FIPS
Private Function CensusCountyAt(ByVal lat As Double, ByVal lon As Double, ByRef fips As String) As String
    Dim url As String, txt As String, p As Long, q As Long, seg As String
    url = TIGER_COUNTIES & "/" & TigerCountyLayerId() & "/query?geometry=" & NumStr(lon, 6) & "," & NumStr(lat, 6) & _
          "&geometryType=esriGeometryPoint&inSR=4326&spatialRel=esriSpatialRelIntersects" & _
          "&outFields=" & UrlEncode("NAME,BASENAME,STATE") & "&returnGeometry=false&f=json"
    txt = HttpGetText(url)
    p = InStr(txt, """attributes""")
    If p = 0 Then Exit Function
    q = InStr(p, txt, "}")
    seg = Mid$(txt, p, q - p + 1)
    fips = JsonFieldText(seg, "STATE")
    CensusCountyAt = JsonFieldText(seg, "NAME")
End Function

Private Sub ApplyMapLocation(ByVal lat As Double, ByVal lon As Double)
    Dim ws As Worksheet, fips As String, countyName As String, st As String, cb As Object, i As Long
    Dim soilName As String, soilDesc As String, found As Boolean, report As String, idx As Integer

    Set ws = Sheets("New Flow File")
    gLocating = True
    Application.Cursor = xlWait

    ' ---- state and county (US Census)
    Application.StatusBar = "Finding the state and county at " & NumStr(lat, 5) & ", " & NumStr(lon, 5) & " ..."
    DoEvents
    countyName = CensusCountyAt(lat, lon, fips)
    If countyName = "" Then Err.Raise vbObjectError + 600, "Locate from Map", "The point is not inside a US county."
    st = FipsToState(fips)
    idx = StateOptionIndex(st)
    If ws.OLEObjects("OptionButton" & idx).Object.Value <> True Then
        ws.OLEObjects("OptionButton" & idx).Object.Value = True      ' runs InputState
    End If
    Set cb = ws.OLEObjects("ComboBox1").Object
    found = False
    For i = 0 To cb.ListCount - 1
        If NormCounty(cb.List(i)) = NormCounty(countyName) Then
            cb.ListIndex = i
            found = True
            Exit For
        End If
    Next i
    If Not found Then Sheets("DMData").Range("CountyName").Value = Replace(countyName, " County", "")
    report = "State: " & Replace(st, "_", " ") & vbLf & "County: " & countyName & IIf(found, "", " (not in this state's county list)")

    ' ---- the point itself
    Application.EnableEvents = False
    ws.Range("SiteLat").Value = Round(lat, 5)
    ws.Range("SiteLon").Value = Round(lon, 5)
    Application.EnableEvents = True
    report = report & vbLf & "Latitude, longitude: " & NumStr(lat, 5) & ", " & NumStr(lon, 5)

    ' ---- soil at the point (SSURGO)
    Application.StatusBar = "Finding the soil at the point (USDA Soil Data Access) ..."
    DoEvents
    soilName = PointSoilName(lat, lon, soilDesc)
    If soilName = "" Then Err.Raise vbObjectError + 601, "Locate from Map", "No SSURGO soil is mapped at this point."
    Set cb = ws.OLEObjects("ComboBox2").Object
    found = False
    For i = 0 To cb.ListCount - 1
        If LCase$(Trim$(cb.List(i))) = LCase$(soilName) Or _
           LCase$(Split(Trim$(cb.List(i)) & " ", " ")(0)) = LCase$(soilName) Then
            found = True
            Exit For
        End If
    Next i
    If st = "Illinois" And found Then
        ' representative soil from the Illinois soil database
        Application.EnableEvents = False
        ws.Range("SoilSource").Value = "Soil from dropdown"
        Application.EnableEvents = True
        cb.ListIndex = i
        report = report & vbLf & "Soil: " & soilName & " (" & soilDesc & ")" & vbLf & _
            "Illinois soil database file: " & ws.Range("SoilFileName").Value
    ElseIf st = "Illinois" Then
        ' not in the Illinois soil list: properties from SSURGO at the point
        Application.EnableEvents = False
        ws.Range("SoilSource").Value = "SSURGO at site"
        Application.EnableEvents = True
        report = report & vbLf & "Soil: " & soilName & " (" & soilDesc & ")" & vbLf & _
            soilName & " is not in the Illinois soil list, so its properties come from SSURGO at the point."
    Else
        Application.EnableEvents = False
        ws.Range("SoilSource").Value = "SSURGO at site"
        Application.EnableEvents = True
        If found Then cb.ListIndex = i
        report = report & vbLf & "Soil: " & soilName & " (" & soilDesc & "), from SSURGO at the point"
    End If
    gLocating = False
    RefreshSoilPreview

    Application.Cursor = xlDefault
    Application.StatusBar = False
    MsgBox "Location set from the map:" & vbLf & vbLf & report, vbInformation, "Locate from Map"
    Exit Sub
End Sub
