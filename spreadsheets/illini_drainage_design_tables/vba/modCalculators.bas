Attribute VB_Name = "modCalculators"
Option Explicit

' Drainage design calculators
' Ported from the VB6 projects (AGENG):
'   Table 1  MinRadii        Table 6  maxLength       Table 7  MinGrade
'   Table 8  wheelLoads      Table 9  TrenchDepth     Table 10 MaxTrenchDepth
'   PumpingPlants
'
' The lookup sheets work entirely with formulas. Macros below reproduce the
' buttons on the original forms.

Private Const PUMP_SHEET As String = "Pumping Plants"
Private Const MAXLEN_SHEET As String = "Max Length (T6)"

' ----------------------------------------------------------------- common
Public Sub ExitWorkbook()
    ThisWorkbook.Close
End Sub

Private Function PositiveNumber(ByVal cell As Range) As Boolean
    If IsEmpty(cell.Value) Then Exit Function
    If Not IsNumeric(cell.Value) Then Exit Function
    PositiveNumber = (CDbl(cell.Value) > 0)
End Function

' ------------------------------------------------------- Table 6 max length
' Max length (m) = 10000 / (DC[mm/day] x Spacing[m] x n)
'                  x (D[mm] x S^0.1875 / 51.7)^(1/0.375), then x 3.281 -> ft
Public Sub MaxLength_Calculate()
    Dim ws As Worksheet
    Set ws = ThisWorkbook.Worksheets(MAXLEN_SHEET)
    Application.Calculate
    If PositiveNumber(ws.Range("ML_DC")) And PositiveNumber(ws.Range("ML_Spacing")) _
       And PositiveNumber(ws.Range("ML_ManningN")) And PositiveNumber(ws.Range("ML_Dia")) _
       And PositiveNumber(ws.Range("ML_Slope")) Then
        ws.Range("ML_Result").Select
    Else
        MsgBox "Please check your input data", vbCritical, "Warning"
    End If
End Sub

Public Sub MaxLength_Clear()
    ThisWorkbook.Worksheets(MAXLEN_SHEET).Range("ML_DC,ML_Spacing,ML_ManningN,ML_Dia,ML_Slope").ClearContents
    Application.Calculate
End Sub

' ------------------------------------------------------------ Pumping plants
'   Pump capacity (GPM)          = 18.9 x DC (in/24 hr) x Area (acres)
'   Power requirements (HP)      = Q (GPM) x Head (ft) / 3960 / Efficiency
'   Water storage, automatic:  storage = Q x 2 / cycles; area = Q x 2 / depth / cycles
'   Water storage, manual:     storage = Q x 25;         area = Q x 25 / depth
Public Sub CalculateResults()
    Dim ws As Worksheet
    Dim ok As Boolean

    Set ws = ThisWorkbook.Worksheets(PUMP_SHEET)

    Select Case CStr(ws.Range("CalcType").Value)
        Case "Pump capacity"
            ok = PositiveNumber(ws.Range("DrainCoef")) And _
                 PositiveNumber(ws.Range("WatershedArea"))
        Case "Power requirements"
            ok = PositiveNumber(ws.Range("PowerQ")) And _
                 PositiveNumber(ws.Range("TotalHead")) And _
                 PositiveNumber(ws.Range("Efficiency"))
        Case "Water storage"
            ok = PositiveNumber(ws.Range("StorageQ")) And _
                 PositiveNumber(ws.Range("SumpDepth"))
            If CStr(ws.Range("Operation").Value) = "Automatic operation" Then
                ok = ok And PositiveNumber(ws.Range("CyclesPerHour"))
            End If
        Case Else
            ok = False
    End Select

    Application.Calculate

    If Not ok Then
        MsgBox "Invalid value.", vbCritical, "Warning"
    Else
        ws.Range("Result1").Select
    End If
End Sub

Public Sub ResetInputs()
    ThisWorkbook.Worksheets(PUMP_SHEET).Range( _
        "DrainCoef,WatershedArea,PowerQ,TotalHead,Efficiency,StorageQ,SumpDepth,CyclesPerHour").ClearContents
    Application.Calculate
End Sub
