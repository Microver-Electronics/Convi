procedure Body;
var
  Doc, Board, T: Variant;
  P: String;
begin
  Doc := Client.CurrentView.OwnerDocument;
  L('current: ' + Doc.FileName + ' kind=' + Doc.Kind);
  P := '%ROOT%\devtools\_probe_run\p02.PcbDoc';
  Doc.DoSafeChangeFileNameAndSave(P, 'PCB');
  L('saved1 exists=' + BoolToStr(FileExists(P), True));
  L('now: ' + Doc.FileName);
  Client.CloseDocument(Doc);
  L('closed');
end;
