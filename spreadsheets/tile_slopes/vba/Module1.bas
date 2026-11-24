Attribute VB_Name = "Module1"
'=====================================================================
' Burying methods of Illini Subsurface Drain Layout, for one profile
' (Richard Cooke and Anamelechi Falasy).
'
' BuryTile(...) is entered as one array formula over the tile column and
' the columns beside it (Columns: how many). For every row of Dist it returns:
'   column 1        the tile elevation (before the offset)
'   column 2        the tile depth below the ground (with the offset),
'                   when the range has 3 or more columns
'   last column     notes: "Grade break" where the grade changes, and
'                   "Check: ..." where the tile is outside a limit
' Methods (first letters are enough):
'   Routine J            the target depth row by row, the grade held
'                        between the minimum and maximum slope
'   Minimum cover        as shallow as the absolute upper depth and the
'                        minimum slope allow, buried from the top down
'   Fewest grade breaks  the fewest straight runs that keep within the
'                        absolute depths and the slopes; of those, the
'                        one closest to the target depth
'   Least cost           grade breaks (each costing BreakCost ft of tile
'                        1 ft off the target) balanced against distance
'                        from the target depth (and excavation, if given)
' Distances in ft from the outlet (or the main), rising upstream; slopes
' in percent. StartElev: the tile elevation at the first row (the main at
' a lateral, or an outlet invert); blank for a free start.
' The constant-slope section (ConstOn = 1) is held in every method.
'=====================================================================

Private Const BIG As Double = 1E+30
Private Const EPSG As Double = 0.0000001
Private Const MAXC As Long = 80          ' most break candidates along a piece
Private Const MAXK As Long = 60          ' most tile levels per candidate
Private Const DZ0 As Double = 0.0656     ' ft between tile levels (0.02 m)

Private mN As Long
Private mS() As Double, mZ() As Double, mMid() As Double, mT() As Double
Private mLo() As Double, mHi() As Double
Private mGmin As Double, mGmax As Double, mMinRun As Double
Private mMaxCh As Double, mBC As Double, mExc As Double

Function Elevation(x As Single, Table As Range, _
   YCol As Integer)
    
    'Returns the interpolated number from the first column of
   'Table and the YCol column of table.
   'x - the number between two numbers in the first column of Table
   'Table - The range of values with at least YCol columns
   'YCol - the column of Table used for the Y-side of the interpolation
   
    
   Dim TableRow As Long, Temp As Variant
   Dim x0 As Double, x1 As Double, y0 As Double, y1 As Double
    
    
    'TableRow - the row of Table that is less than or equal to x
   'x0, x1, y0, y1 - the coordinates to interpolate
   
   
    'skip the error when match returns N/A
   On Error Resume Next
    
    'Find the row in Table that's less than or equal to x
   Temp = Application.WorksheetFunction.Match(x, Table.Resize(, 1), 1)
    
    'If Match returns an error, return the error
   If IsError(Temp) Then
        FlowParameters = CVErr(Temp)
    Else

       TableRow = Temp
        
        'The first coordinates are the from the row just less than x
       x0 = Table(TableRow, 1)
        y0 = Table(TableRow, YCol)
        
        'If x is the same as the row just less than (or equal to) x,
       'return the value from YCol
       If x = x0 Then
            Elevation = y0
        Else
            'Get the second coordinates from the next row
           x1 = Table(TableRow + 1, 1)
            y1 = Table(TableRow + 1, YCol)
            'Do the math
           Elevation = (x - x0) / (x1 - x0) * (y1 - y0) + y0
        End If
    End If
End Function

Function TileDepth(L1, L2, Upper, Lower, minSlope, maxSlope, AbsU, AbsL, Surface, check1, overide, cstart, cend, D0)

 

If L2 = L1 Then
  TD = D0
 GoTo 100
End If

MD = (Upper + Lower) / 2


