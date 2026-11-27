Attribute VB_Name = "Module1"
#If VBA7 Then
    Private Declare PtrSafe Sub Sleep Lib "kernel32" (ByVal ms As LongPtr)
#Else
    Private Declare Sub Sleep Lib "kernel32" (ByVal ms As Long)
#End If

Public Sub plotgraph(YRange)
Dim XRange As Range, Ndepths As Integer, NSpacings As Integer
Dim cht As Chart, xdata As Range, ydata As Range, graphrange As Range, Frange As Range

With Worksheets("DMOutput")

Set cht = Worksheets("Main").ChartObjects("Chart 11").Chart
Set XRange = .Range("Spacing")
Set Frange = .Range("Depthft")

cht.Axes(xlCategory, xlPrimary).HasTitle = True
'cht.ChartTitle.text = yrange(1).Value
cht.Axes(xlCategory, xlPrimary).AxisTitle.Characters.Text = XRange(1)
cht.Axes(xlValue, xlPrimary).AxisTitle.Characters.Text = YRange(1)


Ndepths = Worksheets("DMOutput").Range("Ndepths")
NSpacings = Worksheets("DMOutput").Range("NSpacings")

For N = 1 To Ndepths
    v1 = 2 + (N - 1) * NSpacings: v2 = v1 + NSpacings - 1

    Set xdata = .Range(XRange(v1), XRange(v2))
    Set ydata = .Range(YRange(v1), YRange(v2))
    
   If N = 1 Then
    
    Set graphrange = Union(xdata, ydata)
    cht.SetSourceData Source:=graphrange
    cht.SeriesCollection(1).Name = Frange(v1)
   Else
      .Range("b11") = Frange(v1)
      
      With cht.SeriesCollection.NewSeries
        .xvalues = xdata
        .Name = Frange(v1)
        .Values = ydata
      End With
    End If


Next N

End With
End Sub

Public Sub plotgraphR(YRange, CrtName)
Dim XRange As Range, Ndepths As Integer, NSpacings As Integer
Dim cht As Chart, xdata As Range, ydata As Range, graphrange As Range, Frange As Range

With Worksheets("DMOutput")

Set cht = Worksheets("Report").ChartObjects(CrtName).Chart
Set XRange = .Range("Spacing")
Set Frange = .Range("Depthft")

cht.Axes(xlCategory, xlPrimary).HasTitle = True
'cht.ChartTitle.text = yrange(1).Value
cht.Axes(xlCategory, xlPrimary).AxisTitle.Characters.Text = XRange(1)
cht.Axes(xlValue, xlPrimary).AxisTitle.Characters.Text = YRange(1)


Ndepths = Worksheets("DMOutput").Range("Ndepths")
NSpacings = Worksheets("DMOutput").Range("NSpacings")

For N = 1 To Ndepths
    v1 = 2 + (N - 1) * NSpacings: v2 = v1 + NSpacings - 1

    Set xdata = .Range(XRange(v1), XRange(v2))
    Set ydata = .Range(YRange(v1), YRange(v2))
    
   If N = 1 Then
    
    Set graphrange = Union(xdata, ydata)
    cht.SetSourceData Source:=graphrange
    cht.SeriesCollection(1).Name = Frange(v1)
   Else
      .Range("b11") = Frange(v1)
      
      With cht.SeriesCollection.NewSeries
        .xvalues = xdata
        .Name = Frange(v1)
        .Values = ydata
      End With
    End If


Next N

End With
End Sub





Function FileExists(ByVal FileToTest As String) As Boolean
   FileExists = (Dir(FileToTest) <> "")
End Function
Sub DeleteFile(ByVal FileToDelete As String)
   If FileExists(FileToDelete) Then 'See above
      SetAttr FileToDelete, vbNormal
      Kill FileToDelete
   End If
End Sub
Function strz(DesignAmount, DesignLabel) As String
Dim STV As String
STV = Str(DesignAmount)
STV = Right(STV, Len(STV) - 1)
If DesignLabel = "Return Period (Years)" Then
   strz = STV + "-year, 24 hour flow"
Else
   strz = STV + " % of maximum tile flow"
End If
End Function
Function Strl(A_Number) As String
Dim STV As String
STV = Str(A_Number)
Strl = "<=" + Right(STV, Len(STV) - 1)
End Function

Function StrEQ(A_Number) As String
Dim STV As String
STV = Str(A_Number)
StrEQ = "=" + Right(STV, Len(STV) - 1)
End Function

Function StrMinus1(A_Number) As String
Dim STV As String
STV = Str(A_Number)
StrMinus1 = "_" + Right(STV, Len(STV) - 1)
End Function





Function ResetDesignPeriod()


'Dim i As Integer

StateName = Worksheets("New Flow File").Range("StateName")
Counties = StateName + "Counties"
Set RCounty = Worksheets("Counties").Range(Counties)
Worksheets("New Flow File").ComboBox1.Clear
For i = 1 To RCounty.Count
   Worksheets("New Flow File").ComboBox1.AddItem (RCounty(i))
Next i
Worksheets("New Flow File").ComboBox1.ListIndex = 0
i = 1
Worksheets("DMData").Range("CountyName") = RCounty(i)
Worksheets("New Flow File").Range("RainfallFileName") = "(downloaded when the simulation runs)"
Worksheets("New Flow File").Range("TemperatureFileName") = "(downloaded when the simulation runs)"

