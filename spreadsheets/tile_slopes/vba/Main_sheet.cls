Attribute VB_Name = "Sheet1"
Attribute VB_Base = "0{00020820-0000-0000-C000-000000000046}"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = True
Attribute VB_TemplateDerived = False
Attribute VB_Customizable = True
Attribute VB_Control = "SpinButton8, 18, 0, MSForms, SpinButton"
Attribute VB_Control = "CheckBox1, 16, 1, MSForms, CheckBox"
Attribute VB_Control = "SpinButton4, 10, 2, MSForms, SpinButton"
Attribute VB_Control = "CommandButton2, 2, 3, MSForms, CommandButton"
Attribute VB_Control = "SpinButton2, 7, 4, MSForms, SpinButton"
Attribute VB_Control = "SpinButton3, 8, 5, MSForms, SpinButton"
Attribute VB_Control = "SpinButton1, 11, 6, MSForms, SpinButton"
Attribute VB_Control = "SpinButton5, 12, 7, MSForms, SpinButton"
Attribute VB_Control = "SpinButton6, 13, 8, MSForms, SpinButton"
Attribute VB_Control = "CommandButton1, 14, 9, MSForms, CommandButton"
Attribute VB_Control = "SpinButton7, 15, 10, MSForms, SpinButton"




Private Sub CheckBox1_Click()
If CheckBox1.Value = True Then
  Me.Range("check1") = 1
  
  Me.Range("Cstart") = Int(WorksheetFunction.Min(Me.Range("A7:A421")))
  Me.Range("Cend") = Int(WorksheetFunction.Max(Me.Range("A7:A421"))) + 1
End If

If CheckBox1.Value = False Then Me.Range("check1") = 0

End Sub

Private Sub CommandButton1_Click()
Dim cht As Chart

With Me
Set cht = Me.ChartObjects("Chart 1").Chart
cht.Axes(xlValue).MinimumScale = .Range("min_elev")
cht.Axes(xlValue).MaximumScale = .Range("max_elev")
.SpinButton1.Value = 500: .Range("o6") = .SpinButton1.Value
.SpinButton2.Value = 500: .Range("p6") = .SpinButton2.Value
.SpinButton3.Value = 500: .Range("q6") = .SpinButton3.Value
.SpinButton4.Value = 500: .Range("r6") = .SpinButton4.Value
.SpinButton5.Value = 500: .Range("s6") = .SpinButton5.Value
.SpinButton6.Value = 500: .Range("t6") = .SpinButton6.Value
.SpinButton7.Value = 500: .Range("u6") = .SpinButton7.Value
.SpinButton8.Value = 500: .Range("u7") = .SpinButton8.Value
End With
End Sub


Private Sub CommandButton2_Click()
Dim NewNameR As String
NewNameR = InputBox(Prompt:="Enter Lateral Name")
Sheet3.Range("m22") = InputBox(Prompt:="Distance along main")

Sheet3.Visible = xlSheetVisible
 Sheet3.Copy After:=Worksheets(Sheets.Count)
 Worksheets(Sheets.Count).Name = NewNameR
  Worksheets(Sheets.Count).Range("d2") = NewNameR
  Worksheets(Sheets.Count).ChartObjects("Chart 1").Chart.ChartTitle.Text = NewNameR
 Sheet3.Range("m22") = 1
Sheet3.Visible = xlSheetHidden






End Sub

Private Sub SpinButton1_change()

With Me
    .Range("l5") = .Range("l5") - (.Range("o6") - SpinButton1.Value) / 100
    .Range("o6") = SpinButton1.Value
End With
End Sub

Private Sub SpinButton2_Change()
With Me
    .Range("l6") = .Range("l6") - (.Range("p6") - SpinButton2.Value) / 100
    .Range("p6") = SpinButton2.Value
End With
End Sub

Private Sub SpinButton3_Change()
With Me
    .Range("l11") = .Range("l11") - (.Range("q6") - SpinButton3.Value) / 100
    .Range("q6") = SpinButton3.Value
End With
End Sub

Private Sub SpinButton4_Change()
 With Me
    .Range("l12") = .Range("l12") - (.Range("r6") - SpinButton4.Value) / 100
    .Range("r6") = SpinButton4.Value
End With
End Sub
Private Sub SpinButton5_Change()
With Me
    .Range("l8") = .Range("l8") - (.Range("s6") - SpinButton5.Value) / 100
    .Range("s6") = SpinButton5.Value
End With
End Sub

Private Sub SpinButton6_Change()
 With Me
    .Range("l9") = .Range("l9") - (.Range("t6") - SpinButton6.Value) / 100
    .Range("t6") = SpinButton6.Value
End With
End Sub

Private Sub SpinButton7_Change()
 With Me
    .Range("Moffset") = .Range("Moffset") - (.Range("u6") - SpinButton7.Value) / 100
    .Range("u6") = SpinButton7.Value
End With
End Sub

Private Sub SpinButton8_Change()
 With Me
    .Range("Overide") = .Range("Overide") - (.Range("u7") - SpinButton8.Value) / 100
    .Range("u7") = SpinButton8.Value
End With
End Sub
