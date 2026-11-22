Attribute VB_Name = "modManning"
'==========================================================================
' Manning Equation - converted from the VB6 project prjManning (frmManning)
'
'   V = (k / n) * R^(2/3) * S^(1/2)        Q = V * A
'
'   k = unit constant (1.49 in the original program, ft-s units)
'   Channel shapes : 0 Rectangular, 1 Triangular, 2 Trapezoidal,
'                    3 Circular, 4 Parabolic, 5 Irregular
'   Calculations   : 0 Velocity and Discharge
'                    1 Depth given Velocity       2 Depth given Discharge
'                    3 Slope given Velocity       4 Slope given Discharge
'                    5 Manning's n given Velocity 6 Manning's n given Discharge
'==========================================================================
Option Explicit

Private Const PI As Double = 3.14159265358979

' current section geometry (equivalent of GetGeometry in frmManning)
Private mShape As Integer
Private mB As Double        ' width / bottom width / diameter / top width
Private mZ As Double        ' side slope z (z horizontal : 1 vertical)
Private mY0 As Double       ' parabolic: depth at which top width is given
Private mNP As Integer      ' irregular: number of points
Private mPX() As Double     ' irregular: distance
Private mPY() As Double     ' irregular: elevation
Private mMinE As Double     ' irregular: lowest elevation
Private mK As Double        ' unit constant

Private Function Sh() As Worksheet
    Set Sh = ThisWorkbook.Worksheets("Manning")
End Function

'--------------------------------------------------------------------------
' Calculate button / automatic recalculation
'--------------------------------------------------------------------------
Public Sub CalculateManning()
    Dim ws As Worksheet, msg As String, oldEvents As Boolean
    Dim kshape As Integer, kcalc As Integer
    Dim Depth As Double, Slope As Double, N As Double, Velocity As Double, Discharge As Double
    Dim Area As Double, WP As Double, HRadius As Double, maxD As Double

    Set ws = Sh()
    oldEvents = Application.EnableEvents
    Application.EnableEvents = False
    On Error GoTo Crash

    ClearResults ws
    UpdateShapePicture

    kshape = GetIndex(ws.Range("ShapeSel").Value, ws.Range("ShapeList"))
    kcalc = GetIndex(ws.Range("CalcSel").Value, ws.Range("CalcList"))
    If kshape < 0 Then msg = "Select a channel shape.": GoTo Fail
    If kcalc < 0 Then msg = "Select the required calculation.": GoTo Fail

    If Not ReadPositive(ws.Range("Kconst").Value, mK) Then msg = "Unit constant k must be a positive number.": GoTo Fail

    msg = LoadGeometry(ws, kshape, kcalc)
    If msg <> "" Then GoTo Fail

    ' ---- read the hydraulic inputs required by this calculation ----
    If NeedsDepth(kshape, kcalc) And mShape <> 4 Then
        If Not ReadPositive(ws.Range("Depth").Value, Depth) Then msg = "Enter a positive Depth.": GoTo Fail
    End If
    If kcalc = 0 Or kcalc = 1 Or kcalc = 2 Or kcalc = 5 Or kcalc = 6 Then
        If Not ReadPositive(ws.Range("SlopeIn").Value, Slope) Then msg = "Enter a positive Channel Slope (%).": GoTo Fail
        Slope = Slope / 100
    End If
    If kcalc <= 4 Then
        If Not ReadPositive(ws.Range("NIn").Value, N) Then msg = "Enter a positive Manning's n.": GoTo Fail
    End If
    If kcalc = 1 Or kcalc = 3 Or kcalc = 5 Then
        If Not ReadPositive(ws.Range("VIn").Value, Velocity) Then msg = "Enter a positive Velocity.": GoTo Fail
    End If
    If kcalc = 2 Or kcalc = 4 Or kcalc = 6 Then
        If Not ReadPositive(ws.Range("QIn").Value, Discharge) Then msg = "Enter a positive Discharge.": GoTo Fail
    End If

    ' parabolic: the entered depth is the depth at which the top width is given
    If mShape = 4 And kcalc <> 1 And kcalc <> 2 Then Depth = mY0

    ' check the depth fits inside a closed / bounded section
    If kcalc <> 1 And kcalc <> 2 Then
        maxD = MaxDepth()
        If maxD > 0 And Depth > maxD + 0.0000001 Then
            msg = "Depth exceeds the section (maximum " & Format(maxD, "0.000") & " ft).": GoTo Fail
        End If
    End If

    Select Case kcalc
    Case 0  ' Velocity and Discharge
        SectionProps Depth, Area, WP
        HRadius = SafeDiv(Area, WP)
        Velocity = mK / N * HRadius ^ (2 / 3) * Sqr(Slope)
        Discharge = Velocity * Area
    Case 1, 2  ' Depth given Velocity / Discharge
        If kcalc = 1 Then
            msg = SolveDepth(Velocity, False, Slope, N, Depth)
        Else
            msg = SolveDepth(Discharge, True, Slope, N, Depth)
        End If
        If msg <> "" Then GoTo Fail
        SectionProps Depth, Area, WP
        HRadius = SafeDiv(Area, WP)
        If kcalc = 1 Then Discharge = Velocity * Area Else Velocity = Discharge / Area
    Case 3, 4  ' Slope given Velocity / Discharge
        SectionProps Depth, Area, WP
        HRadius = SafeDiv(Area, WP)
        If kcalc = 3 Then Discharge = Velocity * Area Else Velocity = Discharge / Area
        Slope = (Velocity * N / (mK * HRadius ^ (2 / 3))) ^ 2
    Case 5, 6  ' Manning's n given Velocity / Discharge
        SectionProps Depth, Area, WP
        HRadius = SafeDiv(Area, WP)
        If kcalc = 5 Then Discharge = Velocity * Area Else Velocity = Discharge / Area
        N = mK / Velocity * HRadius ^ (2 / 3) * Sqr(Slope)
    End Select

    ws.Range("ResDepth").Value = Depth
    ws.Range("ResArea").Value = Area
    ws.Range("ResWP").Value = WP
    ws.Range("ResR").Value = HRadius
    ws.Range("ResSlope").Value = Slope * 100
    ws.Range("ResN").Value = N
    ws.Range("ResV").Value = Velocity
    ws.Range("ResQ").Value = Discharge
    ws.Range("Status").Value = "Solved: " & ws.Range("CalcSel").Value & " (" & ws.Range("ShapeSel").Value & " channel)"
    ws.Range("Status").Font.Color = RGB(0, 97, 0)
    GoTo Done

