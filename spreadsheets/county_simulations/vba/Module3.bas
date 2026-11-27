Attribute VB_Name = "Module3"
Private Sub WTmanagement()
'this program adjusts the weir in the crop files by first reading the crop file,
'making the adjustments then printing the modified crop as something else
Dim WeirSet, endofyear, beginofyear, middle As String
Dim junk$
Dim RaiseMonth, LowerMonth, RaiseMonthG, LowerMonthG, k As Integer


RaiseMonth = Sheets("New Flow File").Range("FRaiseMonth")
RaiseMonthG = Sheets("New Flow File").Range("GRaiseMonth")
LowerMonth = Sheets("New Flow File").Range("FLowerMonth")
LowerMonthG = Sheets("New Flow File").Range("GLowerMonth")

RaiseWTday = Sheets("New Flow File").Range("FRaiseDay")
RaiseDayG = Sheets("New Flow File").Range("GRaiseDay")
LowerWTday = Sheets("New Flow File").Range("FLowerDay")
LowerDayG = Sheets("New Flow File").Range("GLowerDay")

WTdepth = Int(Sheets("New Flow File").Range("FRaiseDepth") * 2.54)
WTDG = Int(Sheets("New Flow File").Range("GRaiseDepth") * 2.54)

chkFallow = Sheets("New Flow File").Range("chkFallow")
intCropflag = Sheets("New Flow File").Range("intCropflag")

'READS original crop files and writes new ones
CurPath = Sheets("New Flow File").Range("CurrentPath")

If intCropflag = 1 Then Crop = CurPath & "\crops\corn.cin"
If intCropflag = 2 Then Crop = CurPath & "\crops\soybeans.cin"
If intCropflag = 3 Then Crop = CurPath & "\crops\corn.cin"