End Function


Function SerialDMDay(DMDate As Single) As Date
Dim TotalDate As String, dday As Date
TotalDate = "1/1/" + Str(Int(DMDate))
dday = CInt((1000 * DMDate Mod 1000) / 1000 * 365)
serialmday = TotalDate + dday
SerialDMDay = serialmday
End Function


Function GetFolder(strPath As String) As String

Dim fldr As FileDialog
Dim sItem As String
ChDir "C:\"
Set fldr = Application.FileDialog(msoFileDialogFolderPicker)
With fldr
    .Title = "Select the DRAINMOD folder (contains the crops, inputs and outputs folders)"
    .AllowMultiSelect = False
    .InitialFileName = strPath
    If .Show <> -1 Then GoTo NextCode
    sItem = .SelectedItems(1)
End With
NextCode:
GetFolder = sItem
Set fldr = Nothing
End Function



Public Sub PROJC()

Dim strSinFile As String, StrSoil As String
Dim strRaiFile As String, CurPath As String
Dim strTemFile As String, InFile As String
Dim strGen As String

InFile = "Run"
CurPath = Sheets("New Flow File").Range("CurrentPath")
StrSoil = Sheets("New Flow File").Range("SoilFileName")
StateName = Sheets("New Flow File").Range("StateName")
RainName = Sheets("New Flow File").Range("RainfallFileName")
tempName = Sheets("New Flow File").Range("TemperatureFileName")
intWTMflag = Sheets("New Flow File").Range("IntWTMFlag")
intCropflag = Sheets("New Flow File").Range("IntCropflag")

    strProjFile = CurPath & "\inputs\" & InFile & 0 & ".prj"
    strGenFile = CurPath & "\inputs\" & InFile & 0 & ".gen"

Worksheets("DMData").Range("strProjFile") = strProjFile

strSinFile = StrSoil & ".sin"
strMisFile = StrSoil & ".mis"
strWdvFile = StrSoil & ".wdv"

Open strProjFile For Output As #6

Print #6, "[Options]"
Print #6, "Project=0"


Print #6, "Yield=1"
Print #6, "PET=1"
Print #6, "CREAMS=0"
Print #6, "Runoff=0"
Print #6, "SoilTemp=1"
Print #6, "[General]"
strGen = "Hydrology=" & strGenFile
Print #6, strGen
Print #6, "Runoff="
Print #6, "FieldRatio=0"
Print #6, "Nitrogen="; CurPath & "\" & StateName & "\inputs\"; InFile; ".dmn"; ""
Print #6, "Salinity="
Print #6, "[Soils]"
Print #6, "SoilFile="; Sheets("DMData").Range("SinPath"); ""
Print #6, "SoilWater="; Sheets("DMData").Range("MisPath"); ""
Print #6, "VolDrained="; Sheets("DMData").Range("WdvPath"); ""
Print #6, "[Weather]"
Print #6, "Rainfall="; Sheets("DMData").Range("RaiPath"); ""
Print #6, "Temperature="; Sheets("DMData").Range("TemPath"); ""
Print #6, "PET="
Print #6, "[Crops]"
If intCropflag = 1 Then
    Print #6, "NumCrop=1"
    If intWTMflag = 0 Then
        Print #6, "Crop1="; CurPath & "\crops\corn.cin"; ""
    Else
        Print #6, "Crop1="; CurPath & "\crops\cornWT.cin"; ""
    End If
End If

If intCropflag = 3 Then
    Print #6, "NumCrop=2"
    If intWTMflag = 0 Then
        Print #6, "Crop1="; CurPath & "\crops\corn.cin"; ""
        Print #6, "Crop2="; CurPath & "\crops\soybeans.cin"; ""
    Else
        Print #6, "Crop1="; CurPath & "\crops\cornWT.cin"; ""
        Print #6, "Crop2="; CurPath & "\crops\soybeansWT.cin"; ""
    End If
End If
Close #6

End Sub


Public Sub GENC()

Dim Temp(100), Theta(100)
'Dim strEffectiveDepth As String, strW As String, strDrainSpacing As String
'Dim strDrainDepth As String
InFile = "Run"
CurPath = Sheets("New Flow File").Range("CurrentPath")
StrSoil = Sheets("New Flow File").Range("SoilFileName")
StateName = Sheets("New Flow File").Range("StateName")
County = Sheets("DMData").Range("CountyName")
RainName = Sheets("New Flow File").Range("RainfallFileName")
tempName = Sheets("New Flow File").Range("TemperatureFileName")
intWTMflag = Sheets("New Flow File").Range("IntWTMFlag")
intCropflag = Sheets("New Flow File").Range("IntCropflag")

If intCropflag = 3 Then strSelectedCrop = "Corn/Soybean"
If intCropflag = 1 Then strSelectedCrop = "Continuous Corn"

    strGenFile = CurPath & "\inputs\" & InFile & 0 & ".gen"
    


'Call Weather

strWeather1 = Sheets("DMData").Range("strWeather1")
strWeather2 = Sheets("DMData").Range("strWeather2")
strWeather3 = Sheets("DMData").Range("strWeather3")
strPET = Sheets("DMData").Range("strPET")
flNum = FreeFile
Open strGenFile For Output As #flNum