Fail:
    ws.Range("Status").Value = "Check input: " & msg
    ws.Range("Status").Font.Color = RGB(192, 0, 0)
    GoTo Done
Crash:
    ws.Range("Status").Value = "Please check your input data (" & Err.Description & ")."
    ws.Range("Status").Font.Color = RGB(192, 0, 0)
    Resume Done
Done:
    Application.EnableEvents = oldEvents
End Sub

Private Sub ClearResults(ws As Worksheet)
    ws.Range("ResDepth").ClearContents
    ws.Range("ResArea").ClearContents
    ws.Range("ResWP").ClearContents
    ws.Range("ResR").ClearContents
    ws.Range("ResSlope").ClearContents
    ws.Range("ResN").ClearContents
    ws.Range("ResV").ClearContents
    ws.Range("ResQ").ClearContents
End Sub

' Depth is an input unless the depth itself is being solved for
Private Function NeedsDepth(kshape As Integer, kcalc As Integer) As Boolean
    NeedsDepth = Not (kcalc = 1 Or kcalc = 2)
End Function

'--------------------------------------------------------------------------
' Geometry set-up (GetGeometry)
'--------------------------------------------------------------------------
Private Function LoadGeometry(ws As Worksheet, kshape As Integer, kcalc As Integer) As String
    Dim v As Variant, i As Integer, x As Double, y As Double
    mShape = kshape
    mB = 0: mZ = 0: mY0 = 0: mNP = 0

    Select Case kshape
    Case 0, 3  ' rectangular width / circular diameter
        If Not ReadPositive(ws.Range("ChanWidth").Value, mB) Then
            LoadGeometry = "Enter a positive " & IIf(kshape = 0, "Width.", "Diameter."): Exit Function
        End If
    Case 1  ' triangular
        If Not ReadPositive(ws.Range("SideSlope").Value, mZ) Then LoadGeometry = "Enter a positive Side Slope z.": Exit Function
    Case 2  ' trapezoidal
        If Not ReadNonNeg(ws.Range("ChanWidth").Value, mB) Then LoadGeometry = "Enter a Bottom Width of zero or more.": Exit Function
        If Not ReadNonNeg(ws.Range("SideSlope").Value, mZ) Then LoadGeometry = "Enter a Side Slope z of zero or more.": Exit Function
        If mB = 0 And mZ = 0 Then LoadGeometry = "Bottom Width and Side Slope cannot both be zero.": Exit Function
    Case 4  ' parabolic: top width given at a depth
        If Not ReadPositive(ws.Range("ChanWidth").Value, mB) Then LoadGeometry = "Enter a positive Top Width.": Exit Function
        If Not ReadPositive(ws.Range("Depth").Value, mY0) Then LoadGeometry = "Enter a positive Depth for the parabola.": Exit Function
    Case 5  ' irregular
        ReDim mPX(1 To 7): ReDim mPY(1 To 7)
        For i = 1 To ws.Range("IrrPts").Rows.Count
            If IsNum(ws.Range("IrrPts").Cells(i, 1).Value) And IsNum(ws.Range("IrrPts").Cells(i, 2).Value) Then
                x = CDbl(ws.Range("IrrPts").Cells(i, 1).Value)
                y = CDbl(ws.Range("IrrPts").Cells(i, 2).Value)
                If mNP > 0 Then
                    If x < mPX(mNP) Then LoadGeometry = "Irregular points must be entered left to right (distance increasing).": Exit Function
                End If
                mNP = mNP + 1
                mPX(mNP) = x: mPY(mNP) = y
            End If
        Next i
        If mNP < 3 Then LoadGeometry = "Enter at least 3 points for the irregular section.": Exit Function
        mMinE = mPY(1)
        For i = 2 To mNP
            If mPY(i) < mMinE Then mMinE = mPY(i)
        Next i
        If MaxDepth() <= 0 Then LoadGeometry = "The irregular section must be lower in the middle than at both end points.": Exit Function
    End Select