1310 Open Crop For Input As #12
   wtCROP = Crop
   Length = InStrRev(Crop, "\")
   Length = Length + 1
   wtCROP = Mid(wtCROP, Length, 30)    'Removes the path from the file name
   NameLength = Len(wtCROP)
   Place = NameLength - 4
   wtCROP = Mid(wtCROP, 1, Place)      'Removes extension from the file name
   wtCROP = CurPath & "\crops\" & wtCROP & "WT.cin"
   Open wtCROP For Output As #13


'If chkFallow = 0 Then
'   If RaiseWTmonth = LowerWTmonth Or RaiseMonthG = LowerMonthG Or LowerWTmonth = RaiseMonthG Or RaiseWTmonth = LowerMonthG Then
 '      MsgBox "You cannot Raise and Lower the weir in the same month.", vbOKOnly, "Error"
'       Exit Sub
'   End If
'End If
'   If RaiseWTmonth = LowerWTmonth Or LowerWTmonth = RaiseMonthG Or RaiseWTmonth = LowerMonthG Then
'       MsgBox "You cannot Raise and Lower the weir in the same month.", vbOKOnly, "Error"
 '      Exit Sub
 '  End If

    
'Reads crop file then replaces water table depth line
   For N = 1 To 28
       Line Input #12, junk$
       If N = 5 Then
           If RaiseMonth > LowerMonth Then
               For k = 1 To 12
                   If k >= RaiseMonth And k < 12 Then Print #13, Format(RaiseWTday, "@@"); Format(WTdepth, "@@@");
                   If k < LowerMonth Then Print #13, Format(RaiseWTday, "@@"); Format(WTdepth, "@@@");
                   If k = LowerMonth Then Print #13, Format(LowerWTday, "@@"); "200";
                   If k > LowerMonth And k < RaiseMonth Then
                       If chkFallow = 0 Then
                           If k >= RaiseMonthG And k < LowerMonthG Then
                               Print #13, Format(RaiseDayG, "@@"); Format(WTDG, "@@@");
                           Else
                               Print #13, Format(LowerDayG, "@@"); "200";
                           End If
                       Else
                           Print #13, Format(LowerWTday, "@@"); "200";
                       End If
                   End If
                   If k = 12 Then Print #13, Format(RaiseWTday, "@@"); Format(WTdepth, "@@@")
               Next k
           End If
           If LowerMonth > RaiseMonth Then
               For k = 1 To 12
                   If k >= LowerMonth And k < 12 Then
                       If chkFallow = 0 Then
                           If k >= RaiseMonthG And k < LowerMonthG Then
                               Print #13, Format(RaiseDayG, "@@"); Format(WTDG, "@@@");
                           Else
                               Print #13, Format(LowerDayG, "@@"); "200";
                           End If
                       Else
                           Print #13, Format(LowerWTday, "@@"); "200";
                       End If
                   End If
                   If k < RaiseMonth Then Print #13, Format(RaiseWTday, "@@"); "200";
                   If k >= RaiseMonth And k < LowerMonth Then Print #13, Format(RaiseWTday, "@@"); Format(WTdepth, "@@@");
                   If k = 12 Then Print #13, Format(LowerWTday, "@@"); "200"
               Next k
           End If
       Else
           Print #13, junk$
       End If
   Next N
   If intCropflag = 3 And Crop <> CurPath & "\crops\soybeans.cin" Then
       Crop = CurPath & "\crops\soybeans.cin"
       Close #12
       Close #13
       GoTo 1310
   End If
   Close #12
   Close #13
   
End Sub

Sub RunDWM()
  Dim RetVal As Long, InFile As String, plotp(30) As Single
  Dim wsh As Object
Set wsh = VBA.CreateObject("WScript.Shell")
  'On Error Resume Next
 Dim dmExe As String
 dmExe = DMHydroPath()
 If dmExe = "" Then
    MsgBox "DRAINMOD cannot run until you select the DRAINMOD hydrology program (DMhydro.exe).", vbExclamation, "DRAINMOD"
    Exit Sub
 End If
 
 WTmanagement
 Weather
 Soilss
 'SetDrainParameters
 
 
SpacingStart = Sheets("New Flow File").Range("DrainSpace1")
SpacingEnd = Sheets("New Flow File").Range("DrainSpace2")
SpacingInterval = Sheets("New Flow File").Range("DrainSpaceInt")
DepthStart = Sheets("New Flow File").Range("DrainDepth1")
DepthEnd = Sheets("New Flow File").Range("DrainDepth2")
DepthInterval = Sheets("New Flow File").Range("DrainDepthInt")
Worksheets("DwMOutput").Range("c4:af200").ClearContents
 NumSpace = (SpacingEnd - SpacingStart) / SpacingInterval + 1
 NumDepths = (DepthEnd - DepthStart) / DepthInterval + 1
 NumRuns = NumSpace * NumDepths
 RunOutputLine = 3: Ndepths = 0
  Worksheets("New Flow File").Visible = False
  'Worksheets("main").Activate
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
Worksheets("DWMOutput").Cells(RunOutputLine, 3) = Depth
Worksheets("DWMOutput").Cells(RunOutputLine, 4) = Spacing
For ig = 0 To 27
  If Soy = 0 And ig > 17 Then GoTo 100
  Worksheets("DWMOutput").Cells(RunOutputLine, ig + 5) = plotp(ig)
100 Next ig

 Application.Wait Now + TimeValue("0:00:01")
 Kill PltFile
 Kill Yldfile
Worksheets("Main").Range("b16") = (RunOutputLine - 3) / NumRuns
Next Spacing, Depth

 Worksheets("DWMOutput").Range("Ndepths") = Ndepths
 Worksheets("DWMOutput").Range("NSpacings") = NSpacings

Worksheets("Main").Range("b16") = ""
 Worksheets("Main").Activate
 
 Worksheets("Main").Opt_Infiltration.Value = True
 Worksheets("Main").Opt_Drained_Vol.Value = True
End Sub
Public Sub plotgraphDWM(YRange)
Dim XRange As Range, Ndepths As Integer, NSpacings As Integer
Dim cht As Chart, xdata As Range, ydata As Range, graphrange As Range, Frange As Range


Set cht = Worksheets("Main").ChartObjects("Chart 11").Chart
Set XRange = Worksheets("DwMOutput").Range("Spacing")
Set Frange = Worksheets("DwMOutput").Range("Depthft")

cht.Axes(xlCategory, xlPrimary).HasTitle = True
'cht.ChartTitle.text = yrange(1).Value
cht.Axes(xlCategory, xlPrimary).AxisTitle.Characters.Text = XRange(1)
cht.Axes(xlValue, xlPrimary).AxisTitle.Characters.Text = YRange(1)


Ndepths = Worksheets("DMOutput").Range("Ndepths")
NSpacings = Worksheets("DMOutput").Range("NSpacings")

For N = 1 To Ndepths
    v1 = 2 + (N - 1) * NSpacings: v2 = v1 + NSpacings - 1

    Set xdata = Worksheets("DwMOutput").Range(XRange(v1), XRange(v2))
    Set ydata = Worksheets("DwMOutput").Range(YRange(v1), YRange(v2))
    
  
      Worksheets("DMOutput").Range("b11") = Frange(v1)
      
      With cht.SeriesCollection.NewSeries
        .xvalues = xdata
        .Name = Frange(v1) & "*"
        .Values = ydata
      End With
  


Next N


End Sub

Public Sub ReportGraphs()

Call plotgraphR(Worksheets("DMOutput").Range("H3:H103"), "Chart 3")
If (Worksheets("New Flow File").Range("DWMFlag")) = 1 Then Call plotgraphRDWM(Worksheets("DWMOutput").Range("H3:H103"), "Chart 3")

Call plotgraphR(Worksheets("DMOutput").Range("Yield_Overall"), "Chart 4")
If (Worksheets("New Flow File").Range("DWMFlag")) = 1 Then Call plotgraphRDWM(Worksheets("DWMOutput").Range("v3:v103"), "Chart 4")

Call plotgraphR(Worksheets("DMOutput").Range("Soy_Overall_Yield"), "Chart 6")
If (Worksheets("New Flow File").Range("DWMFlag")) = 1 Then Call plotgraphRDWM(Worksheets("DWMOutput").Range("af3:af103"), "Chart 6")


End Sub


Public Sub plotgraphRDWM(YRange, CrtName)
Dim XRange As Range, Ndepths As Integer, NSpacings As Integer
Dim cht As Chart, xdata As Range, ydata As Range, graphrange As Range, Frange As Range


Set cht = Worksheets("Report").ChartObjects(CrtName).Chart
Set XRange = Worksheets("DwMOutput").Range("Spacing")
Set Frange = Worksheets("DwMOutput").Range("Depthft")

cht.Axes(xlCategory, xlPrimary).HasTitle = True
'cht.ChartTitle.text = yrange(1).Value
cht.Axes(xlCategory, xlPrimary).AxisTitle.Characters.Text = XRange(1)
cht.Axes(xlValue, xlPrimary).AxisTitle.Characters.Text = YRange(1)


Ndepths = Worksheets("DMOutput").Range("Ndepths")
NSpacings = Worksheets("DMOutput").Range("NSpacings")

For N = 1 To Ndepths
    v1 = 2 + (N - 1) * NSpacings: v2 = v1 + NSpacings - 1

    Set xdata = Worksheets("DwMOutput").Range(XRange(v1), XRange(v2))
    Set ydata = Worksheets("DwMOutput").Range(YRange(v1), YRange(v2))
    
  
      Worksheets("DMOutput").Range("b11") = Frange(v1)
      
      With cht.SeriesCollection.NewSeries
        .xvalues = xdata
        .Name = Frange(v1) & "*"
        .Values = ydata
      End With
  


Next N


End Sub
