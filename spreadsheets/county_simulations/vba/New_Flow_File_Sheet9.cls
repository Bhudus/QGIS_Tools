Attribute VB_Name = "Sheet9"
Attribute VB_Base = "0{00020820-0000-0000-C000-000000000046}"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = True
Attribute VB_TemplateDerived = False
Attribute VB_Customizable = True
Attribute VB_Control = "GetSoils, 45, 2, MSForms, CommandButton"
Attribute VB_Control = "GetWeather, 43, 3, MSForms, CommandButton"
Attribute VB_Control = "CheckBox4, 42, 4, MSForms, CheckBox"
Attribute VB_Control = "CheckBox3, 40, 5, MSForms, CheckBox"
Attribute VB_Control = "CheckBox2, 39, 6, MSForms, CheckBox"
Attribute VB_Control = "CheckBox1, 38, 7, MSForms, CheckBox"
Attribute VB_Control = "OptionButton13, 35, 8, MSForms, OptionButton"
Attribute VB_Control = "OptionButton12, 32, 9, MSForms, OptionButton"
Attribute VB_Control = "RunDM, 25, 10, MSForms, CommandButton"
Attribute VB_Control = "ComboBox2, 21, 11, MSForms, ComboBox"
Attribute VB_Control = "OptionButton1, 1, 12, MSForms, OptionButton"
Attribute VB_Control = "OptionButton2, 3, 13, MSForms, OptionButton"
Attribute VB_Control = "OptionButton3, 4, 14, MSForms, OptionButton"
Attribute VB_Control = "OptionButton4, 6, 15, MSForms, OptionButton"
Attribute VB_Control = "OptionButton5, 7, 16, MSForms, OptionButton"
Attribute VB_Control = "OptionButton6, 8, 17, MSForms, OptionButton"
Attribute VB_Control = "OptionButton7, 10, 18, MSForms, OptionButton"
Attribute VB_Control = "OptionButton8, 12, 19, MSForms, OptionButton"
Attribute VB_Control = "OptionButton9, 14, 20, MSForms, OptionButton"
Attribute VB_Control = "OptionButton10, 17, 21, MSForms, OptionButton"
Attribute VB_Control = "OptionButton11, 19, 22, MSForms, OptionButton"
Attribute VB_Control = "ComboBox1, 20, 23, MSForms, ComboBox"
#If VBA7 Then
    Private Declare PtrSafe Function ShellExecute Lib "shell32.dll" Alias "ShellExecuteA" _
        (ByVal hwnd As Long, ByVal lpOperation As String, ByVal lpFile As String, ByVal lpParameters As String, ByVal lpDirectory As String, ByVal nShowCmd As Long) As Long
#Else
    Private Declare Function ShellExecute Lib "shell32.dll" Alias "ShellExecuteA" _
        (ByVal hwnd As Long, ByVal lpOperation As String, ByVal lpFile As String, ByVal lpParameters As String, ByVal lpDirectory As String, ByVal nShowCmd As Long) As Long
#End If

Const SW_SHOW = 1
Const SW_SHOWMAXIMIZED = 3



Private Sub CheckBox1_Click()
If CheckBox1.Value = False Then
  Worksheets("New Flow File").Columns("K:M").EntireColumn.Hidden = True
  Worksheets("New Flow File").CheckBox2.Visible = False
  Worksheets("New Flow File").Range("IntWTMFlag") = 0
  Worksheets("New Flow File").Range("DWMFlag") = 0
  Sheets("Main").Shapes("TextBox 2").Visible = False
  
Else
  Worksheets("New Flow File").Columns("K:M").EntireColumn.Hidden = False
  Worksheets("New Flow File").CheckBox2.Visible = True
  Worksheets("New Flow File").Range("IntWTMFlag") = 1
  Worksheets("New Flow File").Range("DWMFlag") = 1
  Sheets("Main").Shapes("TextBox 2").Visible = True
  UpdateWMLabels

End If
End Sub
Private Sub CheckBox3_Click()
If CheckBox3.Value = False Then
  Worksheets("New Flow File").Rows("23:25").EntireRow.Hidden = True
  Worksheets("New Flow File").Range("ChkDeepSeepage") = 0

   
Else
  Worksheets("New Flow File").Rows("23:25").EntireRow.Hidden = False
  Worksheets("New Flow File").Range("ChkDeepSeepage") = 1
  Worksheets("New Flow File").Range("Piezhead") = 200
  Worksheets("New Flow File").Range("RestrictThick") = 200
  Worksheets("New Flow File").Range("RestrictCond") = 0.00006
  Worksheets("New Flow File").Range("PiezHead").Select

