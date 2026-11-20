procedure Body;
var D, B, It, R: Variant;
begin
  D := Client.OpenDocument('PCB', '%OUT%\batch\board\board.PcbDoc');
  B := PCBServer.GetPCBBoardByPath('%OUT%\batch\board\board.PcbDoc');
  It := B.BoardIterator_Create;
  It.AddFilter_ObjectSet(MkSet(eRuleObject));
  It.AddFilter_LayerSet(AllLayers);
  It.AddFilter_Method(eProcessAll);
  R := It.FirstPCBObject;
  while R <> Nil do
  begin
    if R.RuleKind = eRule_PolygonConnectStyle then
      L(R.Name + ' prio=' + IntToStr(R.Priority) + ' enabled=' + BoolToStr(R.Enabled, True) + ' style=' + IntToStr(R.ConnectStyle) +
        ' scope1=' + R.Scope1Expression + ' scope2=' + R.Scope2Expression);
    R := It.NextPCBObject;
  end;
  B.BoardIterator_Destroy(It);
end;