Print #flNum, "*** Job Title ***"
Print #flNum, County; " - "; StrSoil; " - "; strSelectedCrop
Print #flNum, strGenFile
Print #flNum, "*** Printout and Input Control ***"
Print #flNum, " 1 101 "; CurPath & "\outputs"; ""
Print #flNum, "*** Climate ***"
Print #flNum, strWeather1
Print #flNum, strWeather2
Print #flNum, strWeather3
Print #flNum, strPET
Print #flNum, "*** Drainage System Design ***"; Tab(77); ""
If intWTMflag = 1 Then
    Print #flNum, " 2"
Else
    Print #flNum, " 1"
End If

'Call Soilss
Call DrainageDesign

strDrainage1 = Sheets("DMData").Range("strDrainage1")
Print #flNum, strDrainage1

Print #flNum, "           0    0.000000E+00    0.000000E+00"
If Worksheets("New Flow File").Range("ChkDeepSeepage") = 1 Then
    Print #flNum, Spc(11); "1    "; Format(Worksheets("New Flow File").Range("PiezHead"), "scientific"), Spc(4); Format(Worksheets("New Flow File").Range("RestrictThick"), "scientific"), Spc(4); Format(Worksheets("New Flow File").Range("RestrictCond"), "scientific")
Else
    Print #flNum, "           0    0.000000E+00    0.000000E+00    0.000000E+00"
End If
Print #flNum, "           0    0.000000E+00    0.000000E+00    0.000000E+00    0.000000E+00"
Print #flNum, "      1.00       .10       .00"
Print #flNum, " 1160 1160 1160 1160 1160 1160 1160 1160 1160 1160 1160 1160"
Print #flNum, "*** Soils ***"




strSoils1 = Sheets("DMData").Range("strSoils1")
strSoils2 = Sheets("DMData").Range("strSoils2")
strT1 = Sheets("DMData").Range("strT1")
strT2 = Sheets("DMData").Range("strT2")
strT3 = Sheets("DMData").Range("strT3")
strT4 = Sheets("DMData").Range("strT4")
strT5 = Sheets("DMData").Range("strT5")
strT6 = Sheets("DMData").Range("strT6")
strT7 = Sheets("DMData").Range("strT7")
strT8 = Sheets("DMData").Range("strT8")
strT9 = Sheets("DMData").Range("strT9")
strT10 = Sheets("DMData").Range("strT10")
strT11 = Sheets("DMData").Range("strT11")


Print #flNum, strSoils1
Print #flNum, strSoils2
Print #flNum, "99     .00"
Print #flNum, "*** Traficability ***"
Print #flNum, " 415 9 1 814               2.0       2.0       1.0"
Print #flNum, "12311231 820               2.0       2.0       1.0"
If intCropflag = 1 Or 3 Then
    Print #flNum, "*** Crop ***"
    Print #flNum, "      .175"
    Print #flNum, " 4101030       30.00"
    Print #flNum, " 4101030"
    Print #flNum, "7"
    Print #flNum, " 1 1  4.00 5 1  7.00 630 80.00 716 90.00 831 90.0010 1  7.001231  4.00"
Else
    Print #flNum, "*** Crop ***"
    Print #flNum, "      .175"
    Print #flNum, " 5 1 915       30.00"
    Print #flNum, " 5 1 915"
    Print #flNum, "7"
    Print #flNum, " 1 1  4.00 5 1  7.00 630 80.00 716 90.00 831 90.0010 1  7.001231  4.00"
End If
Print #flNum, "*** Wastewater Irrigation ***"
Print #flNum, " 0    0 0 365   0  0"
Print #flNum, " 0 0       0 0       0 0       0 0"
Print #flNum, "    .00000    .00000  .00  .00  .00  .00  .00  .00  .00  .00  .00  .00  .00  .00"
Print #flNum, "WET *** Wetlands Information ***"
Print #flNum, "0"
Print #flNum, "   1 365"
Print #flNum, " 30.0  14"
Print #flNum, "COM *** Combo Drainage Weir Settings ***"
Print #flNum, " 0  0  0    .0"
Print #flNum, " 0  0  0    .0"
Print #flNum, " 0  0  0    .0"
Print #flNum, " 0  0  0    .0"
Print #flNum, " 0  0  0    .0"
Print #flNum, " 0  0  0    .0"
Print #flNum, " 0  0  0    .0"
Print #flNum, " 0  0  0    .0"
Print #flNum, " 0  0  0    .0"
Print #flNum, " 0  0  0    .0"
Print #flNum, " 0  0  0    .0"
Print #flNum, " 0  0  0    .0"
Print #flNum, " 0  0  0    .0"
Print #flNum, " 0  0  0    .0"
Print #flNum, " 0  0  0    .0"
Print #flNum, " 0  0  0    .0"
Print #flNum, " 0  0  0    .0"
Print #flNum, " 0  0  0    .0"
Print #flNum, " 0  0  0    .0"
Print #flNum, " 0  0  0    .0"
Print #flNum, " 0  0  0    .0"
Print #flNum, " 0  0  0    .0"
Print #flNum, " 0  0  0    .0"
Print #flNum, " 0  0  0    .0"
Print #flNum, "FPE *** Fixed Avg Daily PET for the month(cm) ***"
Print #flNum, "    1.00    1.00    1.00    1.00    1.00    1.00    1.00    1.00    1.00    1.00    1.00    1.00"
Print #flNum, "MRA *** Monthly Ranking ***"
Print #flNum, "0"
Print #flNum, "FAC *** Daily PET Factors ***"
Print #flNum, "0"
Print #flNum, "STM *** Soil Temperature ***"
Print #flNum, "      ZA      ZB     TKA     TKB      TB    TLAG   TSNOW   TMELT    CDEG    CICE"
Print #flNum, "   1.200   1.168    .260   1.248    10.0     8.0    -1.1     2.0     6.0      .2"
Print #flNum, "Initial Soil Temperature"
Print #flNum, "2"
Print #flNum, "    .0   -4.40"
Print #flNum, " 500.0   10.00"
Print #flNum, "Initial snow depth(m) & density(kg/m3)"
Print #flNum, "      .00   100.00"
Print #flNum, "Freezing characteristic curve"
Print #flNum, "11"
Print #flNum, strT1
Print #flNum, strT2
Print #flNum, strT3
Print #flNum, strT4
Print #flNum, strT5
Print #flNum, strT6
Print #flNum, strT7
Print #flNum, strT8
Print #flNum, strT9
Print #flNum, strT10
Print #flNum, strT11

