{ Convi: open the converted project in Altium. Placeholders: %PRJ% %DONE% %STARTED% }
procedure Main;
var Done: TStringList;
begin
  Done := TStringList.Create;
  Done.Add('started');
  Done.SaveToFile('%STARTED%');
  try
    ResetParameters;
    AddStringParameter('ObjectKind', 'Project');
    AddStringParameter('FileName', '%PRJ%');
    RunProcess('WorkspaceManager:OpenObject');
  except
  end;
  Done.SaveToFile('%DONE%');
end;
