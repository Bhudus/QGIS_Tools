Attribute VB_Name = "Sheet9"
Attribute VB_Base = "0{00020820-0000-0000-C000-000000000046}"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = True
Attribute VB_TemplateDerived = False
Attribute VB_Customizable = True

' Controls on this sheet are Form Controls (not ActiveX), so they work when
' Office has ActiveX disabled. EnsureControls (run from Workbook_Open) creates
' any that are missing; Workbook_Open also assigns each control's macro.
' The soil picker is an in-cell drop-down on the Soil Name cell.

' Time the splash screen is scheduled to close (so it can be cancelled on close)
Private SplashCloseAt As Date

Private Function SpacingSheet() As Worksheet
    Set SpacingSheet = ThisWorkbook.Worksheets("Spacing")
End Function

Private Function ShapeExists(ws As Worksheet, ByVal nm As String) As Boolean
    Dim shp As Shape
    On Error Resume Next
    Set shp = ws.Shapes(nm)
    ShapeExists = Not shp Is Nothing
    On Error GoTo 0
End Function

Private Sub ShowShape(ws As Worksheet, ByVal nm As String, ByVal bShow As Boolean)
    If ShapeExists(ws, nm) Then
        If bShow Then ws.Shapes(nm).Visible = msoTrue Else ws.Shapes(nm).Visible = msoFalse
    End If
End Sub

' Creates the sheet's Form Controls if they are missing (positions match the original layout, in points)
Public Sub EnsureControls()
    Dim ws As Worksheet, shp As Shape
    Set ws = SpacingSheet

    ' The soil picker is an in-cell drop-down on the Soil Name cell (see EnsureSoilDropdown)
    If ShapeExists(ws, "ComboBox2") Then ws.Shapes("ComboBox2").Delete
    AddButton ws, "CommandButton2", "Navigate to Official Soil Series Description", 225, 21.75, 121.5, 74.25
    AddButton ws, "CommandButton1", "Calculate Drain Spacing", 225.75, 100.5, 120, 45.75
    AddButton ws, "CommandButton4", "View General Recommendations", 225, 150.75, 119.25, 45
    Dim dgVisible As Boolean
    If ShapeExists(ws, "Drainage Groups") Then dgVisible = (ws.Shapes("Drainage Groups").Visible = msoTrue)
    AddCheckBox ws, "CheckBox1", "View Drainage Groups", 225, 200.25, 119.25, 48, dgVisible
    AddCheckBox ws, "CheckBox4", "Adjust Profile Depth", 224.25, 254.25, 121.5, 39, _
        (ws.Range("ChkSoilDepth").Value = 1)

    If Not ShapeExists(ws, "ProfileDepthCover") Then
        Set shp = ws.Shapes.AddShape(msoShapeRectangle, 15.75, 324.75, 333, 21.75)
        shp.Name = "ProfileDepthCover"
        shp.Fill.ForeColor.RGB = RGB(&H83, &HCC, &HEB)
        shp.Line.Visible = msoFalse
        shp.Visible = IIf(ws.Range("ChkSoilDepth").Value = 1, msoFalse, msoTrue)
    End If
End Sub

Private Sub AddButton(ws As Worksheet, ByVal nm As String, ByVal cap As String, _
                      ByVal L As Single, ByVal t As Single, ByVal w As Single, ByVal h As Single)
    Dim shp As Shape
    If ShapeExists(ws, nm) Then Exit Sub
    Set shp = ws.Shapes.AddFormControl(xlButtonControl, L, t, w, h)
    shp.Name = nm
    shp.OLEFormat.Object.Caption = cap
End Sub

Private Sub AddCheckBox(ws As Worksheet, ByVal nm As String, ByVal cap As String, _
                        ByVal L As Single, ByVal t As Single, ByVal w As Single, ByVal h As Single, _
                        ByVal checked As Boolean)
    Dim shp As Shape
    If ShapeExists(ws, nm) Then Exit Sub
    Set shp = ws.Shapes.AddFormControl(xlCheckBox, L, t, w, h)
    shp.Name = nm
    shp.OLEFormat.Object.Caption = cap
    If checked Then shp.ControlFormat.Value = xlOn Else shp.ControlFormat.Value = xlOff
End Sub

' Splash screen: the hidden picture SplashScreen on this sheet is shown centred
' in the window when the workbook opens, and closes after 5 seconds or when clicked.
Public Sub ShowSplash()
    Dim ws As Worksheet, shp As Shape, vr As Range, w As Single
    Set ws = SpacingSheet
    If Not ShapeExists(ws, "SplashScreen") Then Exit Sub
    Set shp = ws.Shapes("SplashScreen")
    ws.Activate
    Set vr = ThisWorkbook.Windows(1).VisibleRange
    shp.LockAspectRatio = msoTrue
    w = vr.Width * 0.9
    If w > 720 Then w = 720
    shp.Width = w
    shp.Left = vr.Left + (vr.Width - shp.Width) / 2
    shp.Top = vr.Top + (vr.Height - shp.Height) / 2
    If shp.Top < vr.Top Then shp.Top = vr.Top
    shp.ZOrder msoBringToFront
    shp.OnAction = "Sheet9.HideSplash"
    shp.Visible = msoTrue
    SplashCloseAt = Now + TimeSerial(0, 0, 5)
    Application.OnTime SplashCloseAt, "Sheet9.HideSplash"
End Sub

Public Sub HideSplash()
    On Error Resume Next
    If SplashCloseAt <> 0 Then Application.OnTime SplashCloseAt, "Sheet9.HideSplash", , False
    SplashCloseAt = 0
    ThisWorkbook.Worksheets("Spacing").Shapes("SplashScreen").Visible = msoFalse
End Sub