asl = (MD - D0) / (L2 - L1)


 
           If check1 = 1 And L2 > cstart And L2 < cend Then
             TD = D0 + (L2 - L1) * overide / 100
             GoTo 100
          End If
 
 Select Case asl
    Case Is < minSlope
        TD = D0 + (L2 - L1) * minSlope
        If TD < Surface - AbsL Then TD = Surface - AbsL
        
    Case Is > maxSlope
        TD = D0 + (L2 - L1) * maxSlope
        If TD > Surface - AbsU Then TD = Surface - AbsU
        
    Case Else
         TD = MD
        
      
 End Select
 

 
100 TileDepth = TD

End Function

Private Function ToVal(v As Variant) As Variant
    ' a Range gives its value (its default property)
    ToVal = v
End Function

Private Function IsNum(v As Variant) As Boolean
    Select Case VarType(v)
        Case 2, 3, 4, 5, 6, 14, 20
            IsNum = True
        Case Else
            IsNum = False
    End Select
End Function

Private Function NumOr(v As Variant, ByVal dflt As Double) As Double
    Dim x As Variant
    If IsMissing(v) Then
        NumOr = dflt
        Exit Function
    End If
    x = ToVal(v)
    If IsArray(x) Then
        x = x(LBound(x, 1), LBound(x, 2))
    End If
    If IsNum(x) Then
        NumOr = CDbl(x)
    ElseIf VarType(x) = 11 Then
        If x Then NumOr = 1 Else NumOr = 0
    ElseIf VarType(x) = 8 Then
        If IsNumeric(x) And Trim(x) <> "" Then NumOr = CDbl(x) Else NumOr = dflt
    Else
        NumOr = dflt
    End If
End Function

Private Function As2D(r As Variant) As Variant
    Dim v As Variant, a() As Variant
    v = ToVal(r)
    If IsArray(v) Then
        As2D = v
    Else
        ReDim a(1 To 1, 1 To 1)
        a(1, 1) = v
        As2D = a
    End If
End Function

Private Sub AddNote(nt() As String, ByVal i As Long, ByVal txt As String)
    If nt(i) = "" Then
        nt(i) = txt
    Else
        nt(i) = nt(i) & "; " & txt
    End If
End Sub

