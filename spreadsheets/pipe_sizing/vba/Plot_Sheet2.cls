Attribute VB_Name = "Sheet2"
Attribute VB_Base = "0{00020820-0000-0000-C000-000000000046}"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = True
Attribute VB_TemplateDerived = False
Attribute VB_Customizable = True
Public Sub CommandButton1_Click()
Dim pt As Point
Dim p As Long
Dim dl As DataLabel
Dim srs As Series
Dim cht As Chart
Set cht = Sheets("Plot").ChartObjects("PicPlott").Chart
For ig = 1 To 20
p = 0
Set srs = cht.SeriesCollection(ig)
srs.HasDataLabels = False '## Turn off the data labels
'For Each pt In srs.Points
 '   p = p + 1
 '   If p = 2 Then
  '      pt.HasDataLabel = True
  Set pt = srs.Points(2)
  pt.HasDataLabel = True
        Set dl = pt.DataLabel
        dl.Text = srs.Name
 '        End If
'Next
Next ig
End Sub
Public Sub CommandButton2_Click()
frmMain.StartUpPosition = 2
frmMain.Show
End Sub

Public Sub CommandButton3_Click()
Sheets("Plot").ChartObjects("PicPlott").Visible = True
End Sub

' The sheet's buttons are a picture (LoadPipeSizing) and Form Control buttons, not ActiveX,
' so they work when Office has ActiveX disabled. Called from Workbook_Open.
Public Sub EnsureControls()
    Dim shp As Shape
    On Error Resume Next
    Me.Shapes("LoadPipeSizing").OnAction = "Sheet2.CommandButton2_Click"
    On Error GoTo 0
    AddButton "CommandButton1", "Label Lines", 2474.25, 1115.25, 96.75, 23.25
    AddButton "CommandButton3", "Show Chart", 2187, 1148.25, 89.25, 25.5
End Sub

Private Sub AddButton(ByVal nm As String, ByVal cap As String, _
                      ByVal l As Single, ByVal t As Single, ByVal w As Single, ByVal h As Single)
    Dim shp As Shape
    On Error Resume Next
    Set shp = Me.Shapes(nm)
    On Error GoTo 0
    If shp Is Nothing Then
        Set shp = Me.Shapes.AddFormControl(xlButtonControl, l, t, w, h)
        shp.Name = nm
        shp.OLEFormat.Object.Caption = cap
    End If
    shp.OnAction = "Sheet2." & nm & "_Click"
End Sub