Close #flNum

End Sub

Public Sub Weather()
'Builds (or reuses) the DRAINMOD weather files for the site and the selected
'time period from online data (Daymet observed or NEX-GDDP-CMIP6 simulated),
'then sets the climate lines of the .gen file.

Dim latt As Single, longt As Single, pvp As Single, siteLat As Double, siteLon As Double
Dim r11$, t11$, latitude As String
Dim HIndex As Range, PEvap As Range, LatRange As Range, LongRange As Range
Dim MNt01 As Range, mnt02 As Range, mnt03 As Range, mnt04 As Range, mnt05 As Range, mnt06 As Range
Dim mnt07 As Range, mnt08 As Range, mnt09 As Range, mnt10 As Range, mnt11 As Range, mnt12 As Range

County = Sheets("DMData").Range("CountyName")

' ---- download / reuse the .RAI and .TEM files
EnsureWeatherFiles
GetSite siteLat, siteLon
latitude = LatDDMM(siteLat)

' monthly PET correction factors for the period (Temperature Data sheet)
If Worksheets("Main").Range("ClmCheck") = 0 Then
   Set MNt01 = Worksheets("Temperature data").Range("Jan1980sim")
   Set mnt02 = Worksheets("Temperature data").Range("Feb1980sim")
   Set mnt03 = Worksheets("Temperature data").Range("Mar1980sim")
   Set mnt04 = Worksheets("Temperature data").Range("Apr1980sim")
   Set mnt05 = Worksheets("Temperature data").Range("May1980sim")
   Set mnt06 = Worksheets("Temperature data").Range("Jun1980sim")
   Set mnt07 = Worksheets("Temperature data").Range("Jul1980sim")
   Set mnt08 = Worksheets("Temperature data").Range("Aug1980sim")
   Set mnt09 = Worksheets("Temperature data").Range("Sep1980sim")
   Set mnt10 = Worksheets("Temperature data").Range("Oct1980sim")
   Set mnt11 = Worksheets("Temperature data").Range("Nov1980sim")
   Set mnt12 = Worksheets("Temperature data").Range("Dec1980sim")
ElseIf Worksheets("Main").Range("ClmCheck") = 3 Then
   Set MNt01 = Worksheets("Temperature data").Range("Jan1980obs")
   Set mnt02 = Worksheets("Temperature data").Range("Feb1980obs")
   Set mnt03 = Worksheets("Temperature data").Range("Mar1980obs")
   Set mnt04 = Worksheets("Temperature data").Range("Apr1980obs")
   Set mnt05 = Worksheets("Temperature data").Range("May1980obs")
   Set mnt06 = Worksheets("Temperature data").Range("Jun1980obs")
   Set mnt07 = Worksheets("Temperature data").Range("Jul1980obs")
   Set mnt08 = Worksheets("Temperature data").Range("Aug1980obs")
   Set mnt09 = Worksheets("Temperature data").Range("Sep1980obs")
   Set mnt10 = Worksheets("Temperature data").Range("Oct1980obs")
   Set mnt11 = Worksheets("Temperature data").Range("Nov1980obs")
   Set mnt12 = Worksheets("Temperature data").Range("Dec1980obs")
ElseIf Worksheets("Main").Range("ClmCheck") = 2 Then
   Set MNt01 = Worksheets("Temperature data").Range("Jan2070sim")
   Set mnt02 = Worksheets("Temperature data").Range("Feb2070sim")
   Set mnt03 = Worksheets("Temperature data").Range("Mar2070sim")
   Set mnt04 = Worksheets("Temperature data").Range("Apr2070sim")
   Set mnt05 = Worksheets("Temperature data").Range("May2070sim")
   Set mnt06 = Worksheets("Temperature data").Range("Jun2070sim")
   Set mnt07 = Worksheets("Temperature data").Range("Jul2070sim")
   Set mnt08 = Worksheets("Temperature data").Range("Aug2070sim")
   Set mnt09 = Worksheets("Temperature data").Range("Sep2070sim")
   Set mnt10 = Worksheets("Temperature data").Range("Oct2070sim")
   Set mnt11 = Worksheets("Temperature data").Range("Nov2070sim")
   Set mnt12 = Worksheets("Temperature data").Range("Dec2070sim")