Public Function BuryTile(Dist, Surface, Method, UpperDepth, LowerDepth, AbsUpper, AbsLower, _
        MinSlope, MaxSlope, StartElev, ConstOn, ConstSlope, ConstStart, ConstEnd, _
        Optional Offset, Optional MinRun, Optional MaxChange, Optional BreakCost, _
        Optional Excavation, Optional Columns) As Variant
    Dim dA As Variant, zA As Variant, res() As Variant
    Dim nRows As Long, nCols As Long, n As Long, i As Long, k As Long
    Dim r0 As Long, c0 As Long, rz As Long, cz As Long
    Dim up As Double, lw As Double, au As Double, al As Double, ov As Double
    Dim cs As Double, ce As Double, off As Double, mid As Double, d As Double
    Dim hasStart As Boolean, st As Double, sv As Variant, ck As Boolean
    Dim m As String, head As String, g As Double, g2 As Double
    Dim nt() As String, cn() As Boolean

    dA = As2D(Dist)
    zA = As2D(Surface)
    r0 = LBound(dA, 1): c0 = LBound(dA, 2)
    rz = LBound(zA, 1): cz = LBound(zA, 2)
    nRows = UBound(dA, 1) - r0 + 1
    If UBound(zA, 1) - rz + 1 < nRows Then nRows = UBound(zA, 1) - rz + 1
    nCols = CLng(NumOr(Columns, 1))
    If nCols < 1 Then nCols = 1
    ReDim res(1 To nRows, 1 To nCols)
    For i = 1 To nRows
        For k = 1 To nCols
            res(i, k) = ""
        Next k
    Next i
    n = 0
    Do While n < nRows
        If r0 + n > UBound(dA, 1) Or rz + n > UBound(zA, 1) Then Exit Do
        If Not IsNum(dA(r0 + n, c0)) Then Exit Do
        If Not IsNum(zA(rz + n, cz)) Then Exit Do
        n = n + 1
    Loop
    If n = 0 Then
        BuryTile = res
        Exit Function
    End If

    mN = n
    ReDim mS(0 To n - 1): ReDim mZ(0 To n - 1): ReDim mMid(0 To n - 1): ReDim mT(0 To n - 1)
    ReDim mLo(0 To n - 1): ReDim mHi(0 To n - 1)
    ReDim nt(0 To n - 1): ReDim cn(0 To n - 1)
    up = NumOr(UpperDepth, 3.25)
    lw = NumOr(LowerDepth, 3.75)
    au = NumOr(AbsUpper, 3)
    al = NumOr(AbsLower, 6)
    mGmin = NumOr(MinSlope, 0.1) / 100
    mGmax = NumOr(MaxSlope, 5) / 100
    ck = (NumOr(ConstOn, 0) <> 0)
    ov = NumOr(ConstSlope, 0.5) / 100
    cs = NumOr(ConstStart, 0)
    ce = NumOr(ConstEnd, 0)
    off = NumOr(Offset, 0)
    mMinRun = NumOr(MinRun, 20)
    mMaxCh = NumOr(MaxChange, 0) / 100
    mBC = NumOr(BreakCost, 100)
    mExc = NumOr(Excavation, 0)
    If mMinRun < 0 Then mMinRun = 0
    If mBC < 0 Then mBC = 0
    If mExc < 0 Then mExc = 0
    sv = ToVal(StartElev)
    If IsArray(sv) Then sv = sv(LBound(sv, 1), LBound(sv, 2))
    hasStart = IsNum(sv)
    If hasStart Then st = CDbl(sv)
    mid = (up + lw) / 2
    For i = 0 To n - 1
        mS(i) = CDbl(dA(r0 + i, c0))
        mZ(i) = CDbl(zA(rz + i, cz))
        mMid(i) = mZ(i) - mid
        mLo(i) = mZ(i) - al
        mHi(i) = mZ(i) - au
    Next i
    For i = 1 To n - 1
        If mS(i) <= mS(i - 1) Then
            res(1, nCols) = "Check: distances must increase down the table (row " & (i + 1) & ")"
            BuryTile = res
            Exit Function
        End If
        cn(i) = ck And mS(i) > cs And mS(i) < ce
    Next i

    m = LCase(Trim(CStr(NumOrText(Method))))
    Select Case Left(m, 3)
        Case "min"
            head = BuryMinCover(cn, ov, hasStart, st, au)
        Case "few"
            head = BuryPieces(cn, ov, hasStart, st, True)
        Case "lea"
            head = BuryPieces(cn, ov, hasStart, st, False)
        Case Else
            head = BuryRoutineJ(cn, ov, hasStart, st, au, al)
    End Select
    If head <> "" Then AddNote nt, 0, head

    ' grade breaks and checks (on the tile with the offset)
    For i = 0 To n - 1
        d = mZ(i) - (mT(i) + off)
        If d < au - 0.005 Then AddNote nt, i, "Check: shallower than abs upper"
        If d > al + 0.005 Then AddNote nt, i, "Check: deeper than abs lower"
        If i >= 1 Then
            g = (mT(i) - mT(i - 1)) / (mS(i) - mS(i - 1))
            If g <= 0.000001 Then
                AddNote nt, i, "Check: reverse or zero grade below"
            ElseIf Not cn(i) And g < mGmin - 0.000001 Then
                AddNote nt, i, "Check: grade below min"
            ElseIf Not cn(i) And g > mGmax + 0.000001 Then
                AddNote nt, i, "Check: grade above max"
            End If
            If i <= n - 2 Then
                g2 = (mT(i + 1) - mT(i)) / (mS(i + 1) - mS(i))
                If Abs(g2 - g) > 0.00001 Then AddNote nt, i, "Grade break"
            End If
        End If
    Next i
    For i = 0 To n - 1
        res(i + 1, 1) = mT(i)
        If nCols >= 3 Then res(i + 1, 2) = mZ(i) - (mT(i) + off)
        If nCols >= 2 Then res(i + 1, nCols) = nt(i)
    Next i
    BuryTile = res