' "View Drainage Groups" check box
Public Sub CheckBox1_Click()
    Dim ws As Worksheet
    Set ws = SpacingSheet
    ShowShape ws, "Drainage Groups", (ws.Shapes("CheckBox1").ControlFormat.Value = xlOn)
End Sub

' "Adjust Profile Depth" check box
Public Sub CheckBox4_Click()
    Dim ws As Worksheet
    Set ws = SpacingSheet
    If ws.Shapes("CheckBox4").ControlFormat.Value <> xlOn Then
        ws.Range("ChkSoilDepth") = 0
        ShowShape ws, "ProfileDepthCover", True
    Else
        ws.Range("ChkSoilDepth") = 1
        ws.Range("SSDepth") = ws.Range("MSDepth")
        ShowShape ws, "ProfileDepthCover", False
        ws.Range("SSDepth").Select
    End If
End Sub

' Soil name drop-down: a Data Validation list on the Soil Name cell (B3).
' Its look is the cell's formatting, so change font/fill with normal cell formatting.
' Formatting is applied only the first time the list is set up.
Public Sub EnsureSoilDropdown()
    Dim ws As Worksheet, src As String, hasList As Boolean, t As Long
    Set ws = SpacingSheet
    src = "=Soils!" & ThisWorkbook.Worksheets("Soils").Range("Soils").Columns(1).Address
    t = -1
    On Error Resume Next
    t = ws.Range("SoilName").Validation.Type
    On Error GoTo 0
    hasList = (t = xlValidateList)

    With ws.Range("SoilName").MergeArea
        .Validation.Delete
        .Validation.Add Type:=xlValidateList, AlertStyle:=xlValidAlertStop, Formula1:=src
        .Validation.InCellDropdown = True
        .Validation.ErrorTitle = "Soil Name"
        .Validation.ErrorMessage = "Pick a soil from the list."
        If Not hasList Then
            .Interior.Color = RGB(0, 192, 192)
            .Font.Name = "Calibri"
            .Font.Size = 16
            .Font.Bold = True
            .Font.Color = RGB(0, 0, 255)
            .HorizontalAlignment = xlCenter
            .VerticalAlignment = xlCenter
        End If
    End With
End Sub

' Called from Worksheet_Change when the Soil Name cell changes
Private Sub SoilNameChanged()
    Dim ws As Worksheet, i As Variant
    Set ws = SpacingSheet
    i = Application.Match(ws.Range("SoilName").Value, _
            ThisWorkbook.Worksheets("Soils").Range("Soils").Columns(1), 0)
    If IsError(i) Then
        ' Cleared or not a listed soil: put the current soil back
        i = Application.Match(ws.Range("SoilNumber").Value, _
                ThisWorkbook.Worksheets("Soils").Range("Soils").Columns(2), 0)
        If IsError(i) Then i = 56
    End If
    LoadSoil CLng(i)
End Sub

Private Sub Worksheet_Change(ByVal Target As Range)
    If Intersect(Target, Me.Range("SoilName").MergeArea) Is Nothing Then Exit Sub
    SoilNameChanged
End Sub

' Copies soil i (row in the Soils table) to the Spacing sheet and shows its drainage-group picture
Public Sub LoadSoil(ByVal i As Long)
    Dim ws As Worksheet, RSoil As Range
    Set ws = SpacingSheet
    Set RSoil = ThisWorkbook.Worksheets("Soils").Range("Soils")
    Application.EnableEvents = False
    On Error GoTo Done
    ShowShape ws, CStr(ws.Range("DrainageGroup").Value), False
    ws.Range("SoilFileName") = RSoil(i, 7)
    ws.Range("SoilName") = RSoil(i, 1)
    ws.Range("SoilNumber") = RSoil(i, 2)
    ws.Range("DrainageGroup") = RSoil(i, 3)
    ws.Range("Conductivity") = RSoil(i, 4)
    ws.Range("DPorosity") = RSoil(i, 5)
    ws.Range("MSDepth") = RSoil(i, 6)
    ws.Range("SSDepth") = RSoil(i, 6)
    ws.Range("OSSD") = RSoil(i, 8)
    ShowShape ws, CStr(ws.Range("DrainageGroup").Value), True
Done:
    Application.EnableEvents = True
    If Err.Number <> 0 Then MsgBox "Could not load soil: " & Err.Description
End Sub

' "Calculate Drain Spacing" button
Public Sub CommandButton1_Click()
    Dim ws As Worksheet
    Set ws = SpacingSheet
    With formSpacing
        .Width = 313
        .Height = 431
        .cmdShow.Caption = "Show Diagram"
        .txtImpDepth.Text = ws.Range("SSDepth")
        .txtTImpDepth.Text = ws.Range("SSDepth")
        .txtSImpDepth.Text = ws.Range("SSDepth")

        .txtTDrainPor.Text = ws.Range("DPorosity")

        .txtHydCon.Text = ws.Range("Conductivity")
        .txtTHydCon.Text = ws.Range("Conductivity")
        .txtSHydCon.Text = ws.Range("Conductivity")

        .tabSpacing.Value = 0
        .frapicSteady.Visible = False
        .frapicKirkham.Visible = False
        .frapicTransient.Visible = True
        .frapicSubirrigate.Visible = False

        .Show
    End With
End Sub

' "Navigate to Official Soil Series Description" button
Public Sub CommandButton2_Click()
    Dim url As String
    url = SpacingSheet.Range("OSSD")
    ThisWorkbook.FollowHyperlink url
End Sub

' "View General Recommendations" button
Public Sub CommandButton4_Click()
    With PictureForm
        .picSteady.Visible = False
        .picTransient.Visible = False
        .picSubIrrigate.Visible = False
        .picSpacing.Visible = True
        .Show
    End With
End Sub
