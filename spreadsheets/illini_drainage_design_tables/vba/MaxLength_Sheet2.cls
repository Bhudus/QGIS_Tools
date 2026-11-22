Attribute VB_Name = "Sheet2"
Attribute VB_Base = "0{00020820-0000-0000-C000-000000000046}"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = True
Attribute VB_TemplateDerived = False
Attribute VB_Customizable = True
Option Explicit

' Max Length (Table 6): choosing a drain material fills in Manning's n,
' like the option buttons on the original form.
Private Sub Worksheet_Change(ByVal Target As Range)
    If Intersect(Target, Me.Range("ML_Material")) Is Nothing Then Exit Sub
    Application.EnableEvents = False
    If CStr(Me.Range("ML_Material").Value) = "Clay and concrete tile" Then
        Me.Range("ML_ManningN").Value = 0.012
    Else
        Me.Range("ML_ManningN").Value = 0.013
    End If
    Application.EnableEvents = True
End Sub
