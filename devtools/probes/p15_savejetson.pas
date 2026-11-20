procedure Body;
var D: Variant;
begin
  D := Client.GetDocumentByPath('%OUT%\board\board.PcbDoc');
  if D = Nil then begin L('not open'); Exit; end;
  D.DoFileSave('');
  L('saved, modified=' + BoolToStr(D.Modified, True));
end;
