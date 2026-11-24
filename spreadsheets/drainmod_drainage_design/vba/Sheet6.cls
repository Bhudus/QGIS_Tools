Attribute VB_Name = "Sheet6"
Attribute VB_Base = "0{00020820-0000-0000-C000-000000000046}"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = True
Attribute VB_TemplateDerived = False
Attribute VB_Customizable = True
Option Explicit

' POE Calculator: a new P.O.E. or design flow recalculates at once. Typing a
' P.O.E. switches to "value given P.O.E.", typing a flow to "P.O.E. given value".
Private Sub Worksheet_Change(ByVal Target As Range)
    If Intersect(Target, Union(Me.Range("POEPct"), Me.Range("POEFlow"), Me.Range("POECalcMode"))) Is Nothing Then Exit Sub
    Application.EnableEvents = False
    If Not Intersect(Target, Me.Range("POEPct")) Is Nothing Then
        Me.Range("POECalcMode").Value = POE_MODE_VALUE
    ElseIf Not Intersect(Target, Me.Range("POEFlow")) Is Nothing Then
        Me.Range("POECalcMode").Value = POE_MODE_POE
    End If
    Application.EnableEvents = True
    If Trim$(CStr(ThisWorkbook.Worksheets("POE Daily").Cells(1, 1).Value)) = "" Then Exit Sub
    UpdatePOE
End Sub
