Attribute VB_Name = "Sheet7"
Attribute VB_Base = "0{00020820-0000-0000-C000-000000000046}"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = True
Attribute VB_TemplateDerived = False
Attribute VB_Customizable = True
Attribute VB_Control = "CommandButton1, 1, 0, MSForms, CommandButton"
Private Sub CommandButton1_Click()
Worksheets("Users Guide").Visible = False
Worksheets("Main").Activate
If Worksheets("Main").Range("dBaseFlag") = 0 Then
  FrmIntroduction.StartUpPosition = 3
  FrmIntroduction.Show
End If

End Sub