Else
   Set MNt01 = Worksheets("Temperature data").Range("Jan2040sim")
   Set mnt02 = Worksheets("Temperature data").Range("Feb2040sim")
   Set mnt03 = Worksheets("Temperature data").Range("Mat2040sim")
   Set mnt04 = Worksheets("Temperature data").Range("Apr2040sim")
   Set mnt05 = Worksheets("Temperature data").Range("May2040sim")
   Set mnt06 = Worksheets("Temperature data").Range("Jun2040sim")
   Set mnt07 = Worksheets("Temperature data").Range("Jul2040sim")
   Set mnt08 = Worksheets("Temperature data").Range("Aug2040sim")
   Set mnt09 = Worksheets("Temperature data").Range("Sep2040sim")
   Set mnt10 = Worksheets("Temperature data").Range("Oct2040sim")
   Set mnt11 = Worksheets("Temperature data").Range("Nov2040sim")
   Set mnt12 = Worksheets("Temperature data").Range("Dec2040sim")
End If

r11$ = Sheets("DMData").Range("RaiPath")
Call SetRainfile(r11$)
RYear1 = Sheets("DMData").Range("RYear1")
RYear2 = Sheets("DMData").Range("RYear2")
RMonth1 = Sheets("DMData").Range("RMonth1")
RMonth2 = Sheets("DMData").Range("RMonth2")

t11$ = Sheets("DMData").Range("TemPath")
Call SetTemperature(t11$)
TYear1 = Sheets("DMData").Range("TYear1")
TYear2 = Sheets("DMData").Range("TYear2")
TMonth1 = Sheets("DMData").Range("TMonth1")
TMonth2 = Sheets("DMData").Range("TMonth2")

Yearlow = RYear1:
If TYear1 > Yearlow Then
    Yearlow = TYear1
End If

YearHigh = RYear2:
If TYear2 < YearHigh Then
   YearHigh = TYear2
End If

If YearHigh < Yearlow Then
 MsgBox ("Temperature and Rainfall files do not have overlapping years")
End If

MonthHigh = RMonth2:
If TMonth2 < MonthHigh Then
   MonthHigh = TMonth2
End If

' simulation period = the years in the downloaded files
Sheets("DMData").Range("FirstYear") = Yearlow
Sheets("DMData").Range("LastYear") = YearHigh

' PET correction factors: interpolate the regional grid at the site
' (coordinates held inside the grid so sites near its edge still work)
Set LatRange = Worksheets("Temperature data").Range("LatRange")
Set LongRange = Worksheets("Temperature data").Range("LongRange")
latt = siteLat: longt = siteLon
If latt < LatRange(1, 1) Then latt = LatRange(1, 1)
If latt > LatRange(LatRange.Rows.Count, 1) Then latt = LatRange(LatRange.Rows.Count, 1)
If longt < LongRange(1, 1) Then longt = LongRange(1, 1)
If longt > LongRange(1, LongRange.Columns.Count) Then longt = LongRange(1, LongRange.Columns.Count)

' Thornthwaite heat index from the downloaded temperatures
heatindex = HeatIndexFromTem(t11$)
Sheets("DMData").Range("HeatIndex") = heatindex

Worksheets("DMData").Range("Pvap01") = InterpolateXY(longt, latt, LongRange, LatRange, MNt01) + 10
Worksheets("DMData").Range("Pvap02") = InterpolateXY(longt, latt, LongRange, LatRange, mnt02) + 10
Worksheets("DMData").Range("Pvap03") = InterpolateXY(longt, latt, LongRange, LatRange, mnt03) + 10
Worksheets("DMData").Range("Pvap04") = InterpolateXY(longt, latt, LongRange, LatRange, mnt04) + 10
Worksheets("DMData").Range("Pvap05") = InterpolateXY(longt, latt, LongRange, LatRange, mnt05) + 10
Worksheets("DMData").Range("Pvap06") = InterpolateXY(longt, latt, LongRange, LatRange, mnt06) + 10
Worksheets("DMData").Range("Pvap07") = InterpolateXY(longt, latt, LongRange, LatRange, mnt07) + 10
Worksheets("DMData").Range("Pvap08") = InterpolateXY(longt, latt, LongRange, LatRange, mnt08) + 10
Worksheets("DMData").Range("Pvap09") = InterpolateXY(longt, latt, LongRange, LatRange, mnt09) + 10
Worksheets("DMData").Range("Pvap10") = InterpolateXY(longt, latt, LongRange, LatRange, mnt10) + 10
Worksheets("DMData").Range("Pvap11") = InterpolateXY(longt, latt, LongRange, LatRange, mnt11) + 10
Worksheets("DMData").Range("Pvap12") = InterpolateXY(longt, latt, LongRange, LatRange, mnt12) + 10

strWeather3 = Str(Yearlow) & "  1" & Str(YearHigh) & Str(MonthHigh) & latitude & "  " & Str(heatindex) & " 0"
strWeather3 = Right(strWeather3, Len(strWeather3) - 1)

