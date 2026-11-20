procedure Body;
var
  Doc, Board, T: Variant;
  P, R: String;
begin
  P := '%ROOT%\devtools\_probe_run\p03.PcbDoc';
  Doc := CreateNewDocumentFromDocumentKind('PCB');
  Board := PCBServer.GetCurrentPCBBoard;
  T := PCBServer.PCBObjectFactory(eTrackObject, eNoDimension, eCreate_Default);
  T.X1 := MilsToCoord(1000); T.Y1 := MilsToCoord(1000);
  T.X2 := MilsToCoord(2000); T.Y2 := MilsToCoord(1000);
  T.Width := MilsToCoord(10);
  T.Layer := eTopLayer;
  Board.AddPCBObject(T);
  Doc.SetFileName(P);
  R := Doc.DoFileSave('');
  L('DoFileSave('''') -> ' + VarToStr(R) + ' exists=' + BoolToStr(FileExists(P), True));
  if not FileExists(P) then
  begin
    R := Doc.DoFileSave('PCB Binary');
    L('DoFileSave(PCB Binary) -> ' + VarToStr(R) + ' exists=' + BoolToStr(FileExists(P), True));
  end;
  if not FileExists(P) then
  begin
    ResetParameters;
    AddStringParameter('ObjectKind', 'Document');
    AddStringParameter('FileName', P);
    RunProcess('WorkspaceManager:SaveObject');
    L('SaveObject exists=' + BoolToStr(FileExists(P), True));
  end;
  L('modified=' + BoolToStr(Doc.Modified, True));
  Client.CloseDocument(Doc);
end;
