procedure Body;
var B, It, R: Variant;
begin
  B := PCBServer.GetPCBBoardByPath('%OUT%\ecc83-pp\ecc83-pp.PcbDoc');
  if B = Nil then B := PCBServer.GetCurrentPCBBoard;
  It := B.BoardIterator_Create;
  It.AddFilter_ObjectSet(MkSet(eRuleObject));
  It.AddFilter_LayerSet(AllLayers);
  It.AddFilter_Method(eProcessAll);
  R := It.FirstPCBObject;
  while R <> Nil do
  begin
    if (R.RuleKind = eRule_Clearance) or (R.RuleKind = eRule_PolygonConnectStyle) then
      L(IntToStr(R.RuleKind) + ' prio=' + IntToStr(R.Priority) + ' ' + R.Name + ' | ' + R.Scope1Expression + ' | ' + R.Scope2Expression);
    R := It.NextPCBObject;
  end;
  B.BoardIterator_Destroy(It);
end;