Set PEvap = Worksheets("DMData").Range("Pvap")
strPET = ""

For i = 1 To 12
pvp = Format(PEvap(i), "00.00")
kk$ = Str(Round(pvp, 2))
ltt = Len(kk$) - 2
kk$ = Right(kk$, ltt)
If Len(kk$) = 1 Then kk$ = kk$ + ".00"
If Len(kk$) = 3 Then kk$ = kk$ + "0"
strPET = strPET + kk$ + " "
Next i

 Sheets("DMData").Range("strWeather3") = strWeather3
 Sheets("DMData").Range("strPET") = strPET

End Sub

Sub SetRainfile(rnfile$)
    Open rnfile$ For Input As 1
    
      Line Input #1, Rr$
      RID$ = Mid$(Rr$, 1, 6)
      strWeather1 = RID$ & " " & rnfile$
      RYear1 = Val(Mid$(Rr$, 8, 4))
      RMonth1 = Val(Mid$(Rr$, 12, 2))
      
      While Not EOF(1)
        Line Input #1, Rr$
      Wend
      
      RYear2 = Val(Mid$(Rr$, 8, 4))
      RMonth2 = Val(Mid$(Rr$, 12, 2))
      
    Close 1
    
 Sheets("DMData").Range("strWeather1") = strWeather1
 Sheets("DMData").Range("RYear1") = RYear1
 Sheets("DMData").Range("RYear2") = RYear2
 Sheets("DMData").Range("RMonth1") = RMonth1
 Sheets("DMData").Range("RMonth2") = RMonth2
 
End Sub

Sub SetTemperature(tnfile$)
    Open tnfile$ For Input As 1
    
      Line Input #1, T$
      TID$ = Mid$(T$, 1, 6)
      strWeather2 = TID$ & " " & tnfile$
      TYear1 = Val(Mid$(T$, 8, 4))
      TMonth1 = Val(Mid$(T$, 12, 2))
      
      While Not EOF(1)
        Line Input #1, T$
        If InStr(T$, TID$) > 0 Then
          TYear2 = Val(Mid$(T$, 8, 4))
          TMonth2 = Val(Mid$(T$, 12, 2))
        End If
      Wend
      
    Close 1
    
 Sheets("DMData").Range("strWeather2") = strWeather2
 Sheets("DMData").Range("TYear1") = TYear1
 Sheets("DMData").Range("TYear2") = TYear2
 Sheets("DMData").Range("TMonth1") = TMonth1
 Sheets("DMData").Range("TMonth2") = TMonth2

End Sub

Public Sub DrainageDesign()
'This Subprogram creates several lines of the general file by
'opening the corresponding soil file
'and calculating the effective depth and kirkham's G coefficient

Dim EffectiveDepth
Dim alpha, strDrain(9) As String
Dim b, h, D, Sum, p, P10, P11, P12, P13, P14, P18, C1, C2, c, a, Gee, V, L
Dim MSDepth As Single, SSDepth As Single, SDR As Single, Ddepth2 As Single

MSDepth = Sheets("New Flow File").Range("MSDepth")
SSDepth = Sheets("New Flow File").Range("SSDepth")
Ddepth2 = Sheets("New Flow File").Range("DrainDepth2")

If MSDepth < Ddepth2 Then MSDepth = Int(1.1 * Ddepth2) + 1

SDR = MSDepth / SSDepth



ImpLayerDepth = Sheets("DMData").Range("ImpLayerDepth")
ImpLayerDepth = Int(ImpLayerDepth * SDR) + 1
DrainDepth = Sheets("DMData").Range("DrainDepth")
DrainSpace = Sheets("DMData").Range("DrainSpace")
R = 0.51

ActualDepth = ImpLayerDepth - DrainDepth

If ActualDepth / DrainSpace < 0.3 And ActualDepth / DrainSpace > 0 Then
    alpha = 3.55 - 1.6 * ActualDepth / DrainSpace + 2 * (2 / DrainSpace) ^ 2
    EffectiveDepth = ActualDepth / (1 + (ActualDepth / DrainSpace) * (2.546479 * Log(ActualDepth / R) - alpha))
Else
    EffectiveDepth = DrainSpace * 3.141593 / (8 * (Log(DrainSpace / R) - 1.15))
End If

'Kirkham's G calculated using code from old version of DRAINMOD
'variable names used in old version of DRAINMOD
h = ImpLayerDepth
b = DrainDepth
L = DrainSpace

'code from old version of DRAINMOD
Sum = 0: p = 0: D = h - b
P10 = Tan(3.141593 * (2 * D - R) / (4 * h))
P11 = Tan(3.141593 * R / (4 * h))
P13 = Cos(3.141593 * R / (2 * h))
P14 = Cos(3.141593 * (2 * D - R) / 2 / h)
9480    p = p + 1
    P12 = 3.141593 * p * L / (2 * h)
    P18 = (Exp(P12) + Exp(-P12)) / 2
    C1 = (P18 + P13) * (P18 - P14): C2 = (P18 - P13) * (P18 + P14)
    c = Log(C1 / C2)
    Sum = Sum + c
If c >= 0.1 Then GoTo 9480
a = 2 * Log(Abs(P10) / P11)
Gee = a + 2 * Sum
V = Int(Gee * 100) / 100
'end of old DRAINMOD code