End Function

' Area and wetted perimeter at flow depth h
Public Sub SectionProps(ByVal h As Double, ByRef Area As Double, ByRef WP As Double)
    Dim theta As Double, a As Double, w As Double, u As Double
    Dim i As Integer, wse As Double, d1 As Double, d2 As Double, dx As Double, L As Double, f As Double
    Area = 0: WP = 0
    If h <= 0 Then Exit Sub
    Select Case mShape
    Case 0  ' rectangular
        Area = mB * h
        WP = mB + 2 * h
    Case 1  ' triangular
        Area = mZ * h * h
        WP = 2 * h * Sqr(1 + mZ * mZ)
    Case 2  ' trapezoidal
        Area = (mB + mZ * h) * h
        WP = mB + 2 * h * Sqr(1 + mZ * mZ)
    Case 3  ' circular (diameter mB)
        If h >= mB Then
            Area = PI * mB * mB / 4
            WP = PI * mB
        Else
            theta = 2 * ArcCos(1 - 2 * h / mB)
            Area = mB * mB / 8 * (theta - Sin(theta))
            WP = mB * theta / 2
        End If
    Case 4  ' parabolic y = a x^2, top width mB at depth mY0
        a = mY0 / ((mB / 2) ^ 2)
        w = Sqr(h / a)
        u = 2 * a * w
        Area = 4 / 3 * w * h
        WP = w * Sqr(1 + u * u) + ASinh(u) / (2 * a)
    Case 5  ' irregular
        wse = mMinE + h
        For i = 1 To mNP - 1
            d1 = wse - mPY(i): d2 = wse - mPY(i + 1)
            dx = Abs(mPX(i + 1) - mPX(i))
            L = Sqr(dx * dx + (mPY(i + 1) - mPY(i)) ^ 2)
            If d1 >= 0 And d2 >= 0 Then
                Area = Area + (d1 + d2) / 2 * dx
                WP = WP + L
            ElseIf d1 > 0 Or d2 > 0 Then
                If d1 > 0 Then f = d1 / (d1 - d2) Else f = d2 / (d2 - d1)
                Area = Area + IIf(d1 > 0, d1, d2) / 2 * f * dx
                WP = WP + f * L
            End If
        Next i
    End Select
End Sub

' Largest depth the section can hold (0 or less = unbounded)
Private Function MaxDepth() As Double
    Select Case mShape
    Case 3: MaxDepth = mB
    Case 5
        If mPY(1) < mPY(mNP) Then MaxDepth = mPY(1) - mMinE Else MaxDepth = mPY(mNP) - mMinE
    Case Else: MaxDepth = -1
    End Select
End Function

Private Function FlowValue(ByVal h As Double, isQ As Boolean, S As Double, N As Double) As Double
    Dim a As Double, P As Double, R As Double
    SectionProps h, a, P
    If P <= 0 Then FlowValue = 0: Exit Function
    R = a / P
    FlowValue = mK / N * R ^ (2 / 3) * Sqr(S)
    If isQ Then FlowValue = FlowValue * a
End Function

