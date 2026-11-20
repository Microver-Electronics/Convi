procedure Body;
var WS: Variant; i: Integer; Paths: TStringList;
begin
  WS := GetWorkspace;
  Paths := TStringList.Create;
  for i := 0 to WS.DM_ProjectCount - 1 do
    if Pos('%WS%\', WS.DM_Projects(i).DM_ProjectFullPath) = 1 then Paths.Add(WS.DM_Projects(i).DM_ProjectFullPath);
  for i := 0 to Paths.Count - 1 do
  begin
    ResetParameters;
    AddStringParameter('ObjectKind', 'Project');
    AddStringParameter('FileName', Paths[i]);
    RunProcess('WorkspaceManager:CloseObject');
    L('closed ' + Paths[i]);
  end;
end;