strDrainDepth$ = FormatNumber(DrainDepth, 2)
strEffectiveDepth$ = FormatNumber(EffectiveDepth, 2)
strDrainSpacing$ = Format(DrainSpace, "  ####.00")
strSm$ = FormatNumber(1, 2)
strDC$ = FormatNumber(5.3, 2)
strSl$ = FormatNumber(1, 2)
strKirkhamG$ = FormatNumber(V, 2)
strW$ = FormatNumber(100, 2)

strDrain(1) = strDrainDepth$
strDrain(2) = strEffectiveDepth$
strDrain(3) = strDrainSpacing$
strDrain(4) = strSm$
strDrain(5) = strDC$
strDrain(6) = strSl$
strDrain(7) = strKirkhamG$
strDrain(8) = strW$

strDrainage1 = ""
For j = 1 To 8
   For i = 1 To 10 - Len(strDrain(j))
      strDrainage1 = strDrainage1 + " "
   Next i
   strDrainage1 = strDrainage1 + strDrain(j)
Next j
'Sheets("DMData").Range ("")
Sheets("DMData").Range("strDrainage1") = strDrainage1
Sheets("DMData").Range("strDrainDepth") = strDrainDepth$
Sheets("DMData").Range("strEffectiveDepth") = strEffectiveDepth$
Sheets("DMData").Range("strDrainSpacing") = strDrainSpacing$
Sheets("DMData").Range("strSm") = strSm$
Sheets("DMData").Range("strDC") = strDC$
Sheets("DMData").Range("strSl") = strSl$
Sheets("DMData").Range("strKirkhamG") = strKirkhamG$
Sheets("DMData").Range("strW") = strW$


End Sub

Public Sub Soilss()
'Builds (or reuses) the soil files from SSURGO (BuildOnlineSoil writes the
'.soi, .SIN, .MIS and .WDV files), then sets the soil lines of the .gen file:
'impermeable layer depth, five lateral conductivity layers, and the
'freezing characteristic curve (DMData!FreezeCurve).

Dim D(5), DD(5)
Dim k(5)
Dim T(11), O(11), FC As Range
Dim MSDepth As Single, SSDepth As Single, SDR As Single, Ddepth2 As Single

If IllinoisSoilDb() Then
    SoilssDatabase
    Exit Sub
End If

Call BuildOnlineSoil

ImpLayerDepth = Sheets("DMData").Range("OnlineImpDepth")
Sheets("New Flow File").Range("MSDepth") = ImpLayerDepth
If Sheets("New Flow File").Range("ChkSoilDepth") <> 1 Then Sheets("New Flow File").Range("SSDepth") = ImpLayerDepth

MSDepth = Sheets("New Flow File").Range("MSDepth")
SSDepth = Sheets("New Flow File").Range("SSDepth")
Ddepth2 = Sheets("New Flow File").Range("DrainDepth2")

If MSDepth < Ddepth2 Then MSDepth = Int(1.1 * Ddepth2) + 1

Sheets("New Flow File").Range("MSDepth") = MSDepth

SDR = MSDepth / SSDepth

For N = 1 To 5
    D(N) = Sheets("DMData").Range("KLayers").Cells(N, 1)
    k(N) = Sheets("DMData").Range("KLayers").Cells(N, 2)
    DD(N) = Int(D(N) * SDR) + 1
Next N

Set FC = Sheets("DMData").Range("FreezeCurve")
For N = 1 To 11
    T(N) = FC.Cells(N, 1)
    O(N) = FC.Cells(N, 2)
Next N

R = 0.51 'Effective Radius of drain tube (default is .51)

strSoils1 = "    " & Int(ImpLayerDepth * SDR) + 1 & "       " & R
strSoils2 = Format(DD(1), " @@@.") & Format(k(1), "@@@@@") & Format(DD(2), " @@@.") & Format(k(2), "@@@@@") _
            & Format(DD(3), " @@@.") & Format(k(3), "@@@@@") & Format(DD(4), " @@@.") & Format(k(4), "@@@@@") _
            & Format(DD(5), " @@@.") & Format(k(5), "@@@@@")
strSoils2 = Replace(strSoils2, ",", ".")

strT1 = "   " & FormatNumber(T(1), 1) & Format(O(1), "     .000")
strT2 = "  " & FormatNumber(T(2), 1) & Format(O(2), "     .000")
strT3 = "  " & FormatNumber(T(3), 1) & Format(O(3), "     .000")
strT4 = "  " & FormatNumber(T(4), 1) & Format(O(4), "     .000")
strT5 = "  " & FormatNumber(T(5), 1) & Format(O(5), "     .000")
strT6 = "  " & FormatNumber(T(6), 1) & Format(O(6), "     .000")
strT7 = "  " & FormatNumber(T(7), 1) & Format(O(7), "     .000")
strT8 = "  " & FormatNumber(T(8), 1) & Format(O(8), "     .000")
strT9 = "  " & FormatNumber(T(9), 1) & Format(O(9), "     .000")
strT10 = "  " & FormatNumber(T(10), 1) & Format(O(10), "     .000")
strT11 = " " & FormatNumber(T(11), 1) & Format(O(11), "     .000")

 Sheets("DMData").Range("strSoils1") = strSoils1
 Sheets("DMData").Range("strSoils2") = strSoils2
 Sheets("DMData").Range("strT1") = strT1
 Sheets("DMData").Range("strT2") = strT2
 Sheets("DMData").Range("strT3") = strT3
 Sheets("DMData").Range("strT4") = strT4
 Sheets("DMData").Range("strT5") = strT5
 Sheets("DMData").Range("strT6") = strT6
 Sheets("DMData").Range("strT7") = strT7
 Sheets("DMData").Range("strT8") = strT8
 Sheets("DMData").Range("strT9") = strT9
 Sheets("DMData").Range("strT10") = strT10
 Sheets("DMData").Range("strT11") = strT11
 Sheets("DMData").Range("ImpLayerDepth") = ImpLayerDepth