End If
End Sub

Private Sub CheckBox4_Click()
If CheckBox4.Value = False Then
  Worksheets("New Flow File").Rows("20:21").EntireRow.Hidden = True
  Worksheets("New Flow File").Range("ChkSoilDepth") = 0

   
Else
  Worksheets("New Flow File").Rows("20:21").EntireRow.Hidden = False
  Worksheets("New Flow File").Range("ChkSoilDepth") = 1
  Worksheets("New Flow File").Range("SSDepth") = Worksheets("New Flow File").Range("MSDepth")
  Worksheets("New Flow File").Range("SSDepth").Select

End If
End Sub

Private Sub CheckBox2_Click()
If CheckBox2.Value = False Then
  
  Worksheets("New Flow File").Range("chkFallow") = 1

Else
 
Worksheets("New Flow File").Range("chkFallow") = 0
End If
End Sub

Private Sub ComboBox1_Change()
Dim i As Integer, Counties As String, RCounty As Range
i = ComboBox1.ListIndex + 1
If i < 1 Then Exit Sub
StateName = Worksheets("New Flow File").Range("StateName")
Counties = StateName + "Counties"
Set RCounty = Worksheets("Counties").Range(Counties)
Worksheets("DMData").Range("CountyName") = RCounty(i)
' weather files are downloaded for the site latitude/longitude when the
' simulation runs; the county name is used as a label only
Worksheets("New Flow File").Range("RainfallFileName") = "(downloaded when the simulation runs)"
Worksheets("New Flow File").Range("TemperatureFileName") = "(downloaded when the simulation runs)"
' weather location: centroid of the selected county (editable afterwards)
If gLocating Then Exit Sub
If ActiveSheet.Name = "New Flow File" Then SetCountyCentroid True
If Not IllinoisSoilDb() And LCase$(Left$(Worksheets("New Flow File").Range("SoilSource"), 4)) = "soil" Then
  If ActiveSheet.Name = "New Flow File" Then RefreshSoilPreview
End If
End Sub

Private Sub ComboBox2_Change()
Dim i As Integer, Soils As String, RSoil As Range
i = ComboBox2.ListIndex + 1
If i < 1 Then Exit Sub
StateName = Worksheets("New Flow File").Range("StateName")
Soils = StateName + "Soils"
Set RSoil = Worksheets("Soils").Range(Soils)
' picking from the dropdown in Illinois means the Illinois soil database
If StateName = "Illinois" And Not gLocating Then
  Application.EnableEvents = False
  Worksheets("New Flow File").Range("SoilSource") = "Soil from dropdown"
  Application.EnableEvents = True
End If
If IllinoisSoilDb() Then
  ' Illinois: representative soil from the county soil database
  Worksheets("New Flow File").Range("SoilFileName") = RSoil(i, 2)
  If Not gLocating Then RefreshSoilPreview
Else
  ' other states: soil series name; its SSURGO data (from the selected county,
  ' or the state if the county has none) are downloaded when "Soil source"
  ' is the dropdown
  Worksheets("New Flow File").Range("SoilFileName") = Trim$(RSoil(i, 1))
  If LCase$(Left$(Worksheets("New Flow File").Range("SoilSource"), 4)) = "soil" And Not gLocating Then
    If ActiveSheet.Name = "New Flow File" Then RefreshSoilPreview
  End If
End If
End Sub

Private Sub Worksheet_Change(ByVal Target As Range)
' refresh the soil (and its profile depth) when the site or soil choice changes
Dim watch As Range
On Error Resume Next
Set watch = Union(Range("SiteLat"), Range("SiteLon"), Range("SoilSource"), Range("MaxRoot"), Range("AvgRoot"))
If Not Intersect(Target, watch) Is Nothing Then
  On Error GoTo 0
  If IsNumeric(Range("SiteLat").Value) And IsNumeric(Range("SiteLon").Value) _
     And Not IsEmpty(Range("SiteLat").Value) And Not IsEmpty(Range("SiteLon").Value) Then RefreshSoilPreview
End If
On Error Resume Next
If Not Intersect(Target, Union(Range("ObsYear1"), Range("ObsYear2"))) Is Nothing Then SetTimePeriodCaptions
If Not Intersect(Target, Range("WMType")) Is Nothing Then UpdateWMLabels
On Error GoTo 0
End Sub