End Function

Private Function NumOrText(v As Variant) As Variant
    Dim x As Variant
    x = ToVal(v)
    If IsArray(x) Then x = x(LBound(x, 1), LBound(x, 2))
    If IsError(x) Then x = ""
    NumOrText = x
End Function

' ---- Routine J: the target depth row by row --------------------------
Private Function BuryRoutineJ(cn() As Boolean, ByVal ov As Double, ByVal hasStart As Boolean, _
        ByVal st As Double, ByVal au As Double, ByVal al As Double) As String
    Dim k As Long, L As Double, g As Double
    If hasStart Then mT(0) = st Else mT(0) = mMid(0)
    For k = 1 To mN - 1
        L = mS(k) - mS(k - 1)
        If cn(k) Then
            mT(k) = mT(k - 1) + L * ov
        Else
            g = (mMid(k) - mT(k - 1)) / L
            If g < mGmin Then g = mGmin
            If g > mGmax Then g = mGmax
            mT(k) = mT(k - 1) + L * g
            If mT(k) > mZ(k) - au Then
                mT(k) = mZ(k) - au
            ElseIf mT(k) < mZ(k) - al Then
                mT(k) = mZ(k) - al
            End If
        End If
    Next k
    BuryRoutineJ = ""
End Function

' ---- Minimum cover: as shallow as the cover and minimum slope allow ---
Private Function BuryMinCover(cn() As Boolean, ByVal ov As Double, ByVal hasStart As Boolean, _
        ByVal st As Double, ByVal au As Double) As String
    Dim k As Long, L As Double, t As Double
    mT(mN - 1) = mZ(mN - 1) - au
    For k = mN - 2 To 0 Step -1
        L = mS(k + 1) - mS(k)
        If cn(k + 1) Then
            mT(k) = mT(k + 1) - ov * L
        Else
            t = mT(k + 1) - mGmin * L
            If mZ(k) - au < t Then t = mZ(k) - au
            mT(k) = t
        End If
    Next k
    BuryMinCover = ""
    If hasStart Then
        If mT(0) > st + 0.000000001 Then
            mT(0) = st
            BuryMinCover = "First section dropped to the start elevation"
        ElseIf mT(0) < st - 0.005 Then
            BuryMinCover = "Check: tile ends below the start elevation"
        End If
    End If
End Function

' ---- Fewest grade breaks / least cost ---------------------------------
' The profile is buried in pieces between the constant-slope section(s):
' free pieces by the search, constant pieces at the constant slope.
Private Function BuryPieces(cn() As Boolean, ByVal ov As Double, ByVal hasStart As Boolean, _
        ByVal st As Double, ByVal fewest As Boolean) As String
    Dim a As Long, b As Long, k As Long, fixedStart As Boolean, t0 As Double
    Dim note As String, allNotes As String
    a = 0
    fixedStart = hasStart
    t0 = st
    If mN = 1 Then
        If hasStart Then mT(0) = st Else mT(0) = mMid(0)
        BuryPieces = ""
        Exit Function
    End If
    Do While a < mN - 1
        If cn(a + 1) Then
            b = a + 1
            Do While b + 1 <= mN - 1
                If Not cn(b + 1) Then Exit Do
                b = b + 1
            Loop
            If fixedStart Then mT(a) = t0 Else mT(a) = mMid(a)
            For k = a + 1 To b
                mT(k) = mT(k - 1) + ov * (mS(k) - mS(k - 1))
            Next k
        Else
            b = a + 1
            Do While b + 1 <= mN - 1
                If cn(b + 1) Then Exit Do
                b = b + 1
            Loop
            note = SolveFree(a, b, fixedStart, t0, fewest)
            If note <> "" Then
                If InStr(allNotes, note) = 0 Then
                    If allNotes = "" Then allNotes = note Else allNotes = allNotes & "; " & note
                End If
            End If
        End If
        fixedStart = True
        t0 = mT(b)
        a = b
    Loop
    BuryPieces = allNotes
