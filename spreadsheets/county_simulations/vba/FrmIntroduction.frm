Attribute VB_Name = "FrmIntroduction"
Attribute VB_Base = "0{4CC2D6D2-74C6-4CD1-8ECB-4DCFE11EBAAE}{14DBC617-F619-45D8-BECB-1BF8EF4E8949}"
Attribute VB_GlobalNameSpace = False
Attribute VB_Creatable = False
Attribute VB_PredeclaredId = True
Attribute VB_Exposed = False
Attribute VB_TemplateDerived = False
Attribute VB_Customizable = False
Private Sub CommandButton1_Click()
 Dim PltFile As String, NewPath As String
 Dim answer As Integer
Worksheets("Main").Range("dBaseFlag") = 1
FrmIntroduction.Hide
Worksheets("New Flow File").InputState ("Illinois")
Worksheets("Main").Activate
Worksheets("Main").Range("StartFlag") = 0
10     NewPath = GetFolder(PltFile)
   
   If NewPath = "" Or Dir(NewPath & "\inputs", vbDirectory) = "" Or Dir(NewPath & "\outputs", vbDirectory) = "" Then
      MsgBox ("The inputs and outputs folders were not found in the selected folder. Select the DRAINMOD folder (it holds the crops, inputs and outputs folders).")
      answer = MsgBox("Do you want to try again?", vbYesNo + vbQuestion, "Retry")
      If answer = vbYes Then
    GoTo 10
Else
 ActiveWorkbook.Close savechanges:=False
End If
      
   End If
    Worksheets("New Flow File").Range("CurrentPath") = NewPath
    Worksheets("Main").Range("StartFlag") = 1
    DMHydroPath True
End Sub




Private Sub CommandButton2_Click()
        Dim IEapp As Object
        Dim WebUrl As String
        
        'Set IEapp = CreateObject("InternetExplorer.Application") 'Set IEapp = InternetExplorer
        WebUrl = "http://www.wq.illinois.edu/dg/banner_guidelines.html"
        
        'You can't really automate the passing of variables (ie. Login Name and Passwords) using Chrome or FireFox, but the below code will open the desired page
        'Shell ("C:\Program Files (x86)\Google\Chrome\Application\chrome.exe -url " & WebUrl)  'Opens URL in Chrome if installed, replace chrome.exe with FireFox
                                                                                                
        Set IEapp = CreateObject("InternetExplorer.Application") 'Set IEapp = InternetExplorer
        With IEapp
            .Silent = True 'No Pop-ups
            .Visible = True 'Set InternetExplorer to Visible
            .Navigate WebUrl 'Load web page

            'Run and Wait, if you intend on passing variables at a later stage
            Do While .Busy
                DoEvents
            Loop
            
            Do While .ReadyState <> 4
                DoEvents
            Loop
        
        End With

End Sub


Private Sub CommandButton3_Click()
FrmIntroduction.Hide
Worksheets("Users Guide").Visible = True
Worksheets("Users Guide").Activate

End Sub