' Normal depth for a target velocity or discharge (bracket + bisection)
Private Function SolveDepth(target As Double, isQ As Boolean, S As Double, N As Double, ByRef h As Double) As String
    Dim lo As Double, hi As Double, hm As Double, hmax As Double
    Dim i As Integer, steps As Integer, fmax As Double, hAtMax As Double, f As Double
    Dim what As String
    what = IIf(isQ, "discharge", "velocity")
    hmax = MaxDepth()
    lo = 0
    If hmax <= 0 Then
        ' open shapes: V and Q increase with depth - expand until bracketed
        hi = 1
        For i = 1 To 80
            If FlowValue(hi, isQ, S, N) >= target Then Exit For
            lo = hi: hi = hi * 2
        Next i
        If FlowValue(hi, isQ, S, N) < target Then
            SolveDepth = "The " & what & " entered cannot be reached in this channel at the given slope and n."
            Exit Function
        End If
    Else
        ' bounded shapes: scan for the first depth that reaches the target
        steps = 1000
        fmax = 0
        hi = 0
        For i = 1 To steps
            hm = hmax * i / steps
            f = FlowValue(hm, isQ, S, N)
            If f > fmax Then fmax = f: hAtMax = hm
            If f >= target Then hi = hm: lo = hmax * (i - 1) / steps: Exit For
        Next i
        If hi = 0 Then
            SolveDepth = "The " & what & " entered exceeds the maximum this section can carry (" & _
                         Format(fmax, "0.000") & " at a depth of " & Format(hAtMax, "0.000") & " ft)."
            Exit Function
        End If
    End If
    For i = 1 To 200
        hm = (lo + hi) / 2
        If FlowValue(hm, isQ, S, N) < target Then lo = hm Else hi = hm
        If hi - lo < 0.0000000001 Then Exit For
    Next i
    h = (lo + hi) / 2
    SolveDepth = ""
End Function

'--------------------------------------------------------------------------
' Show the picture for the selected channel shape (picShape in frmManning)
'--------------------------------------------------------------------------
Public Sub UpdateShapePicture()
    Dim shp As Shape, nm As String
    nm = CStr(Sh().Range("ShapeSel").Value)
    For Each shp In Sh().Shapes
        If Left$(shp.Name, 8) = "picShape" Then
            If shp.Name = "picShape" & nm Then shp.Visible = msoTrue Else shp.Visible = msoFalse
        End If
    Next shp
End Sub

'--------------------------------------------------------------------------
' Reset button (ManningResetInputs): restore the default values of the original form
'--------------------------------------------------------------------------
Public Sub ManningResetInputs()
    Dim ws As Worksheet, oldEvents As Boolean
    Dim px As Variant, py As Variant, i As Integer
    Set ws = Sh()
    oldEvents = Application.EnableEvents
    Application.EnableEvents = False
    ws.Range("ShapeSel").Value = "Rectangular"
    ws.Range("CalcSel").Value = "Velocity and Discharge"
    ws.Range("ChanWidth").Value = 4
    ws.Range("Depth").Value = 1
    ws.Range("SideSlope").Value = 2
    ws.Range("SlopeIn").Value = 0.1
    ws.Range("NIn").Value = 0.035
    ws.Range("VIn").Value = 2
    ws.Range("QIn").Value = 1
    px = Array(0, 8, 16, 21, 28, 33, 40)
    py = Array(10, 7, 2.5, 2, 4, 7.5, 10)
    For i = 0 To 6
        ws.Range("IrrPts").Cells(i + 1, 1).Value = px(i)
        ws.Range("IrrPts").Cells(i + 1, 2).Value = py(i)
    Next i
    Application.EnableEvents = oldEvents
    CalculateManning
End Sub

'--------------------------------------------------------------------------
' Worksheet functions (usable in any cell)
'   =ManningVelocity("Trapezoidal", Depth, Width, SideSlope, SlopePct, n)
'   =ManningDischarge("Irregular", Depth, , , SlopePct, n, PointsRange)
'   =ChannelArea("Circular", Depth, Diameter)
' For "Parabolic", Width = top width at the given depth.
'--------------------------------------------------------------------------
Public Function ManningVelocity(ChannelShape As Variant, Depth As Double, Optional Width As Double = 0, _
        Optional SideSlope As Double = 0, Optional SlopePct As Double = 0, Optional N As Double = 0.035, _
        Optional Points As Variant, Optional k As Double = 1.49) As Variant
    Dim a As Double, P As Double, msg As String
    msg = SetupForUDF(ChannelShape, Depth, Width, SideSlope, Points, k)
    If msg <> "" Then ManningVelocity = CVErr(xlErrValue): Exit Function
    If N <= 0 Or SlopePct < 0 Then ManningVelocity = CVErr(xlErrNum): Exit Function
    SectionProps Depth, a, P
    If P <= 0 Then ManningVelocity = 0: Exit Function
    ManningVelocity = k / N * (a / P) ^ (2 / 3) * Sqr(SlopePct / 100)