End Sub

 

Public Sub SoilssDatabase()
'Illinois: soil lines of the .gen file from the county database .dsi file
'(original routine); the .sin/.mis/.wdv paths are set by SetIllinoisDbSoil
'Check the use of the OR STATEMENT later

Dim D(5), DD(5)
Dim k(5)
Dim T1, T2, T3, T4, T5, T6, T7, T8, T9, T10, t11
Dim O1, O2, O3, O4, O5, O6, O7, O8, O9, O10, O11
Dim MSDepth As Single, SSDepth As Single, SDR As Single, Ddepth2 As Single

SetIllinoisDbSoil

MSDepth = Sheets("New Flow File").Range("MSDepth")
SSDepth = Sheets("New Flow File").Range("SSDepth")
Ddepth2 = Sheets("New Flow File").Range("DrainDepth2")

If MSDepth < Ddepth2 Then MSDepth = Int(1.1 * Ddepth2) + 1

Sheets("New Flow File").Range("MSDepth") = MSDepth

SDR = MSDepth / SSDepth

Dim NumberofLayers As Integer

CurPath = Sheets("New Flow File").Range("CurrentPath")
StrSoil = Sheets("New Flow File").Range("SoilFileName")
StateName = Sheets("New Flow File").Range("StateName")

If StateName = "Other" Then StateName = "Illinois"
strSoilRep = CurPath + "\" + StateName + "\" & "\soils\" & StrSoil & ".dsi"

Open strSoilRep For Input As #4

Input #4, ImpLayerDepth, NumberofLayers
For N = 1 To NumberofLayers
    Input #4, D(N), k(N)
    DD(N) = Int(D(N) * SDR) + 1
Next N
Input #4, T1, O1
Input #4, T2, O2
Input #4, T3, O3
Input #4, T4, O4
Input #4, T5, O5
Input #4, T6, O6
Input #4, T7, O7
Input #4, T8, O8
Input #4, T9, O9
Input #4, T10, O10
Input #4, t11, O11
Input #4, wiltpoint, bulkdensity

Close #4

R = 0.51 'Effective Radius of drain tube (default is .51)

strSoils1 = "    " & Int(ImpLayerDepth * SDR) + 1 & "       " & R
strSoils2 = Format(DD(1), " @@@.") & Format(k(1), "@@@@@") & Format(DD(2), " @@@.") & Format(k(2), "@@@@@") _
            & Format(DD(3), " @@@.") & Format(k(3), "@@@@@") & Format(DD(4), " @@@.") & Format(k(4), "@@@@@") _
            & Format(DD(5), " @@@.") & Format(k(5), "@@@@@")

strT1 = "   " & FormatNumber(T1, 1) & Format(O1, "     .000")
strT2 = "  " & FormatNumber(T2, 1) & Format(O2, "     .000")
strT3 = "  " & FormatNumber(T3, 1) & Format(O3, "     .000")
strT4 = "  " & FormatNumber(T4, 1) & Format(O4, "     .000")
strT5 = "  " & FormatNumber(T5, 1) & Format(O5, "     .000")
strT6 = "  " & FormatNumber(T6, 1) & Format(O6, "     .000")
strT7 = "  " & FormatNumber(T7, 1) & Format(O7, "     .000")
strT8 = "  " & FormatNumber(T8, 1) & Format(O8, "     .000")
strT9 = "  " & FormatNumber(T9, 1) & Format(O9, "     .000")
strT10 = "  " & FormatNumber(T10, 1) & Format(O10, "     .000")
strT11 = " " & FormatNumber(t11, 1) & Format(O11, "     .000")

For N = 1 To 5          'clears matrix
    D(N) = 0
    DD(N) = 0
    k(N) = 0
Next N


 Sheets("DMData").Range("strSoils1") = strSoils1
 Sheets("DMData").Range("strSoils2") = strSoils2
 Sheets("DMData").Range("strT1") = strT1
 Sheets("DMData").Range("strT2") = strT2
 Sheets("DMData").Range("strT3") = strT3
 Sheets("DMData").Range("strT4") = strT4
 Sheets("DMData").Range("strT5") = strT5
 Sheets("DMData").Range("strT6") = strT6
 Sheets("DMData").Range("strT7") = strT7
 Sheets("DMData").Range("strT8") = strT8
 Sheets("DMData").Range("strT9") = strT9
 Sheets("DMData").Range("strT10") = strT10
 Sheets("DMData").Range("strT11") = strT11
 Sheets("DMData").Range("ImpLayerDepth") = ImpLayerDepth
End Sub
