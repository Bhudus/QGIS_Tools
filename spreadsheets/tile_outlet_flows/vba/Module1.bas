Attribute VB_Name = "Module1"
Option Explicit

' Tile Outlet Submergence: Flow for Stream Depths
' Richard Cooke - https://github.com/Bhudus/QGIS_Tools
'
' ImportRatingCurve reads the rating-curve CSV written by the QGIS plugin
' Tile Outlet Submergence (its "Rating curve as CSV" output, or the Export CSV
' button in its report) into the Rating sheet. The Flows sheet works out the
' flow for each stream depth with worksheet formulas, so the macros are only
' needed to import a CSV and to clear the stream depths.

Private Const RATING_SHEET As String = "Rating"
Private Const FLOWS_SHEET As String = "Flows"
Private Const HEADER_ROW As Long = 3      ' CSV header row on the Rating sheet
Private Const LAST_ROW As Long = 502      ' last row the rating table may use
Private Const NCOLS As Long = 10          ' columns in the plugin's CSV
Private Const FIRST_DEPTH_ROW As Long = 11
Private Const LAST_DEPTH_ROW As Long = 5010
Private Const LEVEL_HEADER As String = "level_above_invert_"

Public Sub ImportRatingCurve()
    Dim f As Variant, msg As String
    f = Application.GetOpenFilename("CSV files (*.csv),*.csv", , _
        "Rating curve CSV from Tile Outlet Submergence")
    If VarType(f) = vbBoolean Then Exit Sub
    msg = LoadRatingCsv(CStr(f))
    If Left$(msg, 8) = "Imported" Then
        MsgBox msg, vbInformation, "Import rating curve"
    Else
        MsgBox msg, vbExclamation, "Import rating curve"
    End If
End Sub

' Reads a rating-curve CSV into the Rating sheet. Returns a message that starts
' with "Imported" when it worked.
Public Function LoadRatingCsv(ByVal path As String) As String
    Dim fnum As Integer, txt As String
    Dim lines() As String, rows As Collection, ln As Variant
    Dim parts() As String, r As Long, c As Long, n As Long
    Dim ws As Worksheet, v As String, prev As Double, fname As String
    Dim bytes() As Byte

    On Error GoTo Failed
    fname = Mid$(path, InStrRev(Replace(path, "/", "\"), "\") + 1)
    fnum = FreeFile
    Open path For Binary Access Read As #fnum
    If LOF(fnum) = 0 Then
        Close #fnum
        LoadRatingCsv = "The file is empty."
        Exit Function
    End If
    ReDim bytes(0 To LOF(fnum) - 1)
    Get #fnum, , bytes
    Close #fnum
    fnum = 0
    txt = StrConv(bytes, vbUnicode)
    ' skip a byte-order mark (the plugin writes one so Excel opens the CSV as UTF-8)
    r = InStr(1, txt, LEVEL_HEADER)
    If r > 1 And r <= 4 Then txt = Mid$(txt, r)

    Set rows = New Collection
    lines = Split(Replace(txt, vbCr, ""), vbLf)
    For Each ln In lines
        If Len(Trim$(ln)) > 0 Then rows.Add CStr(ln)
    Next ln

    If rows.Count < 3 Then
        LoadRatingCsv = "This is not a rating-curve CSV from Tile Outlet Submergence: " & _
                        "it has fewer than three rows."
        Exit Function
    End If
    If Left$(rows(1), Len(LEVEL_HEADER)) <> LEVEL_HEADER Then
        LoadRatingCsv = "This is not a rating-curve CSV from Tile Outlet Submergence: " & _
                        "its first column should be " & LEVEL_HEADER & "ft (or _m)."
        Exit Function
    End If
    If rows.Count - 1 > LAST_ROW - HEADER_ROW Then
        LoadRatingCsv = "The rating curve has more rows than the Rating sheet can hold (" & _
                        (LAST_ROW - HEADER_ROW) & ")."
        Exit Function
    End If

    Set ws = ThisWorkbook.Worksheets(RATING_SHEET)
    Application.ScreenUpdating = False
    ws.Range(ws.Cells(HEADER_ROW, 1), ws.Cells(LAST_ROW, NCOLS)).ClearContents
    For r = 1 To rows.Count
        parts = Split(rows(r), ",")
        For c = 0 To UBound(parts)
            If c >= NCOLS Then Exit For
            v = Trim$(parts(c))
            If r = 1 Or v = "free" Then
                ws.Cells(HEADER_ROW + r - 1, c + 1).Value = v
            ElseIf Len(v) > 0 Then
                ' Val always reads a full stop as the decimal point, as the CSV is written
                ws.Cells(HEADER_ROW + r - 1, c + 1).Value = Val(v)
            End If
        Next c
    Next r

    ' the ditch levels must rise down the table for the interpolation
    n = 0
    For r = HEADER_ROW + 2 To HEADER_ROW + rows.Count - 1
        If n > 0 And ws.Cells(r, 1).Value <= prev Then
            Application.ScreenUpdating = True
            LoadRatingCsv = "The ditch levels in this CSV do not rise from row to row, " & _
                            "so the flows cannot be interpolated."
            Exit Function
        End If
        prev = ws.Cells(r, 1).Value
        n = n + 1
    Next r

    ws.Range("A2").Value = "Source: " & fname & ", imported " & Format$(Now, "yyyy-mm-dd hh:mm")
    Application.Calculate
    Application.ScreenUpdating = True
    LoadRatingCsv = "Imported the free outlet and " & n & " ditch levels from " & fname & "."
    Exit Function

Failed:
    If fnum <> 0 Then Close #fnum
    Application.ScreenUpdating = True
    LoadRatingCsv = "Could not import the file: " & Err.Description
End Function

Public Sub ClearStreamDepths()
    Dim ws As Worksheet
    If MsgBox("Clear all the dates and stream depths on the Flows sheet?", _
              vbYesNo + vbQuestion, "Clear stream depths") <> vbYes Then Exit Sub
    Set ws = ThisWorkbook.Worksheets(FLOWS_SHEET)
    ' column A: dates or numbers; column B: stream depths
    ws.Range(ws.Cells(FIRST_DEPTH_ROW, 1), ws.Cells(LAST_DEPTH_ROW, 2)).ClearContents
End Sub