End Function

Public Function ManningDischarge(ChannelShape As Variant, Depth As Double, Optional Width As Double = 0, _
        Optional SideSlope As Double = 0, Optional SlopePct As Double = 0, Optional N As Double = 0.035, _
        Optional Points As Variant, Optional k As Double = 1.49) As Variant
    Dim a As Double, P As Double, v As Variant
    v = ManningVelocity(ChannelShape, Depth, Width, SideSlope, SlopePct, N, Points, k)
    If IsError(v) Then ManningDischarge = v: Exit Function
    SectionProps Depth, a, P
    ManningDischarge = v * a
End Function

Public Function ChannelArea(ChannelShape As Variant, Depth As Double, Optional Width As Double = 0, _
        Optional SideSlope As Double = 0, Optional Points As Variant) As Variant
    Dim a As Double, P As Double
    If SetupForUDF(ChannelShape, Depth, Width, SideSlope, Points, 1.49) <> "" Then ChannelArea = CVErr(xlErrValue): Exit Function
    SectionProps Depth, a, P
    ChannelArea = a
End Function

Private Function SetupForUDF(ChannelShape As Variant, Depth As Double, Width As Double, SideSlope As Double, _
        Points As Variant, k As Double) As String
    Dim i As Integer, nm As String
    Dim names As Variant
    names = Array("RECTANGULAR", "TRIANGULAR", "TRAPEZOIDAL", "CIRCULAR", "PARABOLIC", "IRREGULAR")
    mShape = -1
    If IsNumeric(ChannelShape) Then
        mShape = CInt(ChannelShape)
    Else
        nm = UCase$(Trim$(CStr(ChannelShape)))
        For i = 0 To 5
            If Left$(names(i), Len(nm)) = nm And Len(nm) > 0 Then mShape = i: Exit For
        Next i
    End If
    If mShape < 0 Or mShape > 5 Then SetupForUDF = "shape": Exit Function
    mK = k: mB = Width: mZ = SideSlope: mY0 = Depth: mNP = 0
    If mShape = 5 Then
        If IsMissing(Points) Then SetupForUDF = "points": Exit Function
        ReDim mPX(1 To Points.Rows.Count): ReDim mPY(1 To Points.Rows.Count)
        For i = 1 To Points.Rows.Count
            If IsNum(Points.Cells(i, 1).Value) And IsNum(Points.Cells(i, 2).Value) Then
                mNP = mNP + 1
                mPX(mNP) = CDbl(Points.Cells(i, 1).Value): mPY(mNP) = CDbl(Points.Cells(i, 2).Value)
            End If
        Next i
        If mNP < 3 Then SetupForUDF = "points": Exit Function
        mMinE = mPY(1)
        For i = 2 To mNP
            If mPY(i) < mMinE Then mMinE = mPY(i)
        Next i
    End If
    SetupForUDF = ""
End Function

'--------------------------------------------------------------------------
' helpers
'--------------------------------------------------------------------------
Private Function GetIndex(v As Variant, lst As Range) As Integer
    Dim i As Integer
    GetIndex = -1
    For i = 1 To lst.Cells.Count
        If CStr(lst.Cells(i).Value) = CStr(v) Then GetIndex = i - 1: Exit Function
    Next i
End Function

Private Function IsNum(v As Variant) As Boolean
    If IsEmpty(v) Then Exit Function
    If IsError(v) Then Exit Function
    If VarType(v) = vbString Then
        If Len(Trim$(v)) = 0 Then Exit Function
    End If
    IsNum = IsNumeric(v)
End Function

Private Function ReadPositive(v As Variant, ByRef x As Double) As Boolean
    If Not IsNum(v) Then Exit Function
    x = CDbl(v)
    ReadPositive = (x > 0)
End Function

Private Function ReadNonNeg(v As Variant, ByRef x As Double) As Boolean
    If Not IsNum(v) Then Exit Function
    x = CDbl(v)
    ReadNonNeg = (x >= 0)
End Function

Private Function SafeDiv(a As Double, b As Double) As Double
    If b <> 0 Then SafeDiv = a / b
End Function

Private Function ArcCos(ByVal x As Double) As Double
    If x >= 1 Then
        ArcCos = 0
    ElseIf x <= -1 Then
        ArcCos = PI
    Else
        ArcCos = Atn(-x / Sqr(-x * x + 1)) + 2 * Atn(1)
    End If
End Function

Private Function ASinh(ByVal x As Double) As Double
    ASinh = Log(x + Sqr(x * x + 1))
End Function
