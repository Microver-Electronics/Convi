{ Convi: close every open project and document inside the output folder before rewriting it.
  Placeholders: %DIR% %DONE% %STARTED% }
procedure Main;
var WS, P: Variant; i: Integer; Paths, Done: TStringList; D: Variant;
begin
  Done := TStringList.Create;
  Done.Add('started');
  Done.SaveToFile('%STARTED%');
  try
    WS := GetWorkspace;
    Paths := TStringList.Create;
    for i := 0 to WS.DM_ProjectCount - 1 do
      if Pos(AnsiUpperCase('%DIR%'), AnsiUpperCase(WS.DM_Projects(i).DM_ProjectFullPath)) = 1 then
        Paths.Add(WS.DM_Projects(i).DM_ProjectFullPath);
    for i := 0 to Paths.Count - 1 do
    begin
      ResetParameters;
      AddStringParameter('ObjectKind', 'Project');
      AddStringParameter('FileName', Paths[i]);
      RunProcess('WorkspaceManager:CloseObject');
    end;
  except
  end;
  Done.SaveToFile('%DONE%');
end;
