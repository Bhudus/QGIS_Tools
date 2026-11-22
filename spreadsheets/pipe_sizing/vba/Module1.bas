Attribute VB_Name = "Module1"
Global TD As Single, DA As Single, Q As Single, S As Single, DC As Single, V As Single
Global DiaY1(1 To 14) As Single, DiaY2(1 To 14) As Single
Global BDia As Boolean

' Sediment depth (ft) in a pipe of diameter TDft (ft). D0W is the sediment entry:
' a percent of the diameter, or inches when Plot!AR7 = 2 (Depth option on the form).
Function SedFt(TDft, D0W)
    If ThisWorkbook.Worksheets("Plot").Range("AR7").Value = 2 Then
        SedFt = D0W / 12
    Else
        SedFt = D0W * TDft / 100
    End If
End Function

Sub Plotlines(line1, line2)
    Dim Range1 As Range, Range2 As Range
    
    Select Case line1
    Case Is = 1
       Set Range1 = Sheets("Plot").Range("PlotSlope")
    Case Is = 2
       Set Range1 = Sheets("Plot").Range("PlotFlow")
    Case Is = 3
       Set Range1 = Sheets("Plot").Range("PlotVelocity")
    Case Is = 4
       Set Range1 = Sheets("Plot").Range("PlotPipe")
    End Select
    
     Select Case line2
    Case Is = 1
       Set Range2 = Sheets("Plot").Range("PlotSlope")
    Case Is = 2
       Set Range2 = Sheets("Plot").Range("PlotFlow")
    Case Is = 3
       Set Range2 = Sheets("Plot").Range("PlotVelocity")
    Case Is = 4
       Set Range2 = Sheets("Plot").Range("PlotPipe")
    End Select
    For ii = 1 To 6: For jj = 1 To 2
       Sheets("Plot").Cells(ii + 28, jj + 32) = Range1(ii, jj)
       Sheets("Plot").Cells(ii + 37, jj + 32) = Range2(ii, jj)
    Next jj, ii
End Sub


Function Roughness(a As Single) As Single
    If frmMain.optPlastic.Value = True Then
        If a <= 8 Then Roughness = 0.015
        If a > 8 And a <= 12 Then Roughness = 0.017
        If a > 12 Then Roughness = 0.02
        Sheets("Plot").Range("ptype") = 1
    ElseIf frmMain.optClayConcrete.Value = True Then
        Roughness = 0.013
        Sheets("Plot").Range("ptype") = 2
    ElseIf frmMain.optSmoothW.Value = True Then
        Roughness = 0.012
        Sheets("Plot").Range("ptype") = 3
    Else
        Roughness = Val(frmMain.txtOthersN.Text)
        Sheets("Plot").Range("ptype") = 4
    End If
End Function
Function Ruffness(a, ptype) As Single
    If ptype = 1 Then
        If a <= 8 Then Ruffness = 0.015
        If a > 8 And a <= 12 Then Ruffness = 0.017
        If a > 12 Then Ruffness = 0.02
    ElseIf ptype = 2 Then Ruffness = 0.013
    ElseIf ptype = 3 Then Ruffness = 0.012
    Else
        Ruffness = Val(frmMain.txtOthersN.Text)
    End If
End Function
Function VSlope(TDW, VW, D0W, ptype)
            TD1 = TDW / 12: V1 = VW
            D01 = SedFt(TD1, D0W)
            If D01 >= TD1 Then VSlope = CVErr(xlErrNA): Exit Function
            theta = WorksheetFunction.Acos((TD1 - 2 * D01) / TD1)
            HRadius = PipeArea(TD1, D01, theta) / WP(TD1, D01, theta)
            S1 = VToSlope(V1, Ruffness(TDW, ptype), HRadius)
            VSlope = S1 * 100
End Function
Function VDischarge(TDW, VW, D0W, ptype)
            TD1 = TDW / 12: V1 = VW
            D01 = SedFt(TD1, D0W)
            If D01 >= TD1 Then VDischarge = CVErr(xlErrNA): Exit Function
            theta = WorksheetFunction.Acos((TD1 - 2 * D01) / TD1)
            Q1 = PipeArea(TD1, D01, theta) * V1: qfz = TD
            VDischarge = Q1
End Function


Function Flowrate(TDW, SW, D0W, RUFF)
            TD1 = TDW / 12: S1 = SW / 100
            D01 = SedFt(TD1, D0W)
            If D01 >= TD1 Then Flowrate = CVErr(xlErrNA): Exit Function
            theta = WorksheetFunction.Acos((TD1 - 2 * D01) / TD1)
            HRadius = PipeArea(TD1, D01, theta) / WP(TD1, D01, theta)
            Q1 = 1.486 * PipeArea(TD1, D01, theta) * HRadius ^ (2 / 3) * S1 ^ 0.5 / RUFF
            Flowrate = Q1
End Function

Function DrainageCoeff() As Single
     Dim DDC As Single
    If frmMain.optDC3_8.Value = True Then DDC = 0.375
    If frmMain.optDC1_2.Value = True Then DDC = 0.5
    If frmMain.optDC3_4.Value = True Then DDC = 0.75
   If frmMain.optDC_1.Value = True Then DDC = 1
   If frmMain.optOthers.Value = True Then DDC = Val(frmMain.txtOthers.Text)
  DrainageCoeff = DDC
End Function

Function PruductTile(realsize) As Single
    Select Case realsize
        Case Is <= 3: PruductTile = 3
        Case Is <= 4: PruductTile = 4
        Case Is <= 5: PruductTile = 5
        Case Is <= 6: PruductTile = 6
        Case Is <= 8: PruductTile = 8
        Case Is <= 10: PruductTile = 10
        Case Is <= 12: PruductTile = 12
        Case Is <= 15: PruductTile = 15
        Case Is <= 18: PruductTile = 18
        Case Is <= 21: PruductTile = 21
        Case Is <= 24: PruductTile = 24
        Case Is <= 30: PruductTile = 30
        Case Is <= 36: PruductTile = 36
        Case Is <= 42: PruductTile = 42
        Case Else: PruductTile = CInt(realsize + 0.5)
    End Select
End Function
Function QToArea(qq) As Single
    DC = DrainageCoeff()
    QToArea = qq / (9.64506173 * 10 ^ (-7) * DC)
End Function

Function VToSlope(vv, nn, rr) As Single
    VToSlope = (vv * nn / (1.486 * rr ^ (2 / 3))) ^ 2
End Function

Function AreaToQ(aa) As Single
    'aa=drainage area in square ft
    AreaToQ = 9.64506173 * 10 ^ (-7) * aa * DC
End Function


Function PipeArea(a, b, theta) As Single
    PipeArea = (WorksheetFunction.Pi() - theta) * a * a / 4 + (a - 2 * b) / 4 * a * Sin(theta)
End Function

Function WP(a, b, theta)
    WP = (WorksheetFunction.Pi() - theta) * a + a * Sin(theta)
End Function

