{ Convi: generate manufacturing outputs from the project's OutJob.
  Placeholders: %PRJ% %OUTJOB% %DWF% %LIST% %LOG% %DONE% %STARTED%
  %LIST%: one container per line, '<publish|generate><TAB><container name>'; a first line
  'refresh<TAB><name>' publishes the Draftsman container once so Draftsman reads the board, then the
  drawing is saved and closed (a drawing published right after that first update still places the
  drill drawing view by the template board) and every container runs. }

var GLog: TStringList;

procedure Log(S: String);
begin
  GLog.Add(S);
  GLog.SaveToFile('%LOG%');
end;

procedure RunContainer(Kind, Name: String);
var J: Variant;
begin
  // Altium writes nothing for a container whose OutJob is not the active document
  J := Client.OpenDocument('OUTPUTJOB', '%OUTJOB%');
  if J <> Nil then Client.ShowDocument(J);
  ResetParameters;
  AddStringParameter('ObjectKind', 'OutputBatch');
  AddStringParameter('OutputMedium', Name);
  if Kind = 'generate' then
  begin
    AddStringParameter('Action', 'Run');
    RunProcess('WorkspaceManager:GenerateReport');
  end
  else
  begin
    AddStringParameter('Action', 'PublishToPDF');
    AddStringParameter('DisableDialog', 'True');
    RunProcess('WorkspaceManager:Print');
  end;
end;

procedure Main;
var L: TStringList; i, p: Integer; Ln, Kind, Name: String; D: Variant;
begin
  GLog := TStringList.Create;
  L := TStringList.Create;
  L.Add('started');
  L.SaveToFile('%STARTED%');
  try
    ResetParameters;
    AddStringParameter('ObjectKind', 'Project');
    AddStringParameter('FileName', '%PRJ%');
    RunProcess('WorkspaceManager:OpenObject');
    L.LoadFromFile('%LIST%');
    for i := 0 to L.Count - 1 do
    begin
      Ln := L[i];
      p := Pos(#9, Ln);
      if p = 0 then Continue;
      Kind := Copy(Ln, 1, p - 1);
      Name := Copy(Ln, p + 1, Length(Ln));
      Log('START' + #9 + Kind + #9 + Name);
      try
        if Kind = 'refresh' then
        begin
          RunContainer('publish', Name);
          D := Client.GetDocumentByPath('%DWF%');
          if D <> Nil then
          begin
            D.DoFileSave('');
            Client.CloseDocument(D);
            Log('DWF' + #9 + 'saved and closed');
          end
          else
            Log('DWF' + #9 + 'not open');
        end
        else
          RunContainer(Kind, Name);
        Log('END' + #9 + Kind + #9 + Name);
      except
        Log('FAIL' + #9 + Kind + #9 + Name);
      end;
    end;
  except
    Log('FATAL');
  end;
  GLog.Add('done');
  GLog.SaveToFile('%LOG%');
  L.Clear;
  L.Add('done');
  L.SaveToFile('%DONE%');
end;
