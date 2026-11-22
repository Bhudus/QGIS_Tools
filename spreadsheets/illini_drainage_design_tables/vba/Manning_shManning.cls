Attribute VB_Name = "shManning"
Attribute VB_Base = "0{00020820-0000-0000-C000-000000000046}"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = True
Attribute VB_TemplateDerived = False
Attribute VB_Customizable = True
Option Explicit

' Recalculate automatically whenever an input cell changes
Private Sub Worksheet_Change(ByVal target As Range)
    If Intersect(target, Me.Range("InputArea")) Is Nothing Then Exit Sub
    CalculateManning
End Sub