Private Sub GetWeather_Click()
' copies the downloaded DRAINMOD weather files (.RAI, .TEM) for the current
' site and time period to a chosen folder
Dim Savepath As String, f As Variant, n As Integer
On Error GoTo Failed
EnsureWeatherFiles
Application.StatusBar = False
Savepath = GetFolder(" get it")
If Savepath = "" Then Exit Sub
Savepath = Savepath + "\"
For Each f In Array(Sheets("DMData").Range("RaiPath").Value, Sheets("DMData").Range("TemPath").Value)
  If FileHasData(CStr(f)) Then
    FileCopy CStr(f), Savepath & Mid$(CStr(f), InStrRev(CStr(f), "\") + 1)
    n = n + 1
  End If
Next f
MsgBox n & " weather file(s) copied to " & Savepath
Exit Sub
Failed:
Application.StatusBar = False
MsgBox "Could not get the weather files: " & Err.Description, vbExclamation
End Sub

Private Sub GetSoils_Click()
' copies the soil files for the current soil to a chosen folder: the Illinois
' soil database files (.dsi, .sin, .mis, .wdv), or the files built from SSURGO
' (.soi, .SIN, .MIS, .WDV)
Dim Savepath As String, f As Variant, n As Integer, files As Variant, sinP As String
On Error GoTo Failed
EnsureSoilFiles
Application.StatusBar = False
Savepath = GetFolder(" get it")
If Savepath = "" Then Exit Sub
Savepath = Savepath + "\"
sinP = CStr(Sheets("DMData").Range("SinPath").Value)
If IllinoisSoilDb() Then
  files = Array(Left$(sinP, Len(sinP) - 4) & ".dsi", sinP, Sheets("DMData").Range("MisPath").Value, _
                Sheets("DMData").Range("WdvPath").Value)
Else
  files = Array(Sheets("DMData").Range("SoiPath").Value, sinP, Sheets("DMData").Range("MisPath").Value, _
                Sheets("DMData").Range("WdvPath").Value)
End If
For Each f In files
  If FileHasData(CStr(f)) Then
    FileCopy CStr(f), Savepath & Mid$(CStr(f), InStrRev(CStr(f), "\") + 1)
    n = n + 1
  End If
Next f
MsgBox n & " soil file(s) for " & Sheets("New Flow File").Range("SoilFileName").Value & " copied to " & Savepath
Exit Sub
Failed:
Application.StatusBar = False
MsgBox "Could not get the soil files: " & Err.Description, vbExclamation
End Sub

Private Sub OptionButton12_Click()
If OptionButton12.Value = True Then
   Worksheets("New Flow File").Range("intCropflag") = 3
   Worksheets("New Flow File").Range("$H$3").Value = "Corn and Soybean"
Else
   Worksheets("New Flow File").Range("intCropflag") = 1
End If

End Sub

Private Sub OptionButton13_Click()
If OptionButton13.Value = True Then
   Worksheets("New Flow File").Range("intCropflag") = 1
   Worksheets("New Flow File").Range("$H$3").Value = "Continuous corn"
Else
   Worksheets("New Flow File").Range("intCropflag") = 3
End If
End Sub

Sub RunDM_Click()
Call DrainMod_Run
End Sub

Public Sub DrainMod_Run()
  Dim RetVal As Long, InFile As String, plotp(30) As Single
  Dim wsh As Object
Set wsh = VBA.CreateObject("WScript.Shell")
  'On Error Resume Next
 Dim dmExe As String
 If Not EnsureDrainmodFolder() Then Exit Sub
 dmExe = DMHydroPath()
 If dmExe = "" Then
    MsgBox "DRAINMOD cannot run until you select the DRAINMOD hydrology program (DMhydro.exe).", vbExclamation, "DRAINMOD"
    Exit Sub
 End If
 ' weather and soil inputs come from online sources (cached locally)
 On Error GoTo OnlineFailed
 Weather
 Soilss
 On Error GoTo 0
Sheets("New Flow File").Range("intWTMFlag") = 0
 
 
SpacingStart = Sheets("New Flow File").Range("DrainSpace1")
SpacingEnd = Sheets("New Flow File").Range("DrainSpace2")
SpacingInterval = Sheets("New Flow File").Range("DrainSpaceInt")
DepthStart = Sheets("New Flow File").Range("DrainDepth1")
DepthEnd = Sheets("New Flow File").Range("DrainDepth2")
DepthInterval = Sheets("New Flow File").Range("DrainDepthInt")
Worksheets("DMOutput").Range("c4:af200").ClearContents
Worksheets("DMOutput").Range("AH4:AI4").ClearContents   ' undrained baseline
 NumSpace = (SpacingEnd - SpacingStart) / SpacingInterval + 1
 NumDepths = (DepthEnd - DepthStart) / DepthInterval + 1
 NumRuns = NumSpace * NumDepths
 RunOutputLine = 3: Ndepths = 0
  Worksheets("New Flow File").Visible = False
  Worksheets("main").Activate
 For Depth = DepthStart To DepthEnd Step DepthInterval
   Ndepths = Ndepths + 1
 
   NSpacings = 0
   For Spacing = SpacingStart To SpacingEnd Step SpacingInterval
     NSpacings = NSpacings + 1
   
     Sheets("DMData").Range("DrainDepth") = CInt(Depth * 30.48)
     Sheets("DMData").Range("DrainSpace") = CInt(Spacing * 30.48)
 
     PROJC
     GENC
     CurPath = Sheets("New Flow File").Range("CurrentPath")
     strProjFile = Worksheets("DMData").Range("strProjFile")
     strshell = """" & dmExe & """ "
     
     'RetVal = ShellExecute(0, "open", strshell, strProjFile, "", vbHide)
     RetVal = wsh.Run(strshell + " " + strProjFile, 0, vbTrue)

     PltFile = CurPath & "\outputs\run0.yr"

    'Application.Wait Now + TimeValue("0:00:02")




Open PltFile For Input As #1

Do Until EOF(1)
Line Input #1, textline

If InStr(textline, "AVG") > 0 Then
For ig = 0 To 5
   plotp(ig) = Val(Mid(textline, 6 + 8 * ig, 8)) / 2.54
Next ig
   plotp(6) = Val(Mid(textline, 55, 8))
   plotp(7) = Val(Mid(textline, 63, 8))
End If
 
Loop
 Close #1

Yldfile = Left(PltFile, Len(PltFile) - 3) + ".yld"

Open Yldfile For Input As #1
Soy = 0
Do Until EOF(1)
Line Input #1, textline

If InStr(textline, "AVG") > 0 Then

   plotp(Soy + 8) = Val(Mid(textline, 7, 7))
   plotp(Soy + 9) = Val(Mid(textline, 14, 6))
   plotp(Soy + 10) = Val(Mid(textline, 24, 6))
   plotp(Soy + 11) = Val(Mid(textline, 31, 5))
   plotp(Soy + 12) = Val(Mid(textline, 36, 6))
   plotp(Soy + 13) = Val(Mid(textline, 46, 7))
   plotp(Soy + 14) = Val(Mid(textline, 55, 6))
   plotp(Soy + 15) = Val(Mid(textline, 63, 6))
   plotp(Soy + 16) = Val(Mid(textline, 70, 7))
   plotp(Soy + 17) = Val(Mid(textline, 79, 6))

Soy = Soy + 10
End If
 
Loop
111 Close #1
Soy = Soy - 10
RunOutputLine = RunOutputLine + 1
Worksheets("DMOutput").Cells(RunOutputLine, 3) = Depth
Worksheets("DMOutput").Cells(RunOutputLine, 4) = Spacing
For ig = 0 To 27
  If Soy = 0 And ig > 17 Then GoTo 100
  Worksheets("DMOutput").Cells(RunOutputLine, ig + 5) = plotp(ig)
100 Next ig

 Application.Wait Now + TimeValue("0:00:01")
 Kill PltFile
 Kill Yldfile
Worksheets("Main").Range("b16") = (RunOutputLine - 3) / NumRuns
Next Spacing, Depth

 Worksheets("DMOutput").Range("Ndepths") = Ndepths
 Worksheets("DMOutput").Range("NSpacings") = NSpacings
 RunUndrained dmExe          ' baseline yields for the economic analysis



Worksheets("Main").Range("b16") = ""
If Sheets("New Flow File").Range("DWMFlag") = 1 Then
  Sheets("New Flow File").Range("intWTMFlag") = 1
  RunDWM
End If



With Worksheets("main")
If Soy = 0 Then

 .Shapes("TextBox 29").Visible = False
 .Opt_Soy_SDI_excess.Visible = False
 .Opt_Soy_Stress_Drought.Visible = False
 .Opt_Soy_Excess.Visible = False
 .Opt_Soy_Drought.Visible = False
 .Opt_Soy_Overall_Yield.Visible = False
 .Shapes("rectangle 6").Visible = False
 .Shapes("rectangle 5").Width = 214
 '.Shapes("textbox 3").Width = 212
 
 'Resizing parameter tabs for continuous corn
 .Opt_SDI_Excess.Width = 218.625
 .Opt_Stress_Drought.Width = 218.625
 .Opt_Excess.Width = 218.625
 .Opt_Drought.Width = 218.625
 .Opt_Overall_Yield.Width = 218.625
 
 
Else
 .Shapes("TextBox 29").Visible = True
 .Opt_Soy_SDI_excess.Visible = True
 .Opt_Soy_Stress_Drought.Visible = True
 .Opt_Soy_Excess.Visible = True
 .Opt_Soy_Drought.Visible = True
 .Opt_Soy_Overall_Yield.Visible = True
 .Shapes("rectangle 6").Visible = True
 .Shapes("rectangle 5").Width = 107
'.Shapes("textbox 3").Width = 104
'Resizing parameter tabs
.Opt_SDI_Excess.Width = 106.5
.Opt_Stress_Drought.Width = 106.5
.Opt_Excess.Width = 106.5
.Opt_Drought.Width = 106.5
.Opt_Overall_Yield.Width = 106.5

End If
End With

 Worksheets("Main").Activate
 Call ReportGraphs
 Worksheets("Main").Opt_Infiltration.Value = True
 Worksheets("Main").Opt_Drained_Vol.Value = True
 Application.StatusBar = False
 Exit Sub

OnlineFailed:
 Application.StatusBar = False
 MsgBox "Could not prepare the online weather/soil inputs:" & vbLf & vbLf & Err.Description & vbLf & vbLf & _
   "Check the site latitude/longitude, the internet connection, and the DRAINMOD folder.", vbExclamation, "DRAINMOD inputs"
End Sub


Function InputState(StateName As String)
Dim Counties As String, RCounty As Range, RSoil As Range
Dim i As Integer


Counties = StateName + "Counties"
Soils = StateName + "Soils"


Set RCounty = Worksheets("Counties").Range(Counties)
ComboBox1.Clear
For i = 1 To RCounty.Count
   ComboBox1.AddItem (RCounty(i))
Next i
ComboBox1.ListIndex = 0
Worksheets("DMData").Range("CountyName") = RCounty(1)
Worksheets("New Flow File").Range("RainfallFileName") = "(downloaded when the simulation runs)"
Worksheets("New Flow File").Range("TemperatureFileName") = "(downloaded when the simulation runs)"
  
Set RSoil = Worksheets("Soils").Range(Soils)
ComboBox2.Clear
For i = 1 To RSoil.Count / 2
   ComboBox2.AddItem (RSoil(i, 1))
Next i
ComboBox2.ListIndex = 0
If StateName = "Illinois" Then
  Worksheets("New Flow File").Range("SoilFileName") = RSoil(1, 2)
Else
  Worksheets("New Flow File").Range("SoilFileName") = Trim$(RSoil(1, 1))
End If


Worksheets("New Flow File").Range("StateName") = StateName

End Function



Private Sub OptionButton1_Click()
If OptionButton1.Value = True Then
  InputState ("Illinois")
End If
End Sub
Private Sub OptionButton2_Click()
If OptionButton2.Value = True Then
  InputState ("Indiana")
End If
End Sub
Private Sub OptionButton3_Click()
If OptionButton3.Value = True Then
  InputState ("Iowa")
End If
End Sub
Private Sub OptionButton4_Click()
If OptionButton4.Value = True Then
  InputState ("Michigan")
End If
End Sub
Private Sub OptionButton5_Click()
If OptionButton5.Value = True Then
  InputState ("Minnesota")
End If
End Sub
Private Sub OptionButton6_Click()
If OptionButton6.Value = True Then
  InputState ("Missouri")
End If
End Sub
Private Sub OptionButton7_Click()
If OptionButton7.Value = True Then
  InputState ("North_Dakota")
End If
End Sub
Private Sub OptionButton8_Click()
If OptionButton8.Value = True Then
  InputState ("Ohio")
End If
End Sub
Private Sub OptionButton9_Click()
If OptionButton9.Value = True Then
  InputState ("South_Dakota")
End If
End Sub
Private Sub OptionButton10_Click()
If OptionButton10.Value = True Then
  InputState ("Wisconsin")
End If
End Sub
Private Sub OptionButton11_Click()
If OptionButton11.Value = True Then
  InputState ("Other")
End If
End Sub
