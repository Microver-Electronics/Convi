procedure Body;
var D, D2: Variant;
begin
  D := Client.GetDocumentByPath('%OUT%\board\board.PcbDoc');
  Client.ShowDocument(D);
  ResetParameters;
  AddStringParameter('ObjectKind', 'Document');
  AddStringParameter('FileName', '%OUT%\board\board.PcbDoc');
  RunProcess('WorkspaceManager:SaveObject');
  L('SaveObject done, modified=' + BoolToStr(D.Modified, True));
end;