End Function

' one free piece, easing the limits a step at a time when nothing fits
Private Function SolveFree(ByVal a As Long, ByVal b As Long, ByVal fixedStart As Boolean, _
        ByVal t0 As Double, ByVal fewest As Boolean) As String
    Dim runs As Long, i As Long, note As String, j As Long
    Dim lo2() As Double, hi2() As Double, deep As Variant, shal As Variant
    Dim gl As Double, tt As Double, want As Double, ds As Double
    ReDim lo2(0 To mN - 1): ReDim hi2(0 To mN - 1)
    runs = -1
    If mMaxCh > 0 Then runs = SolvePiece(a, b, fixedStart, t0, fewest, mLo, mHi, mGmin, True)
    If runs < 0 Then
        runs = SolvePiece(a, b, fixedStart, t0, fewest, mLo, mHi, mGmin, False)
        If runs >= 0 And mMaxCh > 0 Then note = "Grade-change limit eased"
    End If
    If runs < 0 Then
        deep = Array(1#, 3.3, 3.3)
        shal = Array(0#, 0#, 1#)
        For j = 0 To 2
            For i = 0 To mN - 1
                lo2(i) = mLo(i) - deep(j)
                hi2(i) = mHi(i) + shal(j)
                If hi2(i) > mZ(i) Then hi2(i) = mZ(i)
            Next i
            runs = SolvePiece(a, b, fixedStart, t0, fewest, lo2, hi2, mGmin, False)
            If runs >= 0 Then Exit For
        Next j
        If runs >= 0 Then note = "Check: no profile within the abs depths; depth limits eased"
    End If
    If runs < 0 Then
        For i = 0 To mN - 1
            hi2(i) = mHi(i) + 1
            If hi2(i) > mZ(i) Then hi2(i) = mZ(i)
        Next i
        For j = 0 To 1
            If j = 0 Then gl = 0 Else gl = -mGmax
            runs = SolvePiece(a, b, fixedStart, t0, fewest, mLo, hi2, gl, False)
            If runs >= 0 Then Exit For
        Next j
        If runs >= 0 Then note = "Check: no profile within the depth and slope limits"
    End If
    If runs < 0 Then
        ' follow the target depth up the piece, within the slope limits
        If fixedStart Then
            mT(a) = t0
        Else
            mT(a) = mMid(a)
            If mT(a) < mLo(a) Then mT(a) = mLo(a)
            If mT(a) > mHi(a) Then mT(a) = mHi(a)
        End If
        For i = a + 1 To b
            ds = mS(i) - mS(i - 1)
            want = mMid(i)
            If mHi(i) < want Then want = mHi(i)
            tt = want
            If tt < mT(i - 1) + mGmin * ds Then tt = mT(i - 1) + mGmin * ds
            If tt > mT(i - 1) + mGmax * ds Then tt = mT(i - 1) + mGmax * ds
            If tt > mHi(i) Then tt = mHi(i)
            mT(i) = tt
        Next i
        note = "Check: no profile within the depth and slope limits; tile follows the target depth"
    End If
    SolveFree = note
End Function

Private Function GridN(ByVal x0 As Double, ByVal x1 As Double, ByVal dz As Double) As Long
    Dim k As Long
    If x1 < x0 Then
        GridN = 1
        Exit Function
    End If
    k = Int((x1 - x0) / dz + 0.000000001) + 1
    If (x1 - x0) - (k - 1) * dz > 0.25 * dz Then k = k + 1
    GridN = k
End Function

Private Function GridAt(ByVal x0 As Double, ByVal x1 As Double, ByVal dz As Double, _
        ByVal j As Long) As Double
    Dim k As Long
    If x1 < x0 Then
        GridAt = 0.5 * (x0 + x1)
        Exit Function
    End If
    k = Int((x1 - x0) / dz + 0.000000001) + 1
    If j < k Then GridAt = x0 + dz * j Else GridAt = x1
End Function

' The best chain of straight runs from vertex a (the start) up to vertex b,
' every vertex between lo and hi and every rc rising upstream at a grade
' between gLow and the maximum slope (with the grade-change limit when
' useLimit). Breaks at candidate vertices, tile on a grid of levels; the
' search over those states is exact. Writes mT(a..b); returns the number of
' runs, or -1 when no profile fits.
Private Function SolvePiece(ByVal a As Long, ByVal b As Long, ByVal fixedStart As Boolean, _
        ByVal t0 As Double, ByVal fewest As Boolean, lo() As Double, hi() As Double, _
        ByVal gLow As Double, ByVal useLimit As Boolean) As Long
    Dim m As Long, v As Long, gv As Long, nC As Long, c As Long, k As Long
    Dim w() As Double, ci() As Long, cv() As Long, KC() As Long, LEV() As Double
    Dim PRI() As Double, SEC() As Double, GIN() As Double, HG() As Boolean
    Dim PC() As Long, PK() As Long, PB() As Long, act() As Boolean
    Dim dd As Double, total As Double, stepC As Double, nxt As Double, band As Double
    Dim dz As Double, Kmax As Long, NBins As Long, width As Double, x As Double
    Dim cb As Long, kb As Long, bb As Long, cj As Long, kj As Long, nBin As Long
    Dim vb As Long, tb As Double, Glo As Double, Ghi As Double, d As Double
    Dim Sw As Double, Sm As Double, Sm2 As Double, Sd As Double, Sd2 As Double
    Dim Smd As Double, Szw As Double, wi As Double, mi As Double
    Dim tj As Double, g As Double, rc As Double, p As Double, sc As Double
    Dim topP As Double, topS As Double, anyAct As Boolean, addP As Double
    Dim bk As Long, bbb As Long, guard As Long, nbk As Long, c2 As Long, k2 As Long, b2 As Long
    Dim brkV() As Long, brkT() As Double, q As Long, f As Double

    m = b - a
    ReDim w(0 To m)
    For v = 0 To m - 1
        dd = mS(a + v + 1) - mS(a + v)
        w(v) = w(v) + 0.5 * dd
        w(v + 1) = w(v + 1) + 0.5 * dd
    Next v
    For v = 0 To m
        If w(v) < 0.000000001 Then w(v) = 0.000000001
    Next v

    ' candidate break vertices
    total = mS(b) - mS(a)
    stepC = total / MAXC
    ReDim ci(0 To m): ReDim cv(0 To m)
    nC = 0
    nxt = 0
    For v = 0 To m
        ci(v) = -1
        If v = 0 Or v = m Or mS(a + v) - mS(a) >= nxt - 0.000000001 Then
            ci(v) = nC
            cv(nC) = v
            nC = nC + 1
            nxt = mS(a + v) - mS(a) + stepC
        End If
    Next v

    ' tile levels at each candidate
    band = 0
    For v = 0 To m
        If hi(a + v) - lo(a + v) > band Then band = hi(a + v) - lo(a + v)
    Next v
    dz = DZ0
    If band / (MAXK - 1) > dz Then dz = band / (MAXK - 1)
    ReDim KC(0 To nC - 1)
    Kmax = 1
    For c = 0 To nC - 1
        If c = 0 And fixedStart Then
            KC(c) = 1
        Else
            KC(c) = GridN(lo(a + cv(c)), hi(a + cv(c)), dz)
        End If
        If KC(c) > Kmax Then Kmax = KC(c)
    Next c
    ReDim LEV(0 To nC - 1, 0 To Kmax - 1)
    For c = 0 To nC - 1
        For k = 0 To KC(c) - 1
            If c = 0 And fixedStart Then
                LEV(c, k) = t0
            Else
                LEV(c, k) = GridAt(lo(a + cv(c)), hi(a + cv(c)), dz, k)
            End If
        Next k
    Next c

    ' grade bins for the grade-change limit
    NBins = 1
    width = 1
    If useLimit Then
        width = mMaxCh
        x = (mGmax - gLow) / width - 0.000000001
        NBins = -Int(-x) + 1
        If NBins < 1 Then NBins = 1
    End If
    ReDim PRI(0 To nC - 1, 0 To Kmax - 1, 0 To NBins - 1)
    ReDim SEC(0 To nC - 1, 0 To Kmax - 1, 0 To NBins - 1)
    ReDim GIN(0 To nC - 1, 0 To Kmax - 1, 0 To NBins - 1)
    ReDim HG(0 To nC - 1, 0 To Kmax - 1, 0 To NBins - 1)
    ReDim PC(0 To nC - 1, 0 To Kmax - 1, 0 To NBins - 1)
    ReDim PK(0 To nC - 1, 0 To Kmax - 1, 0 To NBins - 1)
    ReDim PB(0 To nC - 1, 0 To Kmax - 1, 0 To NBins - 1)
    ReDim act(0 To NBins - 1)
    For c = 0 To nC - 1
        For k = 0 To Kmax - 1
            For bb = 0 To NBins - 1
                PRI(c, k, bb) = BIG
                SEC(c, k, bb) = BIG
                PC(c, k, bb) = -1
            Next bb
        Next k
    Next c
    For k = 0 To KC(0) - 1
        PRI(0, k, 0) = 0
        If fixedStart Then
            SEC(0, k, 0) = 0
        Else
            tb = LEV(0, k)
            SEC(0, k, 0) = w(0) * (tb - mMid(a)) ^ 2 + mExc * w(0) * (mZ(a) - tb)
        End If
    Next k
    If fewest Then addP = 1 Else addP = 0

    For cb = 0 To nC - 2
        ' the best profile already reaching the top end (to prune)
        topP = BIG
        topS = BIG
        For k = 0 To KC(nC - 1) - 1
            For bb = 0 To NBins - 1
                If PRI(nC - 1, k, bb) < topP Then topP = PRI(nC - 1, k, bb)
                If SEC(nC - 1, k, bb) < topS Then topS = SEC(nC - 1, k, bb)
            Next bb
        Next k
        vb = cv(cb)
        For kb = 0 To KC(cb) - 1
            anyAct = False
            For bb = 0 To NBins - 1
                act(bb) = PRI(cb, kb, bb) < BIG / 2
                If act(bb) And topP < BIG / 2 Then
                    If fewest Then
                        If PRI(cb, kb, bb) + 1 > topP + 0.000000001 Then act(bb) = False
                    Else
                        If SEC(cb, kb, bb) + mBC >= topS - 0.000000000001 Then act(bb) = False
                    End If
                End If
                If act(bb) Then anyAct = True
            Next bb
            If anyAct Then
                tb = LEV(cb, kb)
                Glo = gLow
                Ghi = mGmax
                Sw = 0: Sm = 0: Sm2 = 0: Sd = 0: Sd2 = 0: Smd = 0: Szw = 0
                For v = vb + 1 To m
                    gv = a + v
                    d = mS(gv) - mS(a + vb)
                    x = (lo(gv) - tb) / d
                    If x > Glo Then Glo = x
                    x = (hi(gv) - tb) / d
                    If x < Ghi Then Ghi = x
                    If Glo > Ghi + EPSG Then Exit For
                    wi = w(v)
                    mi = mMid(gv)
                    Sw = Sw + wi
                    Sm = Sm + wi * mi
                    Sm2 = Sm2 + wi * mi * mi
                    Sd = Sd + wi * d
                    Sd2 = Sd2 + wi * d * d
                    Smd = Smd + wi * mi * d
                    Szw = Szw + wi * mZ(gv)
                    cj = ci(v)
                    If cj >= 0 Then
                        If d >= mMinRun - 0.000000001 Or v = m Then
                            For kj = 0 To KC(cj) - 1
                                tj = LEV(cj, kj)
                                g = (tj - tb) / d
                                If g >= Glo - EPSG And g <= Ghi + EPSG Then
                                    rc = Sw * tb * tb + Sm2 + g * g * Sd2 - 2 * tb * Sm _
                                          + 2 * g * tb * Sd - 2 * g * Smd
                                    If mExc <> 0 Then rc = rc + mExc * (Szw - tb * Sw - g * Sd)
                                    If Not fewest Then rc = rc + mBC
                                    nBin = 0
                                    If useLimit Then
                                        nBin = Int((g - gLow) / width + 0.000000001)
                                        If nBin < 0 Then nBin = 0
                                        If nBin > NBins - 1 Then nBin = NBins - 1
                                    End If
                                    For bb = 0 To NBins - 1
                                        If act(bb) Then
                                            If useLimit And HG(cb, kb, bb) Then
                                                If Abs(g - GIN(cb, kb, bb)) > mMaxCh + EPSG Then GoTo NextBin
                                            End If
                                            p = PRI(cb, kb, bb) + addP
                                            sc = SEC(cb, kb, bb) + rc
                                            If p < PRI(cj, kj, nBin) - 0.000000001 Or _
                                               (Abs(p - PRI(cj, kj, nBin)) <= 0.000000001 And _
                                                sc < SEC(cj, kj, nBin) - 0.000000000001) Then
                                                PRI(cj, kj, nBin) = p
                                                SEC(cj, kj, nBin) = sc
                                                GIN(cj, kj, nBin) = g
                                                HG(cj, kj, nBin) = True
                                                PC(cj, kj, nBin) = cb
                                                PK(cj, kj, nBin) = kb
                                                PB(cj, kj, nBin) = bb
                                            End If
                                        End If
NextBin:
                                    Next bb
                                End If
                            Next kj
                        End If
                    End If
                Next v
            End If
        Next kb
    Next cb

    ' the best profile at the top end
    topP = BIG: topS = BIG: bk = -1: bbb = -1
    c = nC - 1
    For k = 0 To KC(c) - 1
        For bb = 0 To NBins - 1
            If PRI(c, k, bb) < BIG / 2 Then
                If PRI(c, k, bb) < topP - 0.000000001 Or _
                   (Abs(PRI(c, k, bb) - topP) <= 0.000000001 And SEC(c, k, bb) < topS) Then
                    topP = PRI(c, k, bb)
                    topS = SEC(c, k, bb)
                    bk = k
                    bbb = bb
                End If
            End If
        Next bb
    Next k
    If bk < 0 Then
        SolvePiece = -1
        Exit Function
    End If
    ReDim brkV(0 To nC): ReDim brkT(0 To nC)
    nbk = 0
    k = bk
    bb = bbb
    guard = 0
    Do
        brkV(nbk) = cv(c)
        brkT(nbk) = LEV(c, k)
        nbk = nbk + 1
        If c = 0 Then Exit Do
        c2 = PC(c, k, bb): k2 = PK(c, k, bb): b2 = PB(c, k, bb)
        If c2 < 0 Then
            SolvePiece = -1
            Exit Function
        End If
        c = c2: k = k2: bb = b2
        guard = guard + 1
        If guard > nC + 2 Then
            SolvePiece = -1
            Exit Function
        End If
    Loop
    ' brkV runs from the top end down to the start: fill the tile between
    For q = nbk - 1 To 1 Step -1
        For v = brkV(q) To brkV(q - 1)
            f = (mS(a + v) - mS(a + brkV(q))) / (mS(a + brkV(q - 1)) - mS(a + brkV(q)))
            mT(a + v) = brkT(q) + f * (brkT(q - 1) - brkT(q))
        Next v
    Next q
    SolvePiece = nbk - 1
End Function
